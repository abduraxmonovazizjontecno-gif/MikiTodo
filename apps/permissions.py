
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .models import Project, ProjectMember, Task


def project_of(obj) -> Project:
    return obj if isinstance(obj, Project) else obj.project


def is_leader(user, project: Project) -> bool:
    if not user.is_authenticated:
        return False
    if project.owner_id == user.id:
        return True
    return ProjectMember.objects.filter(
        project=project, user=user, role=ProjectMember.ROLE_LEADER
    ).exists()


def is_member(user, project: Project) -> bool:
    if not user.is_authenticated:
        return False
    if project.owner_id == user.id:
        return True
    return ProjectMember.objects.filter(project=project, user=user).exists()


class IsProjectMember(BasePermission):
    message = "Siz bu loyiha a'zosi emassiz."

    def has_object_permission(self, request, view, obj):
        return is_member(request.user, project_of(obj))


class IsProjectLeader(BasePermission):
    message = "Bu amal faqat loyiha lideri uchun."

    def has_object_permission(self, request, view, obj):
        return is_leader(request.user, project_of(obj))


class IsTaskAssigneeOrLeader(BasePermission):
    message = "Faqat vazifa ijrochisi yoki loyiha lideri o'zgartira oladi."

    def has_object_permission(self, request, view, obj: Task):
        return obj.assignee_id == request.user.id or is_leader(
            request.user, obj.project
        )


def enforce(permission_class, request, view, obj) -> None:
    perm = permission_class()
    if not perm.has_object_permission(request, view, obj):
        raise PermissionDenied(getattr(perm, "message", None))
