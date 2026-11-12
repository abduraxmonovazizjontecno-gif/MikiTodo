from datetime import timedelta
from os import getenv
from os.path import join
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = getenv("DEBUG", "False").lower() == "true"

SECRET_KEY = getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-only"
    else:
        raise ImproperlyConfigured("SECRET_KEY env o'zgaruvchisi majburiy (DEBUG=False).")

raw_allowed_hosts = getenv("ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [
    host.strip()
    for host in raw_allowed_hosts.split(",")
    if host.strip()
]

if getenv("TRUST_PROXY", "False").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SECURE_SSL_REDIRECT = getenv("SECURE_SSL_REDIRECT", "False").lower() == "true"
    SECURE_REDIRECT_EXEMPT = [r"^health/$"]

    SESSION_COOKIE_SECURE = getenv("SESSION_COOKIE_SECURE", "False").lower() == "true"
    CSRF_COOKIE_SECURE = getenv("CSRF_COOKIE_SECURE", "False").lower() == "true"

    SECURE_HSTS_SECONDS = int(getenv("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
    SECURE_HSTS_PRELOAD = getenv("SECURE_HSTS_PRELOAD", "False").lower() == "true"
    SECURE_CONTENT_TYPE_NOSNIFF = True

# CSRF Trusted Origins
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

ADMIN_URL = getenv("ADMIN_URL", "admin/").strip("/") + "/"
ENABLE_API_DOCS = DEBUG or getenv("ENABLE_API_DOCS", "False").lower() == "true"

# Installed Apps
INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "channels",
    "apps",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "MikiTodo.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "MikiTodo.wsgi.application"
ASGI_APPLICATION = "MikiTodo.asgi.application"

_CHANNELS_REDIS_URL = getenv("REDIS_URL", "")
if _CHANNELS_REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [_CHANNELS_REDIS_URL]},
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }

AUTH_USER_MODEL = "apps.User"

USE_POSTGRES = getenv("USE_POSTGRES", "False").lower() == "true"

if USE_POSTGRES:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": getenv("DB_NAME", "mikidb"),
            "USER": getenv("DB_USER", "mikitouser"),
            "PASSWORD": getenv("DB_PASSWORD", ""),
            "HOST": getenv("DB_HOST", "localhost"),
            "PORT": getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": int(getenv("DB_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = join(BASE_DIR, "staticfiles")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = join(BASE_DIR, "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "NUM_PROXIES": int(getenv("NUM_PROXIES", "1")),
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "otp_send_phone": "3/min",
        "otp_send_ip": "20/hour",
        "otp_verify": "10/min",
        "google_login": "20/min",
        # Qo'shimcha: soatlik telefon limiti (taxminan 60 SMS/soat max)
        "otp_send_phone_hour": "30/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Miki Todo API",
    "DESCRIPTION": "Jamoaviy loyihalarni boshqarish mobil ilovasi backend API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

OPENAI_API_KEY = getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = getenv("GROQ_API_KEY", "")

REDIS_URL = getenv("REDIS_URL", "")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std"},
    },
    "root": {"handlers": ["console"], "level": getenv("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"level": "WARNING"},
    },
}
