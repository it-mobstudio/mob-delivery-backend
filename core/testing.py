"""Fixtures shared by the apps' test modules. Not a test module itself
(no `test` prefix), so Django's runner doesn't try to collect it."""

import io
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from itertools import count

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from core.choices import (
    PaymentMode,
    PaymentStatus,
    TripStatus,
    VehicleCategory,
    VehicleStatus,
    VerificationStatus,
)
from drivers.models import Vehicle, VehicleType
from drivers.services import DriverKycService, DriverService
from drivers.tokens import DriverTokenService
from trips.models import Trip

# OTPs and throttles live in Redis in real runs; tests get an in-process
# cache so they neither need Redis nor leak state into a dev instance's.
LOCMEM_CACHES = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    DRIVER_OTP_DEBUG_RESPONSE=True,
    # Tests don't talk to Razorpay; the ones about it say so (trips/test_razorpay.py).
    PAYMENT_PROVIDER="upi_static",
)

_phone_numbers = count(9100000000)
_registrations = count(1)

def image_file(name="scan.jpg", size=(16, 16), color=(200, 120, 40)):
    """A real (tiny) JPEG/PNG upload — UploadService verifies with Pillow, so
    random bytes won't do."""
    buffer = io.BytesIO()
    fmt = "PNG" if name.lower().endswith(".png") else "JPEG"
    Image.new("RGB", size, color).save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def pdf_file(name="invoice.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF", content_type="application/pdf")


def encode_polyline(points, precision=6):
    """Google/Valhalla "encoded polyline" of [(lat, lng), ...] — the format
    trips.routing stores in Trip.route_polyline. Here so tests and the
    local stand-in routing server can produce real shapes."""
    factor = 10**precision
    out, prev_lat, prev_lng = [], 0, 0
    for lat, lng in points:
        lat_i, lng_i = round(lat * factor), round(lng * factor)
        for delta in (lat_i - prev_lat, lng_i - prev_lng):
            value = ~(delta << 1) if delta < 0 else (delta << 1)
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lng = lat_i, lng_i
    return "".join(out)


# What a stubbed RoutingService.get_route hands back: a shape from the
# default pickup to the default drop in make_trip below.
FAKE_ROUTE = {
    "polyline": encode_polyline([(12.975, 77.605), (12.9765, 77.62), (12.9783, 77.6408)]),
    "polyline_precision": 6,
    "distance_meters": 4200,
    "duration_seconds": 780,
}


class DriverTestMixin:
    """Company / vehicle-type / driver / vehicle / trip builders plus an
    authenticated API client, for TestCases in any app."""

    def setUp(self):
        super().setUp()
        cache.clear()
        # Uploads land in a folder of their own that's removed afterwards, so
        # tests never write into (or read from) a dev instance's media/.
        self.media_root = tempfile.mkdtemp(prefix="mob-test-media-")
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self.enterContext(override_settings(MEDIA_ROOT=self.media_root))
        self.company = Company.objects.create(name="Test Co")
        self.vehicle_type = self.make_vehicle_type("Bike", VehicleCategory.TWO_WHEELER)

    # -- builders ----------------------------------------------------------

    def make_vehicle_type(self, name, category, **overrides):
        fields = dict(
            company=self.company,
            name=name,
            category=category,
            default_capacity_kg=20,
            base_fare=30,
            per_km_rate=10,
            per_min_rate=1,
            min_fare=40,
        )
        fields.update(overrides)
        return VehicleType.objects.create(**fields)

    def make_vehicle(self, vehicle_type=None, **overrides):
        vehicle_type = vehicle_type or self.vehicle_type
        fields = dict(
            company=self.company,
            vehicle_type=vehicle_type,
            registration_number=f"KA01T{next(_registrations):04d}",
            capacity_kg=vehicle_type.default_capacity_kg,
            status=VehicleStatus.ACTIVE,
        )
        fields.update(overrides)
        return Vehicle.objects.create(**fields)

    def make_driver(self, *, verified=True, categories=None, **overrides):
        fields = dict(
            full_name="Test Driver",
            phone_number=f"+{next(_phone_numbers)}",
            emergency_contact_name="Emergency",
            emergency_contact_phone="+919999900000",
        )
        fields.update(overrides)
        driver = DriverService.create(self.company, **fields)
        if verified:
            DriverKycService.verify_aadhar(driver, VerificationStatus.VERIFIED, admin_id=None)
            DriverKycService.verify_police(driver, VerificationStatus.VERIFIED, admin_id=None)
            DriverKycService.verify_dl(
                driver,
                VerificationStatus.VERIFIED,
                admin_id=None,
                expiry_date=timezone.localdate() + timedelta(days=365),
                allowed_categories=categories or [VehicleCategory.TWO_WHEELER],
            )
        return driver

    def make_trip(self, driver, vehicle=None, **overrides):
        fields = dict(
            company=self.company,
            vehicle_type=self.vehicle_type,
            driver=driver,
            vehicle=vehicle,
            status=TripStatus.ASSIGNED,
            assigned_at=timezone.now(),
            pickup_address="MG Road, Bengaluru",
            pickup_lat=Decimal("12.975000"),
            pickup_lng=Decimal("77.605000"),
            pickup_contact_name="Shop",
            pickup_contact_phone="+919888800001",
            drop_address="Indiranagar, Bengaluru",
            drop_lat=Decimal("12.978300"),
            drop_lng=Decimal("77.640800"),
            drop_contact_name="Customer",
            drop_contact_phone="+919888800002",
            distance_meters=4200,
            duration_seconds=780,
            route_polyline=FAKE_ROUTE["polyline"],
            polyline_precision=6,
            base_fare=Decimal("30.00"),
            distance_fare=Decimal("42.00"),
            time_fare=Decimal("13.00"),
            total_fare=Decimal("85.00"),
            payment_mode=PaymentMode.COD,
            payment_status=PaymentStatus.PENDING,
        )
        fields.update(overrides)
        return Trip.objects.create(**fields)

    # -- clients -----------------------------------------------------------

    def driver_client(self, driver):
        """An APIClient authenticated as `driver` with a real access token
        (so the whole JWTMultiPrincipalAuthentication path is exercised)."""
        access, _ = DriverTokenService.issue(driver)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return client

    def api_client_client(self, company=None):
        """An APIClient signed in as a machine ApiClient of `company` (the
        client-credentials token a company's backend uses)."""
        from accounts.models import ApiClient
        from accounts.services import ApiClientService

        client = ApiClient(company=company or self.company, name=f"test-{next(_registrations)}")
        client.set_secret("s3cret-value")
        client.save()
        access, _ = ApiClientService.issue_token(client.client_id, "s3cret-value")
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return api

    def admin_client(self, company=None):
        """An APIClient signed in as an admin of `company` (default: the test
        company) — the company's own panel, for booking trips and reviewing
        drivers."""
        admin = AdminUser.objects.create_user(
            email=f"admin{next(_registrations)}@example.com", company=company or self.company, password="x"
        )
        client = APIClient()
        client.force_authenticate(user=admin)
        client.admin = admin
        return client
