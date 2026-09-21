"""
Django settings for mob_delivery project.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import environ
from celery.schedules import crontab

from core.cors import parse_origins

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
    # First, so it can answer a browser's CORS preflight before anything else
    # runs. Deployed origins use CORS_ALLOWED_ORIGINS; localhost needs DEBUG.
    "core.middleware.DevCorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Browser access (CORS). Native iOS/Android apps aren't subject to it; a Flutter
# *web* build is, because it runs on a different origin from this API.
#
# * DEV_CORS_ORIGIN_REGEX - only while DEBUG is on: the Flutter web dev server on
#   localhost, on whatever port it picks.
# * CORS_ALLOWED_ORIGINS - in ANY mode: the exact origins of deployed web apps,
#   comma-separated, e.g. https://mob-driver.netlify.app . Scheme + host (+ port),
#   no path, no wildcard. Bearer tokens travel in a header, so credentials
#   (cookies) are never allowed cross-origin.
DEV_CORS_ORIGIN_REGEX = env("DEV_CORS_ORIGIN_REGEX", default=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
CORS_ALLOWED_ORIGINS = parse_origins(env.list("CORS_ALLOWED_ORIGINS", default=[]))

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

# The driver app keeps a shift-long session alive by trading this refresh
# token for a new access token (POST /driver/auth/refresh) — the driver only
# has to redo the SMS OTP once it lapses. See drivers.tokens.
DRIVER_REFRESH_TOKEN_LIFETIME_DAYS = env.int("DRIVER_REFRESH_TOKEN_LIFETIME_DAYS", default=30)

# Non-prod-only: return the generated OTP in the otp/request response body so
# the driver login flow is testable without a real SMS gateway wired in.
# Defaults to DEBUG but is a separate flag so it can be flipped independently
# (e.g. a staging environment that runs DEBUG=False but still wants this).
DRIVER_OTP_DEBUG_RESPONSE = env.bool("DRIVER_OTP_DEBUG_RESPONSE", default=DEBUG)

# Self-registration: a phone number nobody has registered yet can sign in with
# an OTP and becomes a driver of THIS company (then fills in details and uploads
# documents for that company's admins to verify). Blank means sign-up is closed
# and only drivers the company created can log in — except in DEBUG, where the
# only active company, if there's exactly one, is used so local dev needs no
# setup. See DriverService.signup_company.
DRIVER_SIGNUP_COMPANY_ID = env("DRIVER_SIGNUP_COMPANY_ID", default="")

# The share of a completed trip's fare that goes to the driver's wallet
# (trips.services.TripService.driver_complete → drivers.wallet). The rest is the
# company's margin. Business rule with no source of truth yet: set it here until
# it moves onto the fare card / company settings.
DRIVER_EARNING_PERCENT = Decimal(env("DRIVER_EARNING_PERCENT", default="80"))


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

# API documentation (Swagger UI at /api/docs/, ReDoc at /api/redoc/, the raw
# OpenAPI file at /api/schema/). The prose lives in docs/guides/ and the
# per-endpoint text in core/openapi/ - see core/openapi/__init__.py.
# Set API_PUBLIC_URL (e.g. https://api.example.com) to list the live server in
# the docs' "server" picker next to the relative one.
API_PUBLIC_URL = env("API_PUBLIC_URL", default="").rstrip("/")

_BEARER = "A JWT sent as `Authorization: Bearer <token>`."
SPECTACULAR_SETTINGS = {
    "TITLE": "MOB Delivery API",
    "DESCRIPTION": (
        "Book deliveries, follow them live, and run the driver app - one REST API for "
        "companies (server-to-server or admin panel) and for the drivers' mobile app.\n\n"
        "**New here?** Read *Introduction* and *Authentication* in the menu, then follow "
        "*Booking a delivery*. Every endpoint below lists who may call it, which fields are "
        "required, real request/response examples and every error it can return.\n\n"
        "On a running server: `/api/docs/` is the interactive console (try calls), `/api/redoc/` is this reference, "
        "and `/api/schema/` is the machine-readable OpenAPI file."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Public documentation: viewing it must not need a token (a stale one in the
    # browser must not break it either).
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "SCHEMA_PATH_PREFIX_TRIM": True,
    "SERVERS": [{"url": "/api/v1", "description": "This server"}]
    + ([{"url": f"{API_PUBLIC_URL}/api/v1", "description": "Production"}] if API_PUBLIC_URL else []),
    # Separate shapes for what you send and what you get back, so read-only
    # fields (id, timestamps, ...) never appear as things you could send.
    "COMPONENT_SPLIT_REQUEST": True,
    # Several models share one field name (`status`, `category`) with different choices; give each set a proper name.
    "ENUM_NAME_OVERRIDES": {
        "TripStatusEnum": "core.choices.TripStatus",
        "PaymentModeEnum": "core.choices.PaymentMode",
        "PaymentStatusEnum": "core.choices.PaymentStatus",
        "VehicleCategoryEnum": "core.choices.VehicleCategory",
        "VehicleTypeStatusEnum": "core.choices.VehicleTypeStatus",
        "VehicleStatusEnum": "core.choices.VehicleStatus",
        "VehicleDocumentTypeEnum": "core.choices.VehicleDocumentType",
        "VerificationStatusEnum": "core.choices.VerificationStatus",
        "DriverAccountStatusEnum": "core.choices.DriverAccountStatus",
        "OnboardingStatusEnum": "core.choices.OnboardingStatus",
        "WalletTransactionKindEnum": "core.choices.WalletTransactionKind",
        "ItemVerificationStatusEnum": "core.choices.ItemVerificationStatus",
        "CancelledByEnum": "core.choices.CancelledBy",
        "UploadPurposeEnum": "core.choices.UploadPurpose",
        "KycDecisionEnum": ["verified", "rejected"],
    },
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "core.openapi.hooks.describe_fields",
        "core.openapi.hooks.tidy_examples",
        "core.openapi.hooks.finish_schema",
    ],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiClientToken": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": f"{_BEARER} For **server-to-server integrations**: exchange your client id + secret at `POST /auth/client-token`. Valid 60 minutes; there is no refresh token - request a new one.",
            },
            "AdminToken": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": f"{_BEARER} For the company's **admin panel**: sign in at `POST /auth/login` with email + password; renew with `POST /auth/refresh`.",
            },
            "DriverToken": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": f"{_BEARER} For the **driver app**: the `accessToken` returned by `POST /driver/auth/otp/verify` (renew with `POST /driver/auth/refresh`).",
            },
        }
    },
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "filter": True,
        "docExpansion": "list",
        "defaultModelsExpandDepth": 0,
        "tryItOutEnabled": True,
    },
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

# Payments — how a COD trip's "scan to pay" QR is made and how the money is
# confirmed (trips.payments).
#
#   razorpay    (default) a single-use, fixed-amount UPI QR created through
#               Razorpay's QR Codes API, one per trip. The payment is confirmed
#               by Razorpay — via its signed webhook, or when the driver asks us
#               to check — never on the driver's word alone.
#   upi_static  a plain UPI deep link to COMPANY_UPI_VPA. Nothing confirms the
#               payment (the driver taps "received"). Local development only.
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="razorpay")
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", default="")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", default="")
# Set the same value in Razorpay Dashboard → Webhooks (event: qr_code.credited);
# POST /api/v1/webhooks/razorpay refuses anything not signed with it.
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", default="")
RAZORPAY_API_BASE = env("RAZORPAY_API_BASE", default="https://api.razorpay.com/v1")
RAZORPAY_QR_VALID_MINUTES = env.int("RAZORPAY_QR_VALID_MINUTES", default=30)
RAZORPAY_TIMEOUT_SECONDS = env.int("RAZORPAY_TIMEOUT_SECONDS", default=10)

# The VPA the upi_static stand-in provider makes its QR out to.
COMPANY_UPI_VPA = env("COMPANY_UPI_VPA", default="mob-delivery@upi")
COMPANY_UPI_PAYEE_NAME = env("COMPANY_UPI_PAYEE_NAME", default="MOB Delivery")
