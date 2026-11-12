"""
FCM push-notificationlar (TZ: Firebase Cloud Messaging).

- DATA-only xabarlar yuboriladi (Android FirebaseService.onMessageReceived har doim ishlashi uchun):
  type, title, body, task_id, project_id  (hammasi str).
- Matn foydalanuvchi tiliga (User.language: uz/ru/en) qarab render qilinadi.
- Yuborish so'rov tranzaksiyasi COMMIT bo'lgach va alohida threadda bajariladi —
  API javobi FCM'ni kutmaydi, rollback bo'lsa push ham ketmaydi.
- Yaroqsiz (o'chirilgan) tokenlar avtomatik Device jadvalidan o'chiriladi.

Sozlash (bittasi yetarli):
    FIREBASE_CREDENTIALS_FILE=/run/secrets/firebase.json
    FIREBASE_CREDENTIALS_JSON='{"type":"service_account", ...}'
Sozlanmagan bo'lsa push jimgina o'tkazib yuboriladi (log'da ogohlantirish bilan).
"""
import json
import logging
import os
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from .models import Device, ProjectMember, User

logger = logging.getLogger(__name__)

# Android FirebaseTools.DelegatedTask bilan bir xil qiymat
TYPE_TASK_ASSIGNED = "Delegated-task"
TYPE_TASK_DONE = "Task-done"
TYPE_DEADLINE = "Task-deadline"
TYPE_MEMBER_JOINED = "Member-joined"
TYPE_CONTACT_REQUEST = "Contact-request"
TYPE_CONTACT_ACCEPTED = "Contact-accepted"
# Android FirebaseTools.NewMessage bilan bir xil qiymat
TYPE_NEW_MESSAGE = "New-message"

TEXTS = {
    TYPE_TASK_ASSIGNED: {
        "uz": ("Yangi vazifa", "{actor} sizga vazifa biriktirdi: {task}"),
        "ru": ("Новая задача", "{actor} назначил(а) вам задачу: {task}"),
        "en": ("New task", "{actor} assigned you a task: {task}"),
    },
    TYPE_TASK_DONE: {
        "uz": ("Vazifa bajarildi", "{actor} bajardi: {task}"),
        "ru": ("Задача выполнена", "{actor} выполнил(а): {task}"),
        "en": ("Task completed", "{actor} completed: {task}"),
    },
    TYPE_DEADLINE: {
        "uz": ("Muddat yaqin", "«{task}» muddati: {when}"),
        "ru": ("Срок близко", "Срок задачи «{task}»: {when}"),
        "en": ("Deadline soon", "“{task}” is due: {when}"),
    },
    TYPE_MEMBER_JOINED: {
        "uz": ("Yangi a'zo", "{actor} «{project}» loyihasiga qo'shildi"),
        "ru": ("Новый участник", "{actor} присоединился(-ась) к проекту «{project}»"),
        "en": ("New member", "{actor} joined “{project}”"),
    },
    TYPE_CONTACT_REQUEST: {
        "uz": ("Yangi zapros", "{actor} sizga kontakt zaprosi yubordi"),
        "ru": ("Новый запрос", "{actor} отправил(а) вам запрос в контакты"),
        "en": ("New request", "{actor} sent you a contact request"),
    },
    TYPE_CONTACT_ACCEPTED: {
        "uz": ("Zapros qabul qilindi", "{actor} zaprosingizni qabul qildi"),
        "ru": ("Запрос принят", "{actor} принял(а) ваш запрос"),
        "en": ("Request accepted", "{actor} accepted your request"),
    },
}


def render(kind: str, lang: str, **ctx) -> tuple[str, str]:
    variants = TEXTS[kind]
    title, body = variants.get(lang) or variants["uz"]
    return title, body.format(**ctx)


def display_name(user: User) -> str:
    return user.nickname or user.full_name or "Miki"


def project_leaders(project):
    return User.objects.filter(
        Q(pk=project.owner_id)
        | Q(project_memberships__project=project, project_memberships__role=ProjectMember.ROLE_LEADER)
    ).distinct()


# Firebase

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fcm")
_app = None
_app_lock = threading.Lock()


def _get_app():
    """Firebase app'ni bir marta, lazy yaratadi. Sozlanmagan bo'lsa None."""
    global _app
    if _app is not None:
        return _app
    with _app_lock:
        if _app is not None:
            return _app
        raw = os.getenv("FIREBASE_CREDENTIALS_JSON")
        path = os.getenv("FIREBASE_CREDENTIALS_FILE")
        if not raw and not path:
            return None
        if not raw and not os.path.isfile(path):
            logger.warning("FIREBASE_CREDENTIALS_FILE topilmadi: %s — push o'chirilgan.", path)
            return None
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(json.loads(raw) if raw else path)
        _app = firebase_admin.initialize_app(cred)
        return _app


def _send_multicast(tokens: list[str], data: dict) -> list[str]:
    """Yuboradi; yaroqsiz tokenlar ro'yxatini qaytaradi. (Testlarda mock qilinadi.)"""
    app = _get_app()
    if app is None:
        logger.warning("FCM sozlanmagan (FIREBASE_CREDENTIALS_*) — push yuborilmadi.")
        return []

    from firebase_admin import messaging

    # Diqqat: `tokens` firebase-admin 7.6+ da "deprecated" deb belgilangan, lekin `fids` (Installation ID)
    # Android'dagi FirebaseMessaging.getToken() (registration token) bilan bir xil narsa EMAS.
    # Klient registration token yuborayotgan ekan, `tokens` to'g'ri; shuning uchun versiya pin qilingan.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        message = messaging.MulticastMessage(
            tokens=tokens,
            data=data,
            android=messaging.AndroidConfig(priority="high"),
        )
    response = messaging.send_each_for_multicast(message, app=app)

    dead = []
    for token, result in zip(tokens, response.responses):
        if result.success:
            continue
        exc = result.exception
        if isinstance(exc, (messaging.UnregisteredError, messaging.SenderIdMismatchError)) or (
            exc is not None and "registration token" in str(exc).lower()
        ):
            dead.append(token)
        else:
            logger.warning("FCM xatosi: %s", exc)
    return dead


def _deliver(user_id: int, data: dict) -> None:
    from django.db import close_old_connections
    try:
        close_old_connections()
        tokens = list(Device.objects.filter(user_id=user_id).values_list("fcm_token", flat=True))
        if not tokens:
            return
        dead = _send_multicast(tokens, data)
        if dead:
            Device.objects.filter(fcm_token__in=dead).delete()
    except Exception:  # push hech qachon API'ni yiqitmasin
        logger.exception("FCM yuborishda kutilmagan xato (user=%s)", user_id)
    finally:
        close_old_connections()


def _queue(user_id: int, data: dict) -> None:
    """Tranzaksiya commit bo'lgach push'ni navbatga qo'yadi (sync yoki threadpool)."""

    def _go():
        if getattr(settings, "FCM_SYNC", False):
            _deliver(user_id, data)
        else:
            _executor.submit(_deliver, user_id, data)

    transaction.on_commit(_go)


def notify(user: User, kind: str, *, task_obj=None, project_obj=None, extra_data=None, **ctx) -> None:
    """
    `user`ga push navbatga qo'yadi (commit'dan keyin). Matn user.language bo'yicha.
    """
    title, body = render(kind, user.language or "uz", **ctx)
    data = {"type": kind, "title": title, "body": body}
    if task_obj is not None:
        data["task_id"] = str(task_obj.id)
        project_obj = project_obj or task_obj.project
    if project_obj is not None:
        data["project_id"] = str(project_obj.id)
    if extra_data:
        for k, v in extra_data.items():
            data[str(k)] = str(v)

    _queue(user.id, data)


# Voqealar

def task_assigned(task, actor) -> None:
    if task.assignee_id and task.assignee_id != actor.id:
        notify(task.assignee, TYPE_TASK_ASSIGNED, task_obj=task, actor=display_name(actor), task=task.title)


def task_done(task, actor) -> None:
    """Ijrochi vazifani tugatsa — liderlarga (o'zidan boshqa) xabar."""
    for leader in project_leaders(task.project).exclude(pk=actor.id):
        notify(leader, TYPE_TASK_DONE, task_obj=task, actor=display_name(actor), task=task.title)


def member_joined(project, new_member, inviter) -> None:
    """Yangi a'zoga 'siz qo'shildingiz' push; liderga ham (agar boshqa bo'lsa)."""
    # Asosiy: yangi a'zo bilishi kerak
    if inviter and inviter.id != new_member.id:
        notify(
            new_member,
            TYPE_MEMBER_JOINED,
            project_obj=project,
            actor=display_name(inviter),
            project=project.name,
        )
        # Liderga ham (o'ziga o'zi emas)
        notify(
            inviter,
            TYPE_MEMBER_JOINED,
            project_obj=project,
            actor=display_name(new_member),
            project=project.name,
        )


def contact_request_sent(req) -> None:
    """Zapros yuborilganda qabul qiluvchiga push."""
    notify(
        req.to_user,
        TYPE_CONTACT_REQUEST,
        project_obj=req.project,
        actor=display_name(req.from_user),
        extra_data={"request_id": str(req.id)},
    )


def chat_message(message, sender: User) -> None:
    """
    Yangi chat xabari — recipientga push.

    Diqqat: matn shablon (`render`/`.format`) orqali EMAS, to'g'ridan-to'g'ri
    yuboriladi — chat matni erkin foydalanuvchi matni, agar u '{'/'}' belgilarini
    o'z ichiga olsa `.format(**ctx)` bilan ishlangan boshqa turlardek KeyError/
    ValueError berib, push butunlay yuborilmay qolardi.
    """
    body = (message.text or "").strip()
    if len(body) > 200:
        body = body[:200] + "…"

    data = {
        "type": TYPE_NEW_MESSAGE,
        "title": display_name(sender),
        "body": body,
        "from_user_id": str(sender.id),
    }
    _queue(message.recipient_id, data)


def contact_request_accepted(req) -> None:
    """Zapros qabul qilinganda yuboruvchiga push."""
    notify(
        req.from_user,
        TYPE_CONTACT_ACCEPTED,
        project_obj=req.project,
        actor=display_name(req.to_user),
        extra_data={"request_id": str(req.id)},
    )