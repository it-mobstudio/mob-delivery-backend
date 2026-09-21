"""Every non-choice constant in the project (regexes, thresholds, lookup
tables), grouped by the domain that owns it. See core/choices.py for the
TextChoices enums.
"""

import re

# accounts ------------------------------------------------------------------

API_CLIENT_TOKEN_TYPE_CLAIM = "api_client"
DRIVER_TOKEN_TYPE_CLAIM = "driver"

# vehicles --------------------------------------------------------------

REGISTRATION_NUMBER_RE = re.compile(r"^[A-Z0-9-]+$")

# drivers -------------------------------------------------------------------

PHONE_NUMBER_RE = re.compile(r"^\+?[0-9]{10,15}$")
OTP_RE = re.compile(r"^\d{6}$")
OTP_TTL_SECONDS = 300
OTP_THROTTLE_SECONDS = 30
DRIVER_REFRESH_TOKEN_REVOKED_CACHE_PREFIX = "driver_refresh_revoked:"

# Onboarding form rules. The Aadhaar number is only ever *validated* and then
# reduced to its last four digits — see DriverKycService.submit_aadhar.
AADHAR_NUMBER_RE = re.compile(r"^\d{12}$")
DRIVING_LICENCE_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{7,24}$")
PINCODE_RE = re.compile(r"^\d{6}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
BANK_ACCOUNT_NUMBER_RE = re.compile(r"^\d{9,18}$")
UPI_ID_RE = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,64}$")
DRIVER_MIN_AGE_YEARS = 18
DRIVER_MAX_AGE_YEARS = 80

# Vehicles a driver can register for themselves, and pictures per vehicle.
DRIVER_MAX_VEHICLES = 10
VEHICLE_MAX_PHOTOS = 6
# trips -----------------------------------------------------------------

FARE_ROUNDING_CENTS = "0.01"  # str, not Decimal — Decimal isn't module-safe to share

# OTP sent to the drop contact once a COD trip's payment is collected —
# entering it is what lets the driver finalize (complete) the trip. Same
# 6-digit shape as OTP_RE; kept as its own TTL since it's a distinct flow
# from driver login.
DELIVERY_OTP_TTL_SECONDS = 300
# Minimum gap between delivery-OTP (re)sends for one trip, so the resend
# endpoint can't be used to spam the customer's phone.
DELIVERY_OTP_RESEND_THROTTLE_SECONDS = 30

# A booking can carry an item list (and ask the driver to verify each line at
# the drop). Bounded so one request can't create thousands of rows.
MAX_TRIP_ITEMS = 100

# Valhalla costing model per vehicle category (trips.routing) — trucks are
# routed away from truck-restricted city-centre roads (relevant in
# Bengaluru, which has time-windowed truck entry bans), bikes/autos get the
# shorter "no big vehicle" routes.
# https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/#costing-models
CATEGORY_COSTING = {
    "two_wheeler": "motorcycle",
    "three_wheeler": "auto",
    "four_wheeler": "truck",
}
VALHALLA_POLYLINE_PRECISION = 6  # Valhalla's default shape encoding precision.

# core.uploads ----------------------------------------------------------

UPLOAD_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
UPLOAD_DOCUMENT_EXTENSIONS = UPLOAD_IMAGE_EXTENSIONS | {"pdf"}

# Where each upload purpose's files land in the (Azure Blob or local)
# storage tree: {company_id}/{segment}/{uuid}.{ext}
UPLOAD_PURPOSE_PATH_SEGMENT = {
    "vehicle_type_icon": "vehicle-types",
    "vehicle_photo": "vehicles",
    "vehicle_document": "vehicle-documents",
    "driver_document": "driver-documents",
    "driver_photo": "driver-photos",
    "trip_invoice": "trip-invoices",
    "trip_item_image": "trip-items",
    "delivery_proof": "delivery-proofs",
}

UPLOAD_PURPOSE_ALLOWED_EXTENSIONS = {
    "vehicle_type_icon": UPLOAD_IMAGE_EXTENSIONS,
    "vehicle_photo": UPLOAD_IMAGE_EXTENSIONS,
    "vehicle_document": UPLOAD_DOCUMENT_EXTENSIONS,
    "driver_document": UPLOAD_DOCUMENT_EXTENSIONS,
    "driver_photo": UPLOAD_IMAGE_EXTENSIONS,
    "trip_invoice": UPLOAD_DOCUMENT_EXTENSIONS,
    "trip_item_image": UPLOAD_IMAGE_EXTENSIONS,
    "delivery_proof": UPLOAD_IMAGE_EXTENSIONS,
}
