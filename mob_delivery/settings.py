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

if DEBUG:
    # redis-py 5+ defaults to negotiating RESP3 by sending `HELLO 3` on every
    # new connection. The Windows Redis build commonly used for local dev
    # (the old Microsoft-archived port, no official Windows build exists
    # past it) tops out around Redis 5.0, and HELLO wasn't added until Redis
    # 6.0 — so every cache/channel-layer/Celery-broker connection fails with
    # "unknown command `HELLO`" the moment it tries to connect. A real
    # (Linux/Docker/Memurai) Redis 6+ server doesn't have this problem, so
    # this is intentionally DEBUG-only, not a blanket downgrade.
    #
    # There's no single Django/Celery setting that reaches all three
    # consumers (django-redis, channels-redis, and Celery's kombu broker all
    # build their own redis-py connections differently — a `?protocol=2`
    # query string on the URL works for the first two but kombu silently
    # drops unknown query params), so this pins the default at the
    # redis-py library level instead, before anything opens a connection.
    # Three separate module bindings because each does `from .utils import
    # DEFAULT_RESP_VERSION`, copying the name into its own namespace at
    # import time — patching redis.utils alone doesn't reach the others.
    import redis.asyncio.connection
    import redis.connection
    import redis.utils

    redis.connection.DEFAULT_RESP_VERSION = 2
    redis.asyncio.connection.DEFAULT_RESP_VERSION = 2
    redis.utils.DEFAULT_RESP_VERSION = 2


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
    # Serves STATIC_ROOT directly from the Django process — no separate CDN
    # or static-file host needed for a single-web-service deploy (Render).
    # Must sit directly after SecurityMiddleware, before everything else.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

# CORS — the Admin Panel and Driver-app frontends are separate origins from
# this API, so browser requests need an explicit allow-list. Deliberately
# never CORS_ALLOW_ALL_ORIGINS = True: that would let any site read
# authenticated responses. ALLOWED_ADMIN_PANEL_ORIGIN accepts one or more
# comma-separated production origins.
CORS_ALLOWED_ORIGINS = env.list("ALLOWED_ADMIN_PANEL_ORIGIN", default=[])
if DEBUG:
    # Any localhost/127.0.0.1 port, not a fixed list — multiple frontend
    # dev servers running at once (admin panel, driver app, ...) each land
    # on whatever port is free, so a hardcoded port list needs editing every
    # time a new one shows up. Still strictly DEBUG-only, same as the
    # explicit list this replaced — production is unaffected either way.
    CORS_ALLOWED_ORIGIN_REGEXES = [r"^https?://(localhost|127\.0\.0\.1):\d+$"]
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
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
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
    "detect-offline-vehicles": {
        "task": "tracking.tasks.detect_offline_vehicles",
        "schedule": timedelta(minutes=2),
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

# Google Maps — route polylines (trips trip-detail) and pincode geocoding
# (trips order-intake fallback), see maps.services. Left unset in dev/test,
# both features are simply disabled (nulls returned, never a hard failure).
# GOOGLE_ROUTES_API_ENABLED gates trying the newer Routes API first — not
# every Google Cloud project has it turned on yet — before falling back to
# the older, more universally-enabled Directions API.
GOOGLE_MAPS_API_KEY = env("GOOGLE_MAPS_API_KEY", default="")
GOOGLE_ROUTES_API_ENABLED = env.bool("GOOGLE_ROUTES_API_ENABLED", default=False)

SPECTACULAR_SETTINGS = {
    "TITLE": "MOB Delivery Backend API",
    "DESCRIPTION": (
        "Multi-tenant delivery platform API, split below into three audiences by who "
        "calls each endpoint:\n\n"
        "- **Admin: \\*** — the Admin Panel (fleet ops, dispatch, KYC, monitoring). "
        "Callers authenticate as an `AdminUser`.\n"
        "- **Driver: \\*** — the driver mobile app. Callers authenticate as a `Driver` "
        "via phone+OTP login.\n"
        "- **Integrations: \\*** — 3rd-party/partner backends (order intake, order "
        "status). Callers authenticate as an `ApiClient` via client-credentials.\n\n"
        "A handful of endpoints are legitimately used by two audiences (e.g. token "
        "refresh, file upload) — those appear under both of their tags rather than "
        "being force-fit into one."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Every operation below gets an explicit tags=[...] in its @extend_schema /
    # @extend_schema_view (grouped by audience — Admin/Driver/Integrations, see
    # DESCRIPTION above) rather than relying on spectacular's path-based
    # auto-tagging, which would otherwise group everything by URL segment
    # (vehicles, drivers, ...) with no notion of who's meant to call it.
    "SCHEMA_PATH_PREFIX": "/api/v1",
    # Explicit tag order + one-line descriptions so Swagger UI's sidebar lists
    # Admin, then Driver, then Integrations — each internally in a sensible
    # reading order — instead of alphabetical.
    "TAGS": [
        {"name": "Admin: Auth", "description": "Admin login and access-token refresh."},
        {"name": "Admin: Users", "description": "Managing other AdminUser sub-accounts within your own company."},
        {"name": "Admin: Drivers", "description": "Driver roster CRUD."},
        {"name": "Admin: Driver KYC", "description": "Reviewing/approving a driver's Aadhar, police verification, and driving licence."},
        {"name": "Admin: Vehicles", "description": "Vehicle roster CRUD."},
        {"name": "Admin: Vehicle Types", "description": "The vehicle-category master list (Bike, Auto, Tempo, ...)."},
        {"name": "Admin: Vehicle Documents", "description": "Insurance/fitness/RC documents attached to a vehicle."},
        {"name": "Admin: Uploads", "description": "Generic file upload used ahead of a record-creating call."},
        {"name": "Admin: Trips", "description": "Order intake, trip assignment/dispatch, and trip lifecycle."},
        {"name": "Admin: Tracking", "description": "Automated stationary/wrong-direction anomaly alerts."},
        {"name": "Admin: Shifts", "description": "Admin-side, day-level view of driver shift records (separate from individual trips)."},
        {"name": "Admin: Damage Reports", "description": "Company-wide view and resolution of vehicle damage reports."},
        {"name": "Admin: Issues", "description": "Company-wide view and resolution of flagged trip issues."},
        {"name": "Admin: SOS", "description": "SOS alert log, acknowledgement, and resolution."},
        {"name": "Admin: Webhooks", "description": "Outbound webhook delivery log for this company's integrations."},
        {"name": "Admin: Settings", "description": "Per-tenant configurable thresholds (geofence, anomaly detection, ...)."},
        {"name": "Admin: Dashboard", "description": "Fleet status, KPIs, and driver safety-score summaries for the Admin Panel home screen."},
        {"name": "Driver: Auth", "description": "Phone+OTP login for the driver mobile app."},
        {"name": "Driver: Profile", "description": "The logged-in driver's own profile."},
        {"name": "Driver: Uploads", "description": "Generic file upload used ahead of a record-creating call."},
        {"name": "Driver: Trips", "description": "Completing stops, attaching photos, on an assigned trip."},
        {"name": "Driver: Tracking", "description": "Live GPS pings and pause/resume during a trip."},
        {"name": "Driver: Shifts", "description": "Starting/ending a driving shift."},
        {"name": "Driver: Damage Reports", "description": "Reporting damage on the driver's currently assigned vehicle."},
        {"name": "Driver: Issues", "description": "Flagging a problem on a trip mid-delivery."},
        {"name": "Driver: SOS", "description": "Triggering the panic-button alert."},
        {"name": "Driver: Devices", "description": "Registering/deregistering a device for push notifications."},
        {"name": "Integrations: Auth", "description": "Client-credentials token exchange for partner backends."},
        {"name": "Integrations: Orders", "description": "Order intake, cancellation, and status lookup for partner backends."},
    ],
}
