from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from core.exceptions import DomainError
from tenant_settings.services import set_tenant_setting

from . import services
from .models import Vehicle, VehicleCategory, VehicleDocument, VehicleDocumentExpiryAlert, VehicleDocumentType, VehicleType
from .tasks import flag_expiring_vehicle_documents


class DocumentExpiryAlertTests(TestCase):
    """Fix 5 — automated vehicle document expiry alerts."""

    def setUp(self):
        self.company = Company.objects.create(name="Document Expiry Test Co")
        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA19XX0001",
            capacity_kg=Decimal("500"),
        )

    def _doc(self, expiry_date, document_type=VehicleDocumentType.INSURANCE):
        return VehicleDocument.objects.create(
            company=self.company, vehicle=self.vehicle, document_type=document_type,
            file_url="https://x.test/doc.pdf", expiry_date=expiry_date,
        )

    def test_document_expiring_within_window_is_flagged(self):
        today = timezone.localdate()
        doc = self._doc(today + timedelta(days=10))

        created = flag_expiring_vehicle_documents()

        self.assertEqual(created, 1)
        alert = VehicleDocumentExpiryAlert.objects.get(document=doc)
        self.assertFalse(alert.acknowledged)
        self.assertEqual(alert.vehicle_id, self.vehicle.id)

    def test_document_expiring_far_in_the_future_is_not_flagged(self):
        today = timezone.localdate()
        self._doc(today + timedelta(days=60))

        created = flag_expiring_vehicle_documents()
        self.assertEqual(created, 0)

    def test_already_expired_document_is_not_flagged(self):
        today = timezone.localdate()
        self._doc(today - timedelta(days=5))

        created = flag_expiring_vehicle_documents()
        self.assertEqual(created, 0)

    def test_document_with_no_expiry_date_is_skipped_without_error(self):
        self._doc(None, document_type=VehicleDocumentType.PURCHASE)
        created = flag_expiring_vehicle_documents()
        self.assertEqual(created, 0)

    def test_rerun_does_not_create_duplicate_alert(self):
        today = timezone.localdate()
        self._doc(today + timedelta(days=5))

        first = flag_expiring_vehicle_documents()
        second = flag_expiring_vehicle_documents()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "must not create a duplicate alert on a second run")

    def test_custom_tenant_warning_window_is_consulted(self):
        today = timezone.localdate()
        self._doc(today + timedelta(days=20))  # outside the default 14-day window

        self.assertEqual(flag_expiring_vehicle_documents(), 0)

        set_tenant_setting(self.company.id, "document_expiry_warning_days", "30")

        self.assertEqual(
            flag_expiring_vehicle_documents(), 1, "widened tenant window should now flag the same document"
        )


class VehiclesNamedTests(TestCase):
    """Production-readiness Part 3, Vehicles section."""

    def setUp(self):
        self.company_a = Company.objects.create(name="Reg Number Test Co A")
        self.company_b = Company.objects.create(name="Reg Number Test Co B")
        self.admin_a = AdminUser.objects.create_user(
            email="vehiclesadmin_a@test.invalid", company=self.company_a, password="pass12345"
        )
        self.vehicle_type_a = VehicleType.objects.create(
            company=self.company_a, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle_type_b = VehicleType.objects.create(
            company=self.company_b, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin_a)

    def _create_vehicle(self, vehicle_type_id, registration_number="KA01AB1234"):
        return self.client_api.post(
            "/api/v1/vehicles",
            {"vehicleTypeId": str(vehicle_type_id), "registrationNumber": registration_number, "capacityKg": "400"},
            format="json",
        )

    def test_registration_number_uniqueness_is_scoped_per_company(self):
        # Another company already has this exact registration number.
        Vehicle.objects.create(
            company=self.company_b, vehicle_type=self.vehicle_type_b,
            registration_number="KA01AB1234", capacity_kg=Decimal("400"),
        )

        r = self._create_vehicle(self.vehicle_type_a.id, "KA01AB1234")
        self.assertEqual(r.status_code, 201, "same reg number under a different company must be allowed")

        r2 = self._create_vehicle(self.vehicle_type_a.id, "KA01AB1234")
        self.assertEqual(r2.status_code, 400, "same reg number within the same company must be rejected")

    def test_deleting_a_vehicle_type_still_in_use_fails_cleanly(self):
        vehicle = Vehicle.objects.create(
            company=self.company_a, vehicle_type=self.vehicle_type_a,
            registration_number="KA01AB9999", capacity_kg=Decimal("400"),
        )

        with self.assertRaises(DomainError) as ctx:
            services.delete_vehicle_type(self.vehicle_type_a)
        self.assertEqual(ctx.exception.code, "VEHICLE_TYPE_IN_USE")

        self.vehicle_type_a.refresh_from_db()
        self.assertFalse(self.vehicle_type_a.is_deleted)
        vehicle.refresh_from_db()  # sanity: the vehicle itself is untouched

    def test_deleting_a_vehicle_type_via_http_returns_a_clean_error_not_a_raw_db_error(self):
        Vehicle.objects.create(
            company=self.company_a, vehicle_type=self.vehicle_type_a,
            registration_number="KA01AB8888", capacity_kg=Decimal("400"),
        )

        r = self.client_api.delete(f"/api/v1/vehicle-types/{self.vehicle_type_a.id}")
        self.assertEqual(r.status_code, 409)

    def test_deleting_an_unused_vehicle_type_succeeds(self):
        unused = VehicleType.objects.create(
            company=self.company_a, name="Unused Type", category=VehicleCategory.TWO_WHEELER,
            default_capacity_kg=Decimal("20.00"),
        )
        services.delete_vehicle_type(unused)
        unused.refresh_from_db()
        self.assertTrue(unused.is_deleted)
