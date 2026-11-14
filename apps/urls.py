from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenBlacklistView

from .views import (
    SendOTPApiView,
    VerifyOTPApiView,
    CheckNicknameApiView,
    ProfileApiView,
    WorkspaceApiView,
    ProjectApiView,
    ProjectMembersApiView,
    ProjectDashboardApiView,
    ProjectAddMemberApiView,
    UserSearchApiView,
    ContactListApiView,
    ContactRequestApiView,
    ContactRequestRespondApiView,
    TaskApiView,
    TaskStatusApiView,
    DeviceApiView,
    SpeechTranscribeApiView,
)
from .chat_views import ChatThreadApiView, ChatDialogsApiView


urlpatterns = [
    path("auth/send-otp/", SendOTPApiView.as_view(), name="send-otp"),
    path("auth/verify-otp/", VerifyOTPApiView.as_view(), name="verify-otp"),
    path("auth/check-nickname/", CheckNicknameApiView.as_view(), name="check-nickname"),
    path("auth/profile/", ProfileApiView.as_view(), name="profile"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", TokenBlacklistView.as_view(), name="token-blacklist"),
    path("workspaces/", WorkspaceApiView.as_view(), name="workspaces"),
    path("workspaces/<int:pk>/", WorkspaceApiView.as_view(), name="workspace-detail"),
    path("projects/", ProjectApiView.as_view(), name="projects"),
    path("projects/<int:pk>/", ProjectApiView.as_view(), name="project-detail"),
    path("projects/<int:project_id>/members/", ProjectMembersApiView.as_view(), name="project-members"),
    path("projects/<int:project_id>/members/add/", ProjectAddMemberApiView.as_view(), name="project-add-member"),
    path("projects/<int:project_id>/dashboard/", ProjectDashboardApiView.as_view(), name="project-dashboard"),
    path("users/search/", UserSearchApiView.as_view(), name="user-search"),
    path("contacts/", ContactListApiView.as_view(), name="contacts"),
    path("contacts/request/", ContactRequestApiView.as_view(), name="contact-request"),
    path("contacts/requests/", ContactRequestApiView.as_view(), name="contact-requests"),
    path("contacts/requests/<int:pk>/accept/", ContactRequestRespondApiView.as_view(), {"action": "accept"}, name="contact-request-accept"),
    path("contacts/requests/<int:pk>/reject/", ContactRequestRespondApiView.as_view(), {"action": "reject"}, name="contact-request-reject"),
    path("tasks/", TaskApiView.as_view(), name="tasks"),
    path("tasks/<int:pk>/", TaskApiView.as_view(), name="task-detail"),
    path("tasks/<int:pk>/status/", TaskStatusApiView.as_view(), name="task-status"),
    path("devices/", DeviceApiView.as_view(), name="devices"),
    path("speech/transcribe/", SpeechTranscribeApiView.as_view(), name="speech-transcribe"),
    path("chat/messages/", ChatThreadApiView.as_view(), name="chat-messages"),
    path("chat/dialogs/", ChatDialogsApiView.as_view(), name="chat-dialogs"),
]