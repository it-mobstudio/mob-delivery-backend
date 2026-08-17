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
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "channels",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
    "core",
    "tenant_settings",
    "accounts",
    "uploads",
    "vehicles",
    "drivers",
    "trips",
    "tracking",
    "damage_reports",
    "dashboard",
    "issues",
    "webhooks",
    "sos",
    "notifications",
    "idempotency",
]

# Firebase Admin SDK (push notifications — see notifications.apps). Path to
# a service account JSON file; never commit the file itself. Left unset in
# dev/test, push notifications are simply disabled (never a hard failure —
# see notifications.services.send_push_to_driver).
FIREBASE_CREDENTIALS_PATH = env("FIREBASE_CREDENTIALS_PATH", default="")

# ASGI — required by Channels (WebSocket live-tracking feed, see
# tracking.consumers.TrackingConsumer). HTTP requests are unaffected; only
# `manage.py runserver` and an ASGI server (daphne/uvicorn) in production
# pick this up. WSGI_APPLICATION above is still used by anything that runs
# the app over plain WSGI.
ASGI_APPLICATION = "mob_delivery.asgi.application"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # As high as possible, and specifically before CommonMiddleware — see
    # django-cors-headers' own installation docs.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# CORS — the Admin Panel is a separate frontend origin from this API, so
# browser requests need an explicit allow-list. Deliberately never
# CORS_ALLOW_ALL_ORIGINS = True: that would let any site read authenticated
# responses. ALLOWED_ADMIN_PANEL_ORIGIN accepts one or more comma-separated
# production origins; localhost variants are only added when DEBUG is on.
CORS_ALLOWED_ORIGINS = env.list("ALLOWED_ADMIN_PANEL_ORIGIN", default=[])
if DEBUG:
    CORS_ALLOWED_ORIGINS = list(CORS_ALLOWED_ORIGINS) + [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
CORS_ALLOW_CREDENTIALS = True

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


# Database — DATABASE_URL-driven; defaults to SQLite if unset.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
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
#
# Renderer/parser default to camelCase at the API boundary only — the Admin
# Panel (mob-delivery-admin) was built against the prior ASP.NET Core API's
# camelCase responses. Every model/serializer field stays snake_case
# (idiomatic Python) internally; djangorestframework-camel-case converts on
# the way out and back on the way in, so nothing elsewhere in the codebase
# needs to change. Query string params (django-filter) are NOT touched by
# this — only request/response bodies.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.JWTMultiPrincipalAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardResultsPagination",
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "EXCEPTION_HANDLER": "core.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
    ]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "DEFAULT_PARSER_CLASSES": [
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
        "djangorestframework_camel_case.parser.CamelCaseFormParser",
        "djangorestframework_camel_case.parser.CamelCaseMultiPartParser",
    ],
    # Fix 6 — per-ApiClient rate limiting. Applied globally (not per-view)
    # so no client-credentials-authenticated endpoint can be missed; it's a
    # no-op for AdminUser/Driver requests (see accounts.throttling).
    "DEFAULT_THROTTLE_CLASSES": [
        "accounts.throttling.ApiClientRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "api_client": env("API_CLIENT_RATE_LIMIT", default="100/min"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("ACCESS_TOKEN_LIFETIME_MINUTES", default=60)),
    # AdminUser refresh token lifetime (7 days) — Driver's own refresh
    # lifetime is separate (DRIVER_REFRESH_TOKEN_LIFETIME_DAYS below, 30
    # days) since drivers may not log in daily; both are hand-built tokens
    # validated generically by simplejwt regardless of this setting's name.
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("REFRESH_TOKEN_LIFETIME_DAYS", default=7)),
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=None) or SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

API_CLIENT_TOKEN_LIFETIME_MINUTES = env.int("API_CLIENT_TOKEN_LIFETIME_MINUTES", default=60)

DRIVER_TOKEN_LIFETIME_MINUTES = env.int("DRIVER_TOKEN_LIFETIME_MINUTES", default=60)
DRIVER_REFRESH_TOKEN_LIFETIME_DAYS = env.int("DRIVER_REFRESH_TOKEN_LIFETIME_DAYS", default=30)

# Non-prod-only: return the generated OTP in the otp/request response body so
# the driver login flow is testable without a real SMS gateway wired in.
# Defaults to DEBUG but is a separate flag so it can be flipped independently
# (e.g. a staging environment that runs DEBUG=False but still wants this).
DRIVER_OTP_DEBUG_RESPONSE = env.bool("DRIVER_OTP_DEBUG_RESPONSE", default=DEBUG)


# ---------------------------------------------------------------------------
# Redis — one instance, three roles, three DB indices. Using a single Redis
# instance for all three is fine at current scale (see the deployment
# README); each role gets its own DB index so their keys can never collide,
# rather than three separate Redis processes:
#   DB 0 — Celery broker + result backend
#   DB 1 — Django cache (driver OTP storage, live vehicle-location snapshots)
#   DB 2 — Channels layer (live-tracking + SOS WebSocket broadcast groups)
# Point each *_URL at a different instance instead if you want physical
# isolation later — nothing else needs to change.
# ---------------------------------------------------------------------------

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/1")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

# Channels layer — backs the live-tracking + SOS WebSocket broadcasts (see
# tracking.consumers.TrackingConsumer, tracking.realtime, sos.consumers,
# sos.realtime). Deliberately a DIFFERENT DB index from CACHES above, not
# the same Redis logical database, per the "separate key prefixes/DB
# indices per role" requirement.
CHANNEL_LAYER_REDIS_URL = env("CHANNEL_LAYER_REDIS_URL", default="redis://localhost:6379/2")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [CHANNEL_LAYER_REDIS_URL]},
    }
}


# Celery — daily DL-expiry-lock task (drivers.tasks), the two anomaly
# detection sweeps (tracking.tasks), webhook dispatch, push notifications,
# and every other periodic task registered in CELERY_BEAT_SCHEDULE below.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

CELERY_BEAT_SCHEDULE = {
    "lock-expired-driver-licenses": {
        "task": "drivers.tasks.lock_expired_driver_licenses",
        "schedule": crontab(hour=0, minute=15),
    },
    "detect-stationary-vehicles": {
        "task": "tracking.tasks.detect_stationary_vehicles",
        "schedule": timedelta(minutes=1),
    },
    "detect-wrong-direction": {
        "task": "tracking.tasks.detect_wrong_direction",
        "schedule": timedelta(minutes=1),
    },
    "dispatch-pending-webhooks": {
        "task": "webhooks.tasks.dispatch_pending_webhooks",
        "schedule": timedelta(seconds=30),
    },
    "cleanup-expired-idempotency-records": {
        "task": "idempotency.tasks.cleanup_expired_idempotency_records",
        "schedule": crontab(minute=0),
    },
    "flag-expiring-vehicle-documents": {
        "task": "vehicles.tasks.flag_expiring_vehicle_documents",
        "schedule": crontab(hour=1, minute=0),
    },
    "purge-old-location-pings": {
        "task": "tracking.tasks.purge_old_location_pings",
        "schedule": crontab(hour=2, minute=0, day_of_week=0),  # weekly, Sunday 2am
    },
}

# Anomaly detection — stationary_radius_meters, stationary_duration_minutes,
# and wrong_direction_degrees moved to per-tenant TenantSetting (Fix 3,
# see tenant_settings.services.get_tenant_setting); tracking.tasks resolves
# those per trip's company now. wrong_direction_min_consecutive_pings stays
# a single global knob — not one of the values that needed to vary per tenant.
ANOMALY_DETECTION_SETTINGS = {
    "wrong_direction_min_consecutive_pings": env.int("ANOMALY_WRONG_DIRECTION_MIN_CONSECUTIVE_PINGS", default=3),
}

# Fix 7 — TripLocationPing data retention (tracking.tasks.purge_old_location_pings).
LOCATION_PING_RETENTION_DAYS = env.int("LOCATION_PING_RETENTION_DAYS", default=90)

SPECTACULAR_SETTINGS = {
    "TITLE": "MOB Delivery Backend API",
    "DESCRIPTION": "Multi-tenant delivery platform API — Company/AdminUser/ApiClient auth, Vehicle module.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Without this, spectacular's auto-detected common path prefix stops at
    # "/api/" (since every URL starts there), so every operation's tag ends
    # up deduced from the next segment, "v1" — one giant flat group in
    # Swagger UI instead of one group per module. Trimming "/api/v1" first
    # makes the tag come from the segment after that (vehicles, drivers,
    # damage-reports, ...).
    "SCHEMA_PATH_PREFIX": "/api/v1",
}
