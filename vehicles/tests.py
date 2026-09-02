from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from core.exceptions import DomainError
from drivers.models import Driver, VerificationStatus
from drivers.tokens import issue_driver_token
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


class VehicleDeleteSafetyTests(TestCase):
    """DELETE /vehicles/{id} must go through the same disable_vehicle
    safety check (active-trip refusal, soft delete) as the disable action —
    not a raw instance.delete() that bypasses both."""

    def setUp(self):
        self.company = Company.objects.create(name="Vehicle Delete Safety Co")
        self.admin = AdminUser.objects.create_user(
            email="deletesafety@test.invalid", company=self.company, password="pass12345"
        )
        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def test_delete_soft_deletes_and_deactivates_instead_of_hard_deleting(self):
        vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type,
            registration_number="KA01DS0001", capacity_kg=Decimal("400"),
        )

        r = self.client_api.delete(f"/api/v1/vehicles/{vehicle.id}")
        self.assertEqual(r.status_code, 204)

        vehicle.refresh_from_db()
        self.assertTrue(vehicle.is_deleted)
        self.assertEqual(vehicle.status, "disabled")

    def test_delete_is_refused_with_an_active_trip_same_as_disable(self):
        from trips import services as trip_services

        driver = Driver.objects.create(
            company=self.company, full_name="Delete Safety Driver", phone_number="+919700000001",
            emergency_contact_name="EC", emergency_contact_phone="+919700000002",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type,
            registration_number="KA01DS0002", capacity_kg=Decimal("400"),
        )
        actor = SimpleNamespace(company_id=self.company.id)
        result = trip_services.intake_order(
            company_id=self.company.id, order_ref="ORD-DEL-SAFETY", parent_order_ref=None,
            pickup={"address": "P", "latitude": Decimal("1"), "longitude": Decimal("1")},
            delivery={"address": "D", "latitude": Decimal("1"), "longitude": Decimal("1")},
            weight_kg=Decimal("5.00"), actor=actor,
        )
        trip_services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=vehicle.id, driver_id=driver.id, actor=actor
        )

        r = self.client_api.delete(f"/api/v1/vehicles/{vehicle.id}")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "VEHICLE_HAS_ACTIVE_TRIP")

        vehicle.refresh_from_db()
        self.assertFalse(vehicle.is_deleted, "must not be deleted, soft or hard, when refused")


class PutMethodRemovedTests(TestCase):
    """Data-safety fix — every update spec here was PATCH (partial); PUT
    (full replacement) was only ever reachable as an accidental side effect
    of ModelViewSet's defaults. Confirms it's actually gone, not just that
    the flag was set."""

    def setUp(self):
        self.company = Company.objects.create(name="Put Removed Test Co")
        self.admin = AdminUser.objects.create_user(
            email="putremoved@test.invalid", company=self.company, password="pass12345"
        )
        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type,
            registration_number="KA01PR0001", capacity_kg=Decimal("400"),
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def test_put_on_vehicle_type_is_405(self):
        r = self.client_api.put(
            f"/api/v1/vehicle-types/{self.vehicle_type.id}",
            {"name": "Full Replace Attempt"}, format="json",
        )
        self.assertEqual(r.status_code, 405)

    def test_patch_on_vehicle_type_still_works(self):
        r = self.client_api.patch(
            f"/api/v1/vehicle-types/{self.vehicle_type.id}", {"name": "Renamed Van"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.vehicle_type.refresh_from_db()
        self.assertEqual(self.vehicle_type.name, "Renamed Van")
        self.assertEqual(self.vehicle_type.category, VehicleCategory.FOUR_WHEELER, "untouched by the PATCH")

    def test_put_on_vehicle_is_405(self):
        r = self.client_api.put(
            f"/api/v1/vehicles/{self.vehicle.id}", {"registrationNumber": "KA01PR9999"}, format="json"
        )
        self.assertEqual(r.status_code, 405)

    def test_patch_on_vehicle_still_works_and_leaves_other_fields_alone(self):
        r = self.client_api.patch(
            f"/api/v1/vehicles/{self.vehicle.id}", {"capacityKg": "550.00"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.capacity_kg, Decimal("550.00"))
        self.assertEqual(self.vehicle.registration_number, "KA01PR0001", "untouched by the PATCH")


class VehicleTypeStorageDimensionsTests(TestCase):
    """Vehicle types can be compared by physical cargo space, not just
    weight — storage_length/width/height (+ unit) are optional and
    independent of default_capacity_kg."""

    def setUp(self):
        self.company = Company.objects.create(name="Storage Dims Test Co")
        self.admin = AdminUser.objects.create_user(
            email="storagedims@test.invalid", company=self.company, password="pass12345"
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def _create(self, **extra):
        payload = {"name": "Tata Ace", "category": "four_wheeler", "defaultCapacityKg": "750.00"}
        payload.update(extra)
        return self.client_api.post("/api/v1/vehicle-types", payload, format="json")

    def test_creating_with_only_weight_still_succeeds(self):
        r = self._create()
        self.assertEqual(r.status_code, 201)

        vehicle_type = VehicleType.objects.get(pk=r.data["id"])
        self.assertIsNone(vehicle_type.storage_length)
        self.assertIsNone(vehicle_type.storage_unit)
        self.assertIsNone(vehicle_type.storage_display)

    def test_creating_with_all_three_dimensions_and_unit_computes_storage_display(self):
        r = self._create(storageLength="7", storageWidth="4", storageHeight="5", storageUnit="feet")
        self.assertEqual(r.status_code, 201)
        # r.data is the pre-render dict (snake_case); camelCase only applies
        # to the rendered response.content the client actually receives.
        # Decimal fields render at their configured 2 decimal places.
        self.assertEqual(r.data["storage_display"], "7.00 × 4.00 × 5.00 feet")

        vehicle_type = VehicleType.objects.get(pk=r.data["id"])
        self.assertEqual(vehicle_type.storage_length, Decimal("7.00"))
        self.assertEqual(vehicle_type.storage_unit, "feet")
        self.assertEqual(vehicle_type.storage_display, "7.00 × 4.00 × 5.00 feet")

    def test_omitted_unit_defaults_to_cm_when_dimensions_are_present(self):
        r = self._create(storageLength="40", storageWidth="40", storageHeight="40")
        self.assertEqual(r.status_code, 201)

        vehicle_type = VehicleType.objects.get(pk=r.data["id"])
        self.assertEqual(vehicle_type.storage_unit, "cm")

    def test_partial_dimension_set_is_rejected(self):
        r = self._create(storageLength="7")
        self.assertEqual(r.status_code, 400)
        self.assertIn("storageWidth", r.json()["error"]["details"])

    def test_non_positive_dimension_is_rejected(self):
        r = self._create(storageLength="7", storageWidth="0", storageHeight="5", storageUnit="feet")
        self.assertEqual(r.status_code, 400)

    def test_patch_correcting_one_dimension_of_an_already_complete_set_succeeds(self):
        created = self._create(storageLength="7", storageWidth="4", storageHeight="5", storageUnit="feet")
        vehicle_type_id = created.data["id"]

        r = self.client_api.patch(
            f"/api/v1/vehicle-types/{vehicle_type_id}", {"storageLength": "7.5"}, format="json"
        )
        self.assertEqual(r.status_code, 200)

        vehicle_type = VehicleType.objects.get(pk=vehicle_type_id)
        self.assertEqual(vehicle_type.storage_length, Decimal("7.5"))
        self.assertEqual(vehicle_type.storage_width, Decimal("4"), "untouched by the PATCH")
        self.assertEqual(vehicle_type.storage_unit, "feet", "untouched by the PATCH")

    def test_patch_introducing_a_new_partial_set_is_rejected(self):
        created = self._create()  # no dimensions at all yet
        vehicle_type_id = created.data["id"]

        r = self.client_api.patch(
            f"/api/v1/vehicle-types/{vehicle_type_id}", {"storageLength": "7"}, format="json"
        )
        self.assertEqual(r.status_code, 400)


class DriverCurrentVehicleViewTests(TestCase):
    """GET /driver/vehicle — a driver's own view of the vehicle currently
    assigned to them (Driver.current_vehicle_id), joined with its vehicle
    type. Driver-only, and a clean 404 when nothing is assigned."""

    def setUp(self):
        self.company = Company.objects.create(name="Driver Vehicle Endpoint Co")
        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Tempo", category=VehicleCategory.THREE_WHEELER,
            default_capacity_kg=Decimal("750.00"), icon_image_url="https://x.test/tempo-icon.png",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type,
            registration_number="KA05EE0099", capacity_kg=Decimal("700"),
            photo_url="https://x.test/vehicle-photo.jpg",
        )
        self.driver = Driver.objects.create(
            company=self.company, full_name="Assigned Vehicle Driver", phone_number="+919876500060",
            emergency_contact_name="EC", emergency_contact_phone="+919876500061",
            current_vehicle_id=self.vehicle.id,
        )

    def _authed_driver_client(self, driver):
        access, _refresh, _expires_in = issue_driver_token(driver)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return client

    def test_driver_can_view_their_assigned_vehicle(self):
        client = self._authed_driver_client(self.driver)
        r = client.get("/api/v1/driver/vehicle")
        self.assertEqual(r.status_code, 200)

        body = r.json()
        self.assertEqual(body["id"], str(self.vehicle.id))
        self.assertEqual(body["registrationNumber"], "KA05EE0099")
        self.assertEqual(body["photoUrl"], "https://x.test/vehicle-photo.jpg")
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["vehicleType"]["name"], "Tempo")
        self.assertEqual(body["vehicleType"]["category"], "three_wheeler")
        self.assertEqual(body["vehicleType"]["iconImageUrl"], "https://x.test/tempo-icon.png")

    def test_driver_with_no_vehicle_assigned_gets_404(self):
        unassigned_driver = Driver.objects.create(
            company=self.company, full_name="Unassigned Driver", phone_number="+919876500062",
            emergency_contact_name="EC", emergency_contact_phone="+919876500063",
        )
        client = self._authed_driver_client(unassigned_driver)

        r = client.get("/api/v1/driver/vehicle")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "NO_VEHICLE_ASSIGNED")

    def test_non_driver_principal_is_rejected_with_403(self):
        admin = AdminUser.objects.create_user(
            email="notadriver-vehicle@test.invalid", company=self.company, password="pass12345"
        )
        client = APIClient()
        client.force_authenticate(user=admin)

        r = client.get("/api/v1/driver/vehicle")
        self.assertEqual(r.status_code, 403)

    def test_vehicle_from_another_company_is_not_leaked(self):
        other_company = Company.objects.create(name="Other Vehicle Co")
        other_vehicle_type = VehicleType.objects.create(
            company=other_company, name="Truck", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("2000.00"),
        )
        other_vehicle = Vehicle.objects.create(
            company=other_company, vehicle_type=other_vehicle_type, registration_number="KA10FF0001",
            capacity_kg=Decimal("1900"),
        )
        # Same company as self.driver, but current_vehicle_id points at a
        # vehicle belonging to a *different* company — should 404, not leak.
        self.driver.current_vehicle_id = other_vehicle.id
        self.driver.save(update_fields=["current_vehicle_id"])

        client = self._authed_driver_client(self.driver)
        r = client.get("/api/v1/driver/vehicle")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "NO_VEHICLE_ASSIGNED")
