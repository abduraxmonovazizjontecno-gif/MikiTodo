from django.contrib.auth import get_user_model
from django.db.models import Q, Max, OuterRef, Subquery
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .chat import are_contacts, serialize_message
from .models import ChatMessage, Contact
from .serializers import UserPublicSerializer

User = get_user_model()


class ChatThreadApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            other_id = int(request.query_params.get("with", 0))
        except (TypeError, ValueError):
            return Response({"detail": "with parametr kerak"}, status=400)
        if other_id == request.user.id:
            return Response({"detail": "Noto'g'ri user"}, status=400)
        if not are_contacts(request.user.id, other_id):
            return Response(
                {"detail": "Faqat kontaktlar bilan chat"},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = ChatMessage.objects.filter(
            Q(sender=request.user, recipient_id=other_id)
            | Q(sender_id=other_id, recipient=request.user)
        ).select_related("sender", "recipient")

        before = request.query_params.get("before_id")
        if before:
            try:
                qs = qs.filter(id__lt=int(before))
            except ValueError:
                pass

        try:
            limit = int(request.query_params.get("limit", 50))
            if limit < 1:
                limit = 50
            limit = min(limit, 100)
        except (TypeError, ValueError):
            limit = 50

        msgs = list(qs.order_by("-created_at")[:limit])
        msgs.reverse()
        ChatMessage.objects.filter(
            sender_id=other_id, recipient=request.user, is_read=False
        ).update(is_read=True)

        return Response([serialize_message(m) for m in msgs])


class ChatDialogsApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.id
        contacts = Contact.objects.filter(user_id=uid).select_related("contact_user")
        other_ids = [c.contact_user_id for c in contacts]

        if not other_ids:
            return Response([])

        users = {u.id: u for u in User.objects.filter(id__in=other_ids)}
        result = []
        for oid in other_ids:
            last = (
                ChatMessage.objects.filter(
                    Q(sender_id=uid, recipient_id=oid)
                    | Q(sender_id=oid, recipient_id=uid)
                )
                .order_by("-created_at")
                .first()
            )
            unread = ChatMessage.objects.filter(
                sender_id=oid, recipient_id=uid, is_read=False
            ).count()
            u = users.get(oid)
            if not u:
                continue
            result.append({
                "user": UserPublicSerializer(u).data,
                "last_message": serialize_message(last) if last else None,
                "unread_count": unread,
            })
        result.sort(
            key=lambda x: x["last_message"]["created_at"] if x["last_message"] else "",
            reverse=True,
        )
        return Response(result)
