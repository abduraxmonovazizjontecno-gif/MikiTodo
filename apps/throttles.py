from rest_framework.exceptions import ValidationError
from rest_framework.throttling import SimpleRateThrottle

from .utils import normalize_phone_number


def _normalized_phone(request):
    try:
        return normalize_phone_number(request.data.get("phone_number", ""))
    except (ValidationError, AttributeError):
        return None


class OTPSendPhoneThrottle(SimpleRateThrottle):
    scope = "otp_send_phone"

    def get_cache_key(self, request, view):
        phone = _normalized_phone(request)
        if not phone:
            return None
        return self.cache_format % {"scope": self.scope, "ident": phone}


class OTPSendIPThrottle(SimpleRateThrottle):
    scope = "otp_send_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class OTPVerifyThrottle(SimpleRateThrottle):
    scope = "otp_verify"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


