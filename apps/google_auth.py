"""Google ID token tekshiruvi (Android Credential Manager / GetGoogleIdOption)."""
import logging
import os

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)


class GoogleAuthError(Exception):
    """Token yaroqsiz (401)."""


class GoogleNotConfigured(GoogleAuthError):
    """Serverda GOOGLE_CLIENT_IDS yo'q (503)."""


def allowed_client_ids() -> list[str]:
    """
    GOOGLE_CLIENT_IDS=<web-client-id>[,<boshqa>]
    Android'dagi setServerClientId(...) ga berilgan WEB client ID shu yerda bo'lishi kerak.
    """
    raw = os.getenv("GOOGLE_CLIENT_IDS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def verify_google_id_token(token: str) -> dict:
    """
    Imzo, `iss`, `exp` va `aud` ni tekshiradi; email tasdiqlangan bo'lishi shart.
    Qaytaradi: {"sub", "email", "given_name", "family_name", ...}
    """
    client_ids = allowed_client_ids()
    if not client_ids:
        raise GoogleNotConfigured("GOOGLE_CLIENT_IDS sozlanmagan.")
    try:
        # audience ro'yxat bo'lishi mumkin — kutubxona ularning birortasiga mosligini tekshiradi
        info = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=client_ids
        )
    except ValueError as exc:  # imzo/aud/exp xato
        logger.info("Google token rad etildi: %s", exc)
        raise GoogleAuthError("Google token yaroqsiz.") from exc

    if not info.get("sub"):
        raise GoogleAuthError("Google token noto'g'ri.")
    if not info.get("email_verified"):
        raise GoogleAuthError("Google email tasdiqlanmagan.")
    return info
