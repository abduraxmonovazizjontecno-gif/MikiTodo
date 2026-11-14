import hashlib
import hmac
import logging
import os
import re
import secrets

import requests
from django.conf import settings
from rest_framework.serializers import ValidationError

logger = logging.getLogger(__name__)

PHONE_REGEX = re.compile(r"^\+?\d{9,15}$")


NICKNAME_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,20}$")


def normalize_phone_number(value: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", value or "")
    if not PHONE_REGEX.match(cleaned):
        raise ValidationError(
            "Telefon raqam noto'g'ri formatda. Masalan: +998901234567"
        )
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def generate_otp_code(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_otp(phone_number: str, code: str) -> str:
    key = settings.SECRET_KEY.encode()
    msg = f"{phone_number}:{code}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def send_sms(phone_number: str, message: str) -> bool:
    provider = os.getenv("SMS_PROVIDER", "").lower()
    if provider == "eskiz":
        return _send_via_eskiz(phone_number, message)

    if settings.DEBUG:
        print(f"[SMS] {phone_number} → {message}")
        return True

    logger.error("SMS_PROVIDER sozlanmagan — SMS yuborilmadi.")
    return False


def _send_via_eskiz(phone_number: str, message: str) -> bool:
    token = os.getenv("ESKIZ_TOKEN")
    if not token:
        return False
    clean_phone = phone_number.lstrip("+")
    try:
        resp = requests.post(
            "https://notify.eskiz.uz/api/message/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"mobile_phone": clean_phone, "message": message},
            timeout=10,
        )
        return resp.status_code == 200 and resp.json().get("status") == "success"
    except requests.RequestException:
        return False