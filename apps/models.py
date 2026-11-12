import hmac
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils import timezone


class CustomUserManager(UserManager):
    def _create_user_object(self, phone_number, password, email=None, **extra_fields):
        if not phone_number:
            raise ValueError("phone_number is required")
        user = self.model(phone_number=phone_number, email=email or "", **extra_fields)
        if password:
            user.password = make_password(password)
        else:
            user.set_unusable_password()
        return user

    def _create_user(self, phone_number, password, email=None, **extra_fields):
        user = self._create_user_object(phone_number, password, email, **extra_fields)
        user.save(using=self._db)
        return user

    def create_user(self, phone_number, password=None, email=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone_number, password, email, **extra_fields)

    def create_superuser(self, phone_number, password=None, email=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(phone_number, password, email, **extra_fields)


class User(AbstractUser):
    """
    Asosiy foydalanuvchi.
    Kirish: telefon raqami + OTP (yoki JWT).
    """
    username = None
    first_name = models.CharField(max_length=150, blank=True, default="")
    last_name = models.CharField(max_length=150, blank=True, default="")
    nickname = models.CharField(max_length=50, unique=True, null=True, blank=True)
    # Google orqali kirgan foydalanuvchida telefon bo'lmaydi (null); OTP orqali kirganda har doim bor.
    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    google_sub = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    # Frontend til sozlamasi (UZ / RU / EN)
    language = models.CharField(
        max_length=5,
        choices=[("uz", "O'zbekcha"), ("ru", "Русский"), ("en", "English")],
        default="uz",
    )

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-date_joined"]

    def __str__(self):
        return self.nickname or self.phone_number or self.email or f"user#{self.pk}"

    @property
    def full_name(self):
        return (
            f"{self.first_name} {self.last_name}".strip()
            or self.nickname
            or self.phone_number
            or ""
        )


class OTP(models.Model):
    """
    Telefon orqali kirish uchun bir martalik kod.
    Kod ochiq holda saqlanmaydi: HMAC-SHA256 (SECRET_KEY bilan) hash saqlanadi.
    """
    MAX_ATTEMPTS = 5
    LIFETIME_MINUTES = 5

    phone_number = models.CharField(max_length=20, db_index=True)
    code = models.CharField(max_length=64)  # hex digest
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def is_valid(self, lifetime_minutes: int | None = None) -> bool:
        if self.is_used or self.attempts >= self.MAX_ATTEMPTS:
            return False
        minutes = lifetime_minutes or self.LIFETIME_MINUTES
        return timezone.now() < self.created_at + timedelta(minutes=minutes)

    def matches(self, raw_code: str) -> bool:
        from .utils import hash_otp  # circular importdan qochish
        return hmac.compare_digest(self.code, hash_otp(self.phone_number, raw_code))

    def __str__(self):
        return f"OTP({self.phone_number}, used={self.is_used})"


class Workspace(models.Model):
    """
    Ishchi maydon (Workspace).
    Lider yaratadi.
    """
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="owned_workspaces"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Project(models.Model):
    """Loyiha. Workspace ichida bo'ladi."""
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="projects"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="owned_projects"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def progress_percent(self) -> int:
        total = self.tasks.count()
        if total == 0:
            return 0
        done = self.tasks.filter(status=Task.STATUS_DONE).count()
        return int(round(done * 100 / total))


class ProjectMember(models.Model):
    """Loyiha a'zolari va rollari."""
    ROLE_LEADER = "leader"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [
        (ROLE_LEADER, "Loyiha Lideri"),
        (ROLE_MEMBER, "Jamoa A'zosi"),
    ]

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="project_memberships"
    )
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"], name="unique_project_member"
            )
        ]
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"{self.project.name} — {self.user} ({self.role})"



class Contact(models.Model):
    """
    Mutual kontakt (do'stlik).
    Ikkala tomon uchun alohida yozuv yaratiladi:
      Contact(user=A, contact_user=B)
      Contact(user=B, contact_user=A)
    """
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="contacts"
    )
    contact_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="contacted_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "contact_user"],
                name="unique_contact_pair",
            )
        ]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["contact_user"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} ↔ {self.contact_user}"


class ContactRequest(models.Model):
    """
    Zapros (kontakt so'rovi). 12 soat amal qiladi.
    Accept qilinsa → mutual Contact + (ixtiyoriy) ProjectMember.
    """
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Kutilmoqda"),
        (STATUS_ACCEPTED, "Qabul qilingan"),
        (STATUS_REJECTED, "Rad etilgan"),
        (STATUS_EXPIRED, "Muddati o'tgan"),
        (STATUS_CANCELLED, "Bekor qilingan"),
    ]

    LIFETIME_HOURS = 12

    from_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_contact_requests"
    )
    to_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="received_contact_requests"
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="contact_requests",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["to_user", "status"]),
            models.Index(fields=["from_user", "status"]),
            models.Index(fields=["expires_at"]),
        ]
        # Note: full unique on (from,to,pending) is hard with status; app-level + lock protects races
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=self.LIFETIME_HOURS)
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_pending(self) -> bool:
        return self.status == self.STATUS_PENDING and not self.is_expired

    def __str__(self):
        return f"{self.from_user} → {self.to_user} ({self.status})"


class Task(models.Model):
    """
    Vazifa.
    Status: To Do → In Progress → Done
    """
    STATUS_TODO = "todo"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_DONE = "done"
    STATUS_CHOICES = [
        (STATUS_TODO, "To Do"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_DONE, "Done"),
    ]

    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Past"),
        (PRIORITY_MEDIUM, "O'rta"),
        (PRIORITY_HIGH, "Yuqori"),
    ]

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="tasks"
    )
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default="")  # STT dan kelgan matn ham shu yerda
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_TODO, db_index=True
    )
    priority = models.CharField(
        max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM
    )
    deadline = models.DateTimeField(null=True, blank=True)
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="created_tasks"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Deadline eslatmasi yuborilgan vaqt (deadline o'zgarsa None ga qaytariladi)
    deadline_reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["assignee"]),
            models.Index(fields=["deadline"]),
        ]

    def __str__(self):
        return self.title

    def mark_done(self):
        self.status = self.STATUS_DONE
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])


class Device(models.Model):
    """
    FCM push-notification uchun qurilma tokeni.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    fcm_token = models.CharField(max_length=512, unique=True)
    device_name = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user"])]

    def __str__(self):
        return f"{self.user} — {self.fcm_token[:20]}..."

class ChatMessage(models.Model):
    """1:1 chat — faqat o'zaro Contact bo'lgan userlar orasida."""

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sent",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_received",
    )
    text = models.TextField(max_length=4000)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["sender", "recipient", "created_at"]),
            models.Index(fields=["recipient", "is_read"]),
        ]

    def __str__(self):
        return f"{self.sender_id} → {self.recipient_id}: {self.text[:40]}"
