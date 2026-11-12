import os

from django.db import IntegrityError, connection, models, transaction
from django.db.models import Count, Q
from django.utils import timezone
from openai import OpenAI
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from .models import (
    User, OTP, Workspace, Project, ProjectMember,
    Contact, ContactRequest, Task, Device,
)
from .serializers import (
    SendOTPSerializer, VerifyOTPSerializer, ProfileSerializer,
    ProfileUpdateSerializer, NicknameCheckSerializer,
    WorkspaceSerializer, ProjectSerializer, ProjectCreateSerializer,
    ProjectMemberSerializer,
    UserPublicSerializer, ContactSerializer, ContactRequestSerializer,
    SendContactRequestSerializer, AddProjectMemberSerializer,
    TaskSerializer, TaskCreateSerializer, TaskStatusUpdateSerializer,
    TaskUpdateSerializer, DeviceSerializer, GoogleLoginSerializer, get_tokens_for_user,
)
from . import notifications
from .google_auth import GoogleAuthError, GoogleNotConfigured, verify_google_id_token
from .permissions import (
    IsProjectLeader, IsTaskAssigneeOrLeader, enforce, is_leader, is_member,
)
from .throttles import (
    GoogleLoginThrottle, OTPSendIPThrottle, OTPSendPhoneThrottle, OTPVerifyThrottle,
)
from .utils import generate_otp_code, hash_otp, send_sms


# Auth

class SendOTPApiView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OTPSendPhoneThrottle, OTPSendIPThrottle]

    def post(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone_number"]

        code = generate_otp_code()
        with transaction.atomic():
            # Eski OTP larni bekor qilish
            OTP.objects.filter(phone_number=phone, is_used=False).update(is_used=True)
            OTP.objects.create(phone_number=phone, code=hash_otp(phone, code))

        message = f"Miki Todo kodingiz: {code}"
        if not send_sms(phone, message):
            return Response(
                {"detail": "SMS yuborib bo'lmadi. Keyinroq urinib ko'ring."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"message": "OTP yuborildi.", "detail": "OTP yuborildi.", "phone_number": phone})


class VerifyOTPApiView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OTPVerifyThrottle]

    INVALID = {"detail": "OTP noto'g'ri yoki muddati o'tgan."}

    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        phone = data["phone_number"]
        code = data["code"]

        with transaction.atomic():
            # Eng oxirgi faol OTP; qatorni qulflaymiz — parallel urinishlar attempts'ni chetlab o'tolmaydi.
            otp = (
                OTP.objects.select_for_update()
                .filter(phone_number=phone, is_used=False)
                .order_by("-created_at")
                .first()
            )
            if not otp or not otp.is_valid():
                return Response(self.INVALID, status=status.HTTP_400_BAD_REQUEST)

            if not otp.matches(code):
                otp.attempts += 1
                if otp.attempts >= OTP.MAX_ATTEMPTS:
                    otp.is_used = True  # limit tugadi — yangi OTP so'rash kerak
                otp.save(update_fields=["attempts", "is_used"])
                return Response(self.INVALID, status=status.HTTP_400_BAD_REQUEST)

            user, created = User.objects.get_or_create(phone_number=phone)

            # Yangi foydalanuvchi uchun qo'shimcha ma'lumotlar.
            # Nickname band bo'lsa OTP sarflanmaydi — foydalanuvchi boshqa nickname bilan qayta urinadi.
            if created or not user.nickname:
                nickname = data.get("nickname")
                if nickname:
                    if User.objects.filter(nickname__iexact=nickname).exclude(pk=user.pk).exists():
                        transaction.set_rollback(True)  # get_or_create ham bekor bo'ladi
                        return Response(
                            {"detail": "Bu nickname band."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    user.nickname = nickname
                if data.get("first_name"):
                    user.first_name = data["first_name"]
                if data.get("last_name"):
                    user.last_name = data["last_name"]
                user.save()

            otp.is_used = True
            otp.save(update_fields=["is_used"])

            if not user.is_active:
                return Response(
                    {"detail": "Bu akkaunt faol emas."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        tokens = get_tokens_for_user(user)
        return Response(
            {
                "user": ProfileSerializer(user).data,
                "tokens": tokens,
                "is_new_user": created,
            }
        )


class GoogleLoginApiView(APIView):
    """
    POST /auth/google/  {"id_token": "..."}
    Javob verify-otp bilan bir xil: {"user", "tokens", "is_new_user"}.
    Foydalanuvchi Google `sub` bo'yicha topiladi (email o'zgarsa ham bir xil akkaunt).
    Mavjud OTP-akkauntlar avtomatik BIRLASHTIRILMAYDI (email egaligi telefon egaligini isbotlamaydi).
    """
    permission_classes = [AllowAny]
    throttle_classes = [GoogleLoginThrottle]

    def post(self, request):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            info = verify_google_id_token(serializer.validated_data["id_token"])
        except GoogleNotConfigured:
            return Response(
                {"detail": "Google orqali kirish serverda sozlanmagan."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except GoogleAuthError:
            return Response(
                {"detail": "Google token yaroqsiz."}, status=status.HTTP_401_UNAUTHORIZED
            )

        sub = info["sub"]
        created = False
        try:
            with transaction.atomic():
                user = User.objects.filter(google_sub=sub).first()
                if user is None:
                    user = User(
                        google_sub=sub,
                        email=info.get("email", ""),
                        first_name=(info.get("given_name") or "")[:150],
                        last_name=(info.get("family_name") or "")[:150],
                    )
                    user.set_unusable_password()
                    user.save()
                    created = True
        except IntegrityError:  # parallel birinchi kirish — boshqa so'rov yaratib bo'ldi
            user = User.objects.get(google_sub=sub)

        if not user.is_active:
            return Response(status=status.HTTP_403_FORBIDDEN)

        return Response(
            {
                "user": ProfileSerializer(user).data,
                "tokens": get_tokens_for_user(user),
                "is_new_user": created,
            }
        )


class CheckNicknameApiView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OTPVerifyThrottle]  # prevent scanning

    def post(self, request):
        serializer = NicknameCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nickname = serializer.validated_data["nickname"]
        available = not User.objects.filter(nickname__iexact=nickname).exists()
        return Response({"nickname": nickname, "available": available})


class ProfileApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(ProfileSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileUpdateSerializer(
            request.user, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProfileSerializer(request.user).data)


# Workspace

class WorkspaceApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Foydalanuvchi egasi bo'lgan yoki a'zo bo'lgan workspacelar."""
        owned = Workspace.objects.filter(owner=request.user)
        member_ws_ids = ProjectMember.objects.filter(
            user=request.user
        ).values_list("project__workspace_id", flat=True)
        member_ws = Workspace.objects.filter(id__in=member_ws_ids)
        qs = (owned | member_ws).distinct().annotate(
            projects_count_ann=Count("projects", distinct=True)
        )
        return Response(WorkspaceSerializer(qs, many=True).data)

    def post(self, request):
        """Yangi Workspace yaratish (Lider)."""
        raw_name = request.data.get("name")
        name = (raw_name or "").strip() if isinstance(raw_name, str) else ""
        if not name:
            return Response(
                {"detail": "name majburiy."}, status=status.HTTP_400_BAD_REQUEST
            )
        if len(name) > 255:
            return Response(
                {"detail": "name juda uzun (max 255)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ws = Workspace.objects.create(name=name, owner=request.user)
        return Response(
            WorkspaceSerializer(ws).data, status=status.HTTP_201_CREATED
        )

    def delete(self, request, pk=None):
        try:
            ws = Workspace.objects.get(pk=pk, owner=request.user)
        except Workspace.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        ws.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# Project

class ProjectApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        if pk:
            project = self._get_accessible_project(request.user, pk)
            if not project:
                return Response(status=status.HTTP_404_NOT_FOUND)
            return Response(
                ProjectSerializer(project, context={"request": request}).data
            )

        # Barcha ochiq loyihalar (egasi yoki a'zo) + N+1 kamaytirish
        owned = Project.objects.filter(owner=request.user)
        member = Project.objects.filter(members__user=request.user)
        qs = (
            (owned | member)
            .distinct()
            .select_related("workspace", "owner")
            .annotate(members_count_ann=Count("members", distinct=True))
        )
        return Response(
            ProjectSerializer(qs, many=True, context={"request": request}).data
        )

    def post(self, request):
        """Yangi Project yaratish + avtomatik Leader qilib qo'shish."""
        serializer = ProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            workspace = Workspace.objects.get(
                pk=data["workspace_id"], owner=request.user
            )
        except Workspace.DoesNotExist:
            return Response(
                {"detail": "Workspace topilmadi yoki siz egasi emassiz."},
                status=status.HTTP_404_NOT_FOUND,
            )

        project = Project.objects.create(
            workspace=workspace,
            name=data["name"],
            description=data.get("description", ""),
            owner=request.user,
        )
        ProjectMember.objects.create(
            project=project, user=request.user, role=ProjectMember.ROLE_LEADER
        )
        return Response(
            ProjectSerializer(project, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request, pk=None):
        project = self._get_leader_project(request.user, pk)
        if not project:
            return Response(status=status.HTTP_403_FORBIDDEN)

        if "name" in request.data:
            raw = request.data["name"]
            name = (raw or "").strip() if isinstance(raw, str) else ""
            if not name:
                return Response(
                    {"detail": "name bo'sh bo'lishi mumkin emas."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if len(name) > 255:
                return Response(
                    {"detail": "name juda uzun (max 255)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            project.name = name
        if "description" in request.data:
            desc = request.data["description"]
            project.description = (desc or "") if isinstance(desc, str) else ""
        project.save()
        return Response(
            ProjectSerializer(project, context={"request": request}).data
        )

    def delete(self, request, pk=None):
        project = self._get_leader_project(request.user, pk)
        if not project:
            return Response(status=status.HTTP_403_FORBIDDEN)
        project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_accessible_project(self, user, pk):
        project = Project.objects.filter(pk=pk).first()
        return project if project and is_member(user, project) else None

    def _get_leader_project(self, user, pk):
        project = Project.objects.filter(pk=pk).first()
        return project if project and is_leader(user, project) else None


class ProjectMembersApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = Project.objects.filter(
            Q(owner=request.user) | Q(members__user=request.user), pk=project_id
        ).distinct().first()
        if not project:
            return Response(status=status.HTTP_404_NOT_FOUND)
        members = project.members.select_related("user").all()
        return Response(ProjectMemberSerializer(members, many=True).data)


class ProjectDashboardApiView(APIView):
    """
    Lider uchun progress monitoring (TZ 3.3). Faqat lider ko'radi.
    A'zolar bo'yicha statistika bitta agregatsiya so'rovida (N+1 yo'q).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = Project.objects.filter(pk=project_id).first()
        if not project or not is_member(request.user, project):
            return Response(status=status.HTTP_404_NOT_FOUND)
        enforce(IsProjectLeader, request, self, project)

        counts = dict(
            project.tasks.values_list("status").annotate(c=Count("id")).order_by()
        )
        by_status = {
            Task.STATUS_TODO: counts.get(Task.STATUS_TODO, 0),
            Task.STATUS_IN_PROGRESS: counts.get(Task.STATUS_IN_PROGRESS, 0),
            Task.STATUS_DONE: counts.get(Task.STATUS_DONE, 0),
        }
        total = sum(by_status.values())
        progress = int(round(by_status[Task.STATUS_DONE] * 100 / total)) if total else 0

        in_project = Q(user__assigned_tasks__project=project)
        members = project.members.select_related("user").annotate(
            total_assigned=Count("user__assigned_tasks", filter=in_project),
            done=Count(
                "user__assigned_tasks",
                filter=in_project & Q(user__assigned_tasks__status=Task.STATUS_DONE),
            ),
            in_progress=Count(
                "user__assigned_tasks",
                filter=in_project & Q(user__assigned_tasks__status=Task.STATUS_IN_PROGRESS),
            ),
        )
        members_stats = [
            {
                "user": ProfileSerializer(m.user, context={"request": request}).data,
                "role": m.role,
                "total_assigned": m.total_assigned,
                "done": m.done,
                "in_progress": m.in_progress,
            }
            for m in members
        ]

        return Response(
            {
                "project_id": project.id,
                "project_name": project.name,
                "total_tasks": total,
                "by_status": by_status,
                "progress_percent": progress,
                "members": members_stats,
            }
        )



# Contacts

def are_contacts(user_a, user_b) -> bool:
    """Ikkala foydalanuvchi bir-birining kontaktidami?"""
    return Contact.objects.filter(user=user_a, contact_user=user_b).exists()


def make_mutual_contacts(user_a, user_b):
    """Ikkala tomon uchun Contact yozuvini yaratadi (agar yo'q bo'lsa)."""
    Contact.objects.get_or_create(user=user_a, contact_user=user_b)
    Contact.objects.get_or_create(user=user_b, contact_user=user_a)


class UserSearchApiView(APIView):
    """
    GET /api/v1/users/search/?q=nickname
    Nickname bo'yicha qidirish (o'zini chiqarib tashlaydi).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [OTPVerifyThrottle]

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response(
                {"detail": "Kamida 2 ta belgi kiriting."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        users = (
            User.objects.filter(nickname__icontains=q)
            .exclude(pk=request.user.pk)
            .order_by("nickname")[:20]
        )

        contact_ids = set(
            Contact.objects.filter(user=request.user, contact_user__in=users)
            .values_list("contact_user_id", flat=True)
        )

        results = []
        for u in users:
            item = UserPublicSerializer(u).data
            item["is_contact"] = u.id in contact_ids
            results.append(item)

        return Response(results)


class ContactListApiView(APIView):
    """GET /api/v1/contacts/ — mening kontaktlarim."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            Contact.objects.filter(user=request.user)
            .select_related("contact_user")
            .order_by("-created_at")
        )
        return Response(ContactSerializer(qs, many=True).data)


class ContactRequestApiView(APIView):
    """
    POST /api/v1/contacts/request/  — zapros yuborish
    GET  /api/v1/contacts/requests/ — menga kelgan zaproslar
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Muddati o'tganlarni avtomatik expired qilish
        ContactRequest.objects.filter(
            to_user=request.user,
            status=ContactRequest.STATUS_PENDING,
            expires_at__lte=timezone.now(),
        ).update(status=ContactRequest.STATUS_EXPIRED)

        qs = (
            ContactRequest.objects.filter(
                to_user=request.user,
                status=ContactRequest.STATUS_PENDING,
            )
            .select_related("from_user", "to_user", "project")
            .order_by("-created_at")
        )
        return Response(ContactRequestSerializer(qs, many=True).data)

    def post(self, request):
        ser = SendContactRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        to_user_id = ser.validated_data["to_user_id"]
        project_id = ser.validated_data.get("project_id")

        if to_user_id == request.user.id:
            return Response(
                {"detail": "O'zingizga zapros yubora olmaysiz."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        to_user = User.objects.filter(pk=to_user_id).first()
        if not to_user:
            return Response(
                {"detail": "Foydalanuvchi topilmadi."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if are_contacts(request.user, to_user):
            return Response(
                {"detail": "Bu foydalanuvchi allaqachon kontaktingizda."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Bir tomonlama yoki teskari pending so'rovni ham bloklaymiz
        existing = ContactRequest.objects.filter(
            status=ContactRequest.STATUS_PENDING,
            expires_at__gt=timezone.now(),
        ).filter(
            models.Q(from_user=request.user, to_user=to_user)
            | models.Q(from_user=to_user, to_user=request.user)
        ).first()
        if existing:
            return Response(
                {"detail": "Bu foydalanuvchi bilan allaqachon kutilayotgan zapros bor."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project = None
        if project_id:
            project = Project.objects.filter(pk=project_id).first()
            if not project or not is_member(request.user, project):
                return Response(
                    {"detail": "Loyiha topilmadi yoki ruxsatingiz yo'q."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if not is_leader(request.user, project):
                return Response(
                    {"detail": "Faqat loyiha lideri a'zo qo'sha oladi."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        with transaction.atomic():
            # Race: parallel so'rovlar
            if ContactRequest.objects.filter(
                status=ContactRequest.STATUS_PENDING,
                expires_at__gt=timezone.now(),
            ).filter(
                models.Q(from_user=request.user, to_user=to_user)
                | models.Q(from_user=to_user, to_user=request.user)
            ).exists():
                return Response(
                    {"detail": "Bu foydalanuvchi bilan allaqachon kutilayotgan zapros bor."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            req = ContactRequest.objects.create(
                from_user=request.user,
                to_user=to_user,
                project=project,
            )
        notifications.contact_request_sent(req)
        return Response(
            ContactRequestSerializer(req).data,
            status=status.HTTP_201_CREATED,
        )


class ContactRequestRespondApiView(APIView):
    """
    POST /api/v1/contacts/requests/<id>/accept/
    POST /api/v1/contacts/requests/<id>/reject/
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        with transaction.atomic():
            req = (
                ContactRequest.objects.select_for_update()
                .select_related("from_user", "to_user", "project")
                .filter(pk=pk, to_user=request.user)
                .first()
            )
            if not req:
                return Response(
                    {"detail": "Zapros topilmadi."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if req.status != ContactRequest.STATUS_PENDING:
                return Response(
                    {"detail": f"Bu zapros allaqachon {req.status}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if req.is_expired:
                req.status = ContactRequest.STATUS_EXPIRED
                req.save(update_fields=["status"])
                return Response(
                    {"detail": "Zapros muddati o'tgan (12 soat)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            now = timezone.now()

            if action == "reject":
                req.status = ContactRequest.STATUS_REJECTED
                req.responded_at = now
                req.save(update_fields=["status", "responded_at"])
                return Response({"detail": "Zapros rad etildi."})

            if action == "accept":
                # from_user hali leader ekanligini qayta tekshirish (project bo'lsa)
                if req.project_id:
                    if not is_leader(req.from_user, req.project):
                        return Response(
                            {"detail": "Yuboruvchi endi loyiha lideri emas."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    ProjectMember.objects.get_or_create(
                        project=req.project,
                        user=req.to_user,
                        defaults={"role": ProjectMember.ROLE_MEMBER},
                    )

                make_mutual_contacts(req.from_user, req.to_user)

                req.status = ContactRequest.STATUS_ACCEPTED
                req.responded_at = now
                req.save(update_fields=["status", "responded_at"])

                # notification outside lock is better, but keep simple
                if req.project_id:
                    notifications.member_joined(req.project, req.to_user, req.from_user)
                notifications.contact_request_accepted(req)

                return Response({
                    "detail": "Zapros qabul qilindi. Endi kontaktingizdasiz.",
                    "contact_user": UserPublicSerializer(req.from_user).data,
                    "project_id": req.project_id,
                })

        return Response(
            {"detail": "Noto'g'ri action."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class ProjectAddMemberApiView(APIView):
    """
    POST /api/v1/projects/<project_id>/members/add/
    Faqat ALLAQACHON kontaktidagi odamni loyihaga qo'shadi.
    Body: {"user_id": 123}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id):
        ser = AddProjectMemberSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user_id = ser.validated_data["user_id"]

        project = Project.objects.filter(pk=project_id).first()
        if not project or not is_member(request.user, project):
            return Response(
                {"detail": "Loyiha topilmadi."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not is_leader(request.user, project):
            return Response(
                {"detail": "Faqat loyiha lideri a'zo qo'sha oladi."},
                status=status.HTTP_403_FORBIDDEN,
            )

        target = User.objects.filter(pk=user_id).first()
        if not target:
            return Response(
                {"detail": "Foydalanuvchi topilmadi."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not are_contacts(request.user, target):
            return Response(
                {"detail": "Bu foydalanuvchi kontaktingizda emas. Avval zapros yuboring."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if is_member(target, project):
            return Response(
                {"detail": "Bu foydalanuvchi allaqachon loyihada."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        member = ProjectMember.objects.create(
            project=project,
            user=target,
            role=ProjectMember.ROLE_MEMBER,
        )
        notifications.member_joined(project, target, request.user)

        return Response(
            ProjectMemberSerializer(member).data,
            status=status.HTTP_201_CREATED,
        )


#  Tasks 

def _resolve_assignee(project, assignee_id):
    """assignee_id -> User yoki None. Loyiha a'zosi bo'lmasa 400."""
    if assignee_id is None:
        return None
    user = User.objects.filter(pk=assignee_id).first()
    if not user or not is_member(user, project):
        raise ValidationError({"assignee_id": "Ijrochi loyiha a'zosi emas."})
    return user


def _apply_status(task, new_status):
    task.status = new_status
    task.completed_at = timezone.now() if new_status == Task.STATUS_DONE else None


class TaskApiView(APIView):
    permission_classes = [IsAuthenticated]
    LEADER_FIELDS = {"title", "description", "status", "priority", "deadline", "assignee_id"}
    ASSIGNEE_FIELDS = {"status", "description"}

    def get(self, request, pk=None):
        if pk:
            task = self._get_task(request.user, pk)
            if not task:
                return Response(status=status.HTTP_404_NOT_FOUND)
            return Response(TaskSerializer(task).data)

        project_id = request.query_params.get("project_id")
        status_filter = request.query_params.get("status")
        assignee_me = request.query_params.get("assignee") == "me"

        qs = Task.objects.filter(
            Q(project__owner=request.user) | Q(project__members__user=request.user)
        ).distinct().select_related("assignee", "created_by", "project")

        if project_id is not None:
            try:
                pid = int(project_id)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "project_id butun son bo'lishi kerak."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(project_id=pid)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if assignee_me:
            qs = qs.filter(assignee=request.user)

        return Response(TaskSerializer(qs, many=True).data)

    def post(self, request):
        serializer = TaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        project = Project.objects.filter(pk=data["project_id"]).first()
        if not project or not is_member(request.user, project):
            return Response(
                {"detail": "Loyiha topilmadi."}, status=status.HTTP_404_NOT_FOUND
            )

        assignee_id = data.get("assignee_id")
        if not is_leader(request.user, project):
            # A'zo faqat o'ziga vazifa yaratadi (masalan, ovozli zametka)
            if assignee_id not in (None, request.user.id):
                return Response(
                    {"detail": "Boshqalarga vazifa biriktirish faqat lider uchun."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            assignee_id = request.user.id

        task = Task.objects.create(
            project=project,
            title=data["title"],
            description=data.get("description", ""),
            priority=data.get("priority", Task.PRIORITY_MEDIUM),
            deadline=data.get("deadline"),
            assignee=_resolve_assignee(project, assignee_id),
            created_by=request.user,
        )
        notifications.task_assigned(task, request.user)
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)

    def patch(self, request, pk=None):
        task = self._get_task(request.user, pk)
        if not task:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if is_leader(request.user, task.project):
            allowed = self.LEADER_FIELDS
        else:
            enforce(IsTaskAssigneeOrLeader, request, self, task)
            allowed = self.ASSIGNEE_FIELDS

        forbidden = (set(request.data.keys()) & self.LEADER_FIELDS) - allowed
        if forbidden:
            return Response(
                {"detail": f"Bu maydonlarni o'zgartirishga ruxsat yo'q: {', '.join(sorted(forbidden))}."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TaskUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        old_assignee_id, was_done = task.assignee_id, task.status == Task.STATUS_DONE

        with transaction.atomic():
            for field in ("title", "description", "priority", "deadline"):
                if field in data:
                    setattr(task, field, data[field])
            if "deadline" in data:
                task.deadline_reminded_at = None  
            if "status" in data:
                _apply_status(task, data["status"])
            if "assignee_id" in data:
                task.assignee = _resolve_assignee(task.project, data["assignee_id"])
            task.save()

            if task.assignee_id != old_assignee_id:
                notifications.task_assigned(task, request.user)
            if task.status == Task.STATUS_DONE and not was_done:
                notifications.task_done(task, request.user)
        return Response(TaskSerializer(task).data)

    def delete(self, request, pk=None):
        task = self._get_task(request.user, pk)
        if not task:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not is_leader(request.user, task.project) and task.created_by_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_task(self, user, pk):
        task = (
            Task.objects.select_related("assignee", "created_by", "project")
            .filter(pk=pk)
            .first()
        )
        return task if task and is_member(user, task.project) else None


class TaskStatusApiView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        task = (
            Task.objects.select_related("assignee", "created_by", "project")
            .filter(pk=pk)
            .first()
        )
        if not task or not is_member(request.user, task.project):
            return Response(status=status.HTTP_404_NOT_FOUND)
        enforce(IsTaskAssigneeOrLeader, request, self, task)

        serializer = TaskStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        was_done = task.status == Task.STATUS_DONE
        _apply_status(task, serializer.validated_data["status"])
        task.save(update_fields=["status", "completed_at", "updated_at"])
        if task.status == Task.STATUS_DONE and not was_done:
            notifications.task_done(task, request.user)
        return Response(TaskSerializer(task).data)




class DeviceApiView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_token = request.data.get("fcm_token")
        token = (raw_token or "").strip() if isinstance(raw_token, str) else ""
        if not token:
            return Response(
                {"detail": "fcm_token majburiy."}, status=status.HTTP_400_BAD_REQUEST
            )
        if len(token) > 512:
            return Response(
                {"detail": "fcm_token juda uzun (max 512)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw_name = request.data.get("device_name", "")
        device_name = (raw_name or "")[:100] if isinstance(raw_name, str) else ""
        device, _ = Device.objects.update_or_create(
            fcm_token=token,
            defaults={
                "user": request.user,
                "device_name": device_name,
            },
        )
        return Response(DeviceSerializer(device).data, status=status.HTTP_201_CREATED)

    def delete(self, request):
        token = request.data.get("fcm_token", "")
        Device.objects.filter(user=request.user, fcm_token=token).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



class SpeechTranscribeApiView(APIView):
    """
    Audio -> matn (Whisper).
    Himoya: max 10 MB, throttle, timeout, xato matni yashiriladi.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [OTPVerifyThrottle]  # reuse existing IP throttle roughly 10/min

    MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB

    def post(self, request):
        audio = request.FILES.get("audio")
        if not audio:
            return Response(
                {"detail": "audio fayl majburiy."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if audio.size > self.MAX_AUDIO_BYTES:
            return Response(
                {"detail": "Audio fayl 10 MB dan katta bo'lmasligi kerak."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        language = request.data.get("language") or getattr(request.user, "language", None) or "uz"
        lang_map = {"uz": "uz", "ru": "ru", "en": "en"}
        whisper_lang = lang_map.get(str(language).lower()[:5], "uz")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return Response(
                {"detail": "Speech service sozlanmagan."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, timeout=30.0)
            kwargs = {
                "model": "whisper-1",
                "file": audio,
            }
            # uz uchun language bermaymiz (audit)
            if whisper_lang != "uz":
                kwargs["language"] = whisper_lang
            transcription = client.audio.transcriptions.create(**kwargs)
            return Response({"text": transcription.text, "language": language})
        except Exception:
            # Xato matnini klientga bermaymiz (kalit/prefix oshkor bo'lmasin)
            import logging
            logging.getLogger(__name__).exception("Speech transcription failed")
            return Response(
                {"detail": "Transkripsiya muvaffaqiyatsiz. Qayta urinib ko'ring."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

class HealthApiView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            return Response({"status": "db_unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"status": "ok"})
