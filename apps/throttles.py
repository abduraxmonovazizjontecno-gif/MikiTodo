from rest_framework.exceptions import ValidationError
from rest_framework.throttling import SimpleRateThrottle

from .utils import normalize_phone_number


def _normalized_phone(request):
    """'998901234567', '+998 90 123-45-67' kabi variantlar bitta kalitga tushadi."""
    try:
        return normalize_phone_number(request.data.get("phone_number", ""))
    except (ValidationError, AttributeError):
        return None


class OTPSendPhoneThrottle(SimpleRateThrottle):
    """Bitta telefon raqamiga SMS spam qilishni cheklaydi (normalizatsiya qilingan raqam bo'yicha)."""
    scope = "otp_send_phone"

    def get_cache_key(self, request, view):
        phone = _normalized_phone(request)
        if not phone:
            return None
        return self.cache_format % {"scope": self.scope, "ident": phone}


class OTPSendIPThrottle(SimpleRateThrottle):
    """Bitta IP'dan turli raqamlarga ommaviy SMS yuborishni cheklaydi (SMS xarajati)."""
    scope = "otp_send_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class OTPVerifyThrottle(SimpleRateThrottle):
    """verify-otp brute-force'ini IP bo'yicha sekinlashtiradi (OTP.attempts bilan birga ishlaydi)."""
    scope = "otp_verify"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class GoogleLoginThrottle(SimpleRateThrottle):
    scope = "google_login"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
