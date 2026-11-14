
from __future__ import annotations

import json
import logging
from datetime import datetime

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework_simplejwt.tokens import AccessToken

from . import notifications
from .models import ChatMessage, Contact

logger = logging.getLogger(__name__)
User = get_user_model()


def user_group(user_id: int) -> str:
    return f"chat_user_{user_id}"


def are_contacts(a_id: int, b_id: int) -> bool:
    return Contact.objects.filter(user_id=a_id, contact_user_id=b_id).exists()


def serialize_message(msg: ChatMessage) -> dict:
    return {
        "type": "chat.message",
        "id": msg.id,
        "from_user_id": msg.sender_id,
        "to_user_id": msg.recipient_id,
        "text": msg.text,
        "is_read": msg.is_read,
        "created_at": msg.created_at.isoformat(),
    }


class ChatConsumer(AsyncJsonWebsocketConsumer):
    user = None

    async def connect(self):
        token = self.scope.get("query_string", b"").decode()
        jwt = ""
        for part in token.split("&"):
            if part.startswith("token="):
                jwt = part[6:]
                break
        user = await self._user_from_jwt(jwt)
        if user is None:
            await self.close(code=4401)
            return
        self.user = user
        await self.channel_layer.group_add(user_group(user.id), self.channel_name)
        await self.accept()
        logger.info("WS chat connected user=%s", user.id)

    async def disconnect(self, code):
        if self.user:
            await self.channel_layer.group_discard(
                user_group(self.user.id), self.channel_name
            )

    async def receive_json(self, content, **kwargs):
        if not self.user:
            return
        if not isinstance(content, dict):
            await self.send_json({"type": "chat.error", "detail": "JSON obyekt kutilgan"})
            return
        msg_type = content.get("type")
        if msg_type == "chat.send":
            await self._handle_send(content)
        elif msg_type == "chat.read":
            await self._handle_read(content)
        else:
            await self.send_json({"type": "chat.error", "detail": "Noto'g'ri type"})

    async def _handle_send(self, content):
        try:
            to_id = int(content.get("to_user_id"))
        except (TypeError, ValueError):
            await self.send_json({"type": "chat.error", "detail": "to_user_id noto'g'ri"})
            return
        text = (content.get("text") or "").strip()
        if not text:
            await self.send_json({"type": "chat.error", "detail": "Matn bo'sh"})
            return
        if len(text) > 4000:
            await self.send_json({"type": "chat.error", "detail": "Matn juda uzun"})
            return
        if to_id == self.user.id:
            await self.send_json({"type": "chat.error", "detail": "O'zingizga yozib bo'lmaydi"})
            return

        ok = await database_sync_to_async(are_contacts)(self.user.id, to_id)
        if not ok:
            await self.send_json({
                "type": "chat.error",
                "detail": "Faqat kontaktingizdagi odamga yozishingiz mumkin",
            })
            return

        msg = await database_sync_to_async(ChatMessage.objects.create)(
            sender_id=self.user.id,
            recipient_id=to_id,
            text=text,
        )
        payload = serialize_message(msg)
        await self.send_json({**payload, "type": "chat.ack"})
        await self.channel_layer.group_send(
            user_group(to_id),
            {"type": "chat.relay", "payload": payload},
        )
        await database_sync_to_async(notifications.chat_message)(msg, self.user)

    async def _handle_read(self, content):
        try:
            with_id = int(content.get("with_user_id"))
        except (TypeError, ValueError):
            return
        updated = await database_sync_to_async(
            lambda: ChatMessage.objects.filter(
                sender_id=with_id, recipient_id=self.user.id, is_read=False
            ).update(is_read=True)
        )()
        if updated:
            await self.channel_layer.group_send(
                user_group(with_id),
                {
                    "type": "chat.relay",
                    "payload": {
                        "type": "chat.read",
                        "with_user_id": self.user.id,
                        "reader_id": self.user.id,
                    },
                },
            )

    async def chat_relay(self, event):
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _user_from_jwt(self, raw: str):
        if not raw:
            return None
        try:
            token = AccessToken(raw)
            uid = token.get("user_id")
            return User.objects.filter(pk=uid, is_active=True).first()
        except Exception:
            return None