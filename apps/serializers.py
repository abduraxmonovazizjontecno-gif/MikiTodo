from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    User, OTP, Workspace, Project, ProjectMember,
    Contact, ContactRequest, Task, Device,
)
from .utils import normalize_phone_number, NICKNAME_REGEX


# Auth

class SendOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)

    def validate_phone_number(self, value):
        return normalize_phone_number(value)


class VerifyOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    code = serializers.CharField(max_length=6, min_length=6)
    nickname = serializers.CharField(max_length=50, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)

    def validate_phone_number(self, value):
        return normalize_phone_number(value)

    def validate_nickname(self, value):
        if value and not NICKNAME_REGEX.match(value):
            raise serializers.ValidationError(
                "Nickname faqat lotin harf, raqam va _ dan iborat bo'lishi kerak (3-20 belgi)."
            )
        return value


class ProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "phone_number", "nickname", "first_name", "last_name",
            "full_name", "avatar", "language", "date_joined",
        ]
        read_only_fields = ["id", "phone_number", "date_joined"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Android org.json: JSON null -> optString "null" bo'lib qolmasligi uchun bo'sh satr
        for key in ("phone_number", "nickname"):
            if data.get(key) is None:
                data[key] = ""
        return data


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["nickname", "first_name", "last_name", "avatar", "language"]

    def validate_nickname(self, value):
        if value and not NICKNAME_REGEX.match(value):
            raise serializers.ValidationError(
                "Nickname faqat lotin harf, raqam va _ dan iborat bo'lishi kerak (3-20 belgi)."
            )
        if not value:
            return None  # bo'sh satr emas NULL saqlanadi (unique cheklov to'qnashmasligi uchun)
        user = self.context["request"].user
        if User.objects.filter(nickname__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("Bu nickname band.")
        return value

    def validate_avatar(self, value):
        if value and value.size > 2 * 1024 * 1024:  # 2 MB
            raise serializers.ValidationError("Avatar 2 MB dan katta bo'lmasligi kerak.")
        return value

    def update(self, instance, validated_data):
        # Eski avatar faylini o'chirish
        new_avatar = validated_data.get("avatar")
        if new_avatar and instance.avatar:
            old = instance.avatar
            try:
                if old and old.name and old.storage.exists(old.name):
                    old.delete(save=False)
            except Exception:
                pass
        return super().update(instance, validated_data)


class GoogleLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField(max_length=4096, trim_whitespace=True)


class NicknameCheckSerializer(serializers.Serializer):
    nickname = serializers.CharField(max_length=50)

    def validate_nickname(self, value):
        if not NICKNAME_REGEX.match(value):
            raise serializers.ValidationError(
                "Nickname faqat lotin harf, raqam va _ dan iborat bo'lishi kerak (3-20 belgi)."
            )
        return value


# Workspace / Project

class UserPublicSerializer(serializers.ModelSerializer):
    """Qidiruv va kontakt ro'yxati uchun qisqa user ma'lumoti."""
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "nickname", "first_name", "last_name", "full_name", "avatar"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for key in ("nickname", "first_name", "last_name"):
            if data.get(key) is None:
                data[key] = ""
        return data


class WorkspaceSerializer(serializers.ModelSerializer):
    owner_id = serializers.IntegerField(source="owner.id", read_only=True)
    projects_count = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ["id", "name", "owner_id", "projects_count", "created_at", "updated_at"]
        read_only_fields = ["id", "owner_id", "created_at", "updated_at"]

    def get_projects_count(self, obj):
        if hasattr(obj, "projects_count_ann"):
            return obj.projects_count_ann
        return obj.projects.count()


class ProjectMemberSerializer(serializers.ModelSerializer):
    user = UserPublicSerializer(read_only=True)
    user_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = ProjectMember
        fields = ["id", "user", "user_id", "role", "joined_at"]
        read_only_fields = ["id", "joined_at"]


class ProjectSerializer(serializers.ModelSerializer):
    owner_id = serializers.IntegerField(source="owner.id", read_only=True)
    workspace_id = serializers.IntegerField(source="workspace.id", read_only=True)
    progress_percent = serializers.IntegerField(read_only=True)
    members_count = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id", "name", "description", "workspace_id", "owner_id",
            "progress_percent", "members_count", "my_role",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "owner_id", "workspace_id", "created_at", "updated_at"]

    def get_members_count(self, obj):
        if hasattr(obj, "members_count_ann"):
            return obj.members_count_ann
        return obj.members.count()

    def get_my_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = obj.members.filter(user=request.user).first()
        return membership.role if membership else None


class ProjectCreateSerializer(serializers.Serializer):
    workspace_id = serializers.IntegerField()
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")


# Contacts

class ContactSerializer(serializers.ModelSerializer):
    contact_user = UserPublicSerializer(read_only=True)

    class Meta:
        model = Contact
        fields = ["id", "contact_user", "created_at"]


class ContactRequestSerializer(serializers.ModelSerializer):
    from_user = UserPublicSerializer(read_only=True)
    to_user = UserPublicSerializer(read_only=True)
    project_id = serializers.IntegerField(source="project.id", read_only=True, allow_null=True)
    project_name = serializers.CharField(source="project.name", read_only=True, default=None)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = ContactRequest
        fields = [
            "id", "from_user", "to_user", "project_id", "project_name",
            "status", "created_at", "expires_at", "responded_at", "is_expired",
        ]


class SendContactRequestSerializer(serializers.Serializer):
    to_user_id = serializers.IntegerField()
    project_id = serializers.IntegerField(required=False, allow_null=True)


class AddProjectMemberSerializer(serializers.Serializer):
    """Faqat kontaktidagi odamni loyihaga qo'shish."""
    user_id = serializers.IntegerField()


# Task

class TaskSerializer(serializers.ModelSerializer):
    assignee = UserPublicSerializer(read_only=True)
    assignee_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    created_by = UserPublicSerializer(read_only=True)
    project_id = serializers.IntegerField(source="project.id", read_only=True)

    class Meta:
        model = Task
        fields = [
            "id", "project_id", "title", "description", "status", "priority",
            "deadline", "assignee", "assignee_id", "created_by",
            "created_at", "updated_at", "completed_at",
        ]
        read_only_fields = [
            "id", "project_id", "created_by", "created_at", "updated_at", "completed_at",
        ]

    def validate_status(self, value):
        if value not in dict(Task.STATUS_CHOICES):
            raise serializers.ValidationError("Noto'g'ri status.")
        return value

    def validate_priority(self, value):
        if value not in dict(Task.PRIORITY_CHOICES):
            raise serializers.ValidationError("Noto'g'ri priority.")
        return value


class TaskCreateSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    title = serializers.CharField(max_length=500)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    priority = serializers.ChoiceField(
        choices=Task.PRIORITY_CHOICES, default=Task.PRIORITY_MEDIUM
    )
    deadline = serializers.DateTimeField(required=False, allow_null=True)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)


class TaskUpdateSerializer(serializers.Serializer):
    """PATCH /tasks/<id>/ — maydonlar ixtiyoriy (partial), lekin qiymatlari validatsiya qilinadi."""
    title = serializers.CharField(max_length=500)
    description = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=Task.STATUS_CHOICES)
    priority = serializers.ChoiceField(choices=Task.PRIORITY_CHOICES)
    deadline = serializers.DateTimeField(allow_null=True)
    assignee_id = serializers.IntegerField(allow_null=True)


class TaskStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Task.STATUS_CHOICES)


# Device / FCM

class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["id", "fcm_token", "device_name", "created_at"]
        read_only_fields = ["id", "created_at"]


# Speech-to-Text

class SpeechTranscribeSerializer(serializers.Serializer):
    # audio fayl request.FILES orqali keladi
    language = serializers.ChoiceField(
        choices=["uz", "ru", "en"], required=False, default="uz"
    )


# Helpers

def get_tokens_for_user(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }