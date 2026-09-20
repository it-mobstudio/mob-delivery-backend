"""
Django settings for mob_delivery project.
"""

from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "core",
    "accounts",
    "drivers",
    "trips",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "mob_delivery.urls"

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

WSGI_APPLICATION = "mob_delivery.wsgi.application"

POSTGRES_DB = env("POSTGRES_DB")
POSTGRES_USER = env("POSTGRES_USER")
POSTGRES_PASSWORD = env("POSTGRES_PASSWORD")
POSTGRES_HOST = env("POSTGRES_HOST")
POSTGRES_PORT = env("POSTGRES_PORT")
POSTGRES_SSLMODE = env("POSTGRES_SSLMODE")


print("*******")
print(f"POSTGRES_DB: {POSTGRES_DB}", f"POSTGRES_USER: {POSTGRES_USER}", f"POSTGRES_PASSWORD: {POSTGRES_PASSWORD}", f"POSTGRES_HOST: {POSTGRES_HOST}", f"POSTGRES_PORT: {POSTGRES_PORT}", f"POSTGRES_SSLMODE: {POSTGRES_SSLMODE}")

print("*******")
# Database — DATABASE_URL-driven; defaults to SQLite if unset.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": POSTGRES_DB,
        "USER": POSTGRES_USER,
        "PASSWORD": POSTGRES_PASSWORD,
        "HOST": POSTGRES_HOST,
        "PORT": POSTGRES_PORT,
    }
}

AUTH_USER_MODEL = "accounts.AdminUser"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# File uploads — Azure Blob if AZURE_ACCOUNT_NAME is set, else local filesystem
# for dev. Flipping to Azure needs only env vars, no code changes.
AZURE_ACCOUNT_NAME = env("AZURE_ACCOUNT_NAME", default="")
AZURE_ACCOUNT_KEY = env("AZURE_ACCOUNT_KEY", default="")
AZURE_CONTAINER = env("AZURE_CONTAINER", default="mob-uploads")
MAX_UPLOAD_SIZE_MB = env.int("MAX_UPLOAD_SIZE_MB", default=10)

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

if AZURE_ACCOUNT_NAME:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.azure_storage.AzureStorage",
            "OPTIONS": {
                "account_name": AZURE_ACCOUNT_NAME,
                "account_key": AZURE_ACCOUNT_KEY,
                "azure_container": AZURE_CONTAINER,
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


# Email
MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"},
}


# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.JWTMultiPrincipalAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardResultsPagination",
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "EXCEPTION_HANDLER": "core.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("ACCESS_TOKEN_LIFETIME_MINUTES", default=60)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("REFRESH_TOKEN_LIFETIME_DAYS", default=7)),
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=None) or SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

API_CLIENT_TOKEN_LIFETIME_MINUTES = env.int("API_CLIENT_TOKEN_LIFETIME_MINUTES", default=60)

DRIVER_TOKEN_LIFETIME_MINUTES = env.int("DRIVER_TOKEN_LIFETIME_MINUTES", default=60)

# Non-prod-only: return the generated OTP in the otp/request response body so
# the driver login flow is testable without a real SMS gateway wired in.
# Defaults to DEBUG but is a separate flag so it can be flipped independently
# (e.g. a staging environment that runs DEBUG=False but still wants this).
DRIVER_OTP_DEBUG_RESPONSE = env.bool("DRIVER_OTP_DEBUG_RESPONSE", default=DEBUG)


# Redis cache — used for ephemeral driver OTP storage (see drivers.services).
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/1")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}


# Celery — currently only used for the daily DL-expiry-lock beat task
# (drivers.tasks.lock_expired_driver_licenses).
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

CELERY_BEAT_SCHEDULE = {
    "lock-expired-driver-licenses": {
        "task": "drivers.tasks.lock_expired_driver_licenses",
        "schedule": crontab(hour=0, minute=15),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "MOB Delivery Backend API",
    "DESCRIPTION": "Multi-tenant delivery platform API — Company/AdminUser/ApiClient auth, Vehicle module.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}


# Valhalla — self-hosted routing engine (see docker-compose.yml), used by
# trips.routing.get_route for turn-by-turn distance/duration/polyline.
# Build tiles from a Bengaluru/Karnataka OSM extract before first use (see
# docker-compose.yml's valhalla service comment).
VALHALLA_URL = env("VALHALLA_URL", default="http://localhost:8002")
VALHALLA_TIMEOUT_SECONDS = env.int("VALHALLA_TIMEOUT_SECONDS", default=10)

# trips.matching — how far (in km) from pickup a driver's last reported
# location may be and still be considered for assignment.
DRIVER_MATCH_RADIUS_KM = env.float("DRIVER_MATCH_RADIUS_KM", default=8.0)

# Kafka — trip lifecycle events published by trips.events.publish_trip_event
# for other services (analytics, notifications, partner webhooks, live
# tracking) to consume. Never on the critical path: publish failures are
# logged, not raised — see trips.events for why.
KAFKA_ENABLED = env.bool("KAFKA_ENABLED", default=False)
KAFKA_BOOTSTRAP_SERVERS = env.list("KAFKA_BOOTSTRAP_SERVERS", default=["localhost:9092"])
KAFKA_TRIP_EVENTS_TOPIC = env("KAFKA_TRIP_EVENTS_TOPIC", default="trip-events")

# trips.payments.UpiDeepLinkPaymentProvider — the VPA a COD trip's "scan to
# pay" QR is made out to. Stand-in until a real payment gateway is wired
# up; see trips/payments.py.
COMPANY_UPI_VPA = env("COMPANY_UPI_VPA", default="mob-delivery@upi")
COMPANY_UPI_PAYEE_NAME = env("COMPANY_UPI_PAYEE_NAME", default="MOB Delivery")
