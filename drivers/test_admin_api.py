"""The company-facing management API: the fleet (vehicle types, vehicles and
their documents), the driver roster and KYC review. These are documented as a
public contract, so what the docs say is pinned here."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Company
from core.choices import DriverAccountStatus, TripStatus, VehicleCategory, VehicleStatus, VerificationStatus
from core.testing import LOCMEM_CACHES, DriverTestMixin, image_file
from drivers.models import Driver, Vehicle, VehicleDocument, VehicleType, WalletTransaction
from drivers.services import DriverKycService
from drivers.wallet import WalletService

TYPE = {
    "name": "Tempo",
    "category": "three_wheeler",
    "default_capacity_kg": "500.00",
    "base_fare": "60.00",
    "per_km_rate": "12.00",
    "per_min_rate": "1.50",
    "min_fare": "80.00",
}


@LOCMEM_CACHES
class FleetAccessTests(DriverTestMixin, TestCase):
    """The fleet belongs to the company — a driver's token must not be able to
    change it, whatever company the driver works for."""

    ENDPOINTS = ["/api/v1/vehicle-types", "/api/v1/vehicles"]

    def test_a_driver_token_is_refused_everywhere_on_the_fleet_and_uploads(self):
        driver_client = self.driver_client(self.make_driver())
        vehicle = self.make_vehicle()

        for url in self.ENDPOINTS + [f"/api/v1/vehicles/{vehicle.id}/documents", f"/api/v1/vehicles/{vehicle.id}"]:
            self.assertEqual(driver_client.get(url).status_code, 403, url)
        self.assertEqual(driver_client.post("/api/v1/vehicle-types", TYPE, format="json").status_code, 403)
        self.assertEqual(driver_client.delete(f"/api/v1/vehicles/{vehicle.id}").status_code, 403)
        upload = driver_client.post(
            "/api/v1/uploads", {"file": image_file("x.jpg"), "purpose": "vehicle_photo"}, format="multipart"
        )
        self.assertEqual(upload.status_code, 403)
        self.assertTrue(Vehicle.objects.filter(pk=vehicle.pk).exists())

    def test_no_token_is_a_401(self):
        for url in self.ENDPOINTS:
            self.assertEqual(APIClient().get(url).status_code, 401, url)

    def test_both_kinds_of_company_credential_are_accepted(self):
        for client in (self.admin_client(), self.api_client_client()):
            for url in self.ENDPOINTS:
                self.assertEqual(client.get(url).status_code, 200, url)


@LOCMEM_CACHES
class VehicleTypeApiTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_ = self.admin_client()

    def test_create_and_read_back_a_vehicle_type_with_its_fare_card(self):
        response = self.client_.post("/api/v1/vehicle-types", TYPE, format="json")

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["name"], body["category"], body["status"]), ("Tempo", "three_wheeler", "active"))
        self.assertEqual((body["base_fare"], body["per_km_rate"], body["min_fare"]), ("60.00", "12.00", "80.00"))
        self.assertEqual(self.client_.get(f"/api/v1/vehicle-types/{body['id']}").json()["name"], "Tempo")

    def test_only_the_name_category_and_capacity_are_required(self):
        response = self.client_.post(
            "/api/v1/vehicle-types",
            {"name": "Cargo bike", "category": "two_wheeler", "default_capacity_kg": "25"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["min_fare"], "0.00", "no fare card yet — trips can't be booked against it")

        missing = self.client_.post("/api/v1/vehicle-types", {"name": "X"}, format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(set(missing.json()["error"]["details"]), {"category", "default_capacity_kg"})

    def test_values_are_validated(self):
        for field, value in (("default_capacity_kg", "0"), ("base_fare", "-1"), ("per_km_rate", "-0.5"), ("min_fare", "-10"), ("category", "spaceship")):
            response = self.client_.post("/api/v1/vehicle-types", {**TYPE, field: value}, format="json")
            self.assertEqual(response.status_code, 400, field)
            self.assertIn(field, response.json()["error"]["details"])

    def test_names_are_unique_per_company(self):
        self.assertEqual(self.client_.post("/api/v1/vehicle-types", {**TYPE, "name": "Bike"}, format="json").status_code, 400)
        other = self.admin_client(Company.objects.create(name="Other Co"))
        self.assertEqual(other.post("/api/v1/vehicle-types", {**TYPE, "name": "Bike"}, format="json").status_code, 201)

    def test_list_filters_by_category_and_status(self):
        self.make_vehicle_type("Auto", VehicleCategory.THREE_WHEELER)
        self.make_vehicle_type("Truck", VehicleCategory.FOUR_WHEELER, status="inactive")

        names = lambda **q: sorted(r["name"] for r in self.client_.get("/api/v1/vehicle-types", q).json()["results"])
        self.assertEqual(names(), ["Auto", "Bike", "Truck"])
        self.assertEqual(names(category="three_wheeler"), ["Auto"])
        self.assertEqual(names(status="inactive"), ["Truck"])

    def test_update_and_delete(self):
        vt = self.make_vehicle_type("Van", VehicleCategory.FOUR_WHEELER)
        patched = self.client_.patch(f"/api/v1/vehicle-types/{vt.id}", {"per_km_rate": "18.50"}, format="json")
        self.assertEqual(patched.json()["per_km_rate"], "18.50")

        self.assertEqual(self.client_.delete(f"/api/v1/vehicle-types/{vt.id}").status_code, 204)
        self.assertEqual(self.client_.get(f"/api/v1/vehicle-types/{vt.id}").status_code, 404)

    def test_a_type_still_in_use_cannot_be_deleted(self):
        self.make_vehicle()  # a Bike on the road
        response = self.client_.delete(f"/api/v1/vehicle-types/{self.vehicle_type.id}")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "VEHICLE_TYPE_IN_USE")

    def test_another_companys_types_are_invisible(self):
        stranger = self.admin_client(Company.objects.create(name="Other Co"))
        self.assertEqual(stranger.get(f"/api/v1/vehicle-types/{self.vehicle_type.id}").status_code, 404)
        self.assertEqual(stranger.get("/api/v1/vehicle-types").json()["count"], 0)


@LOCMEM_CACHES
class VehicleApiTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_ = self.admin_client()

    def create(self, **overrides):
        return self.client_.post(
            "/api/v1/vehicles",
            {"vehicle_type_id": str(self.vehicle_type.id), "registration_number": "ka01ab1234", **overrides},
            format="json",
        )

    def test_create_normalises_the_registration_and_defaults_the_capacity(self):
        response = self.create()

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["registration_number"], "KA01AB1234")
        self.assertEqual(body["capacity_kg"], "20.00", "the vehicle type's default")
        self.assertEqual((body["status"], body["current_driver_id"]), ("active", None))
        self.assertNotIn("vehicle_type_id", body, "write-only: reads come back as a nested vehicle_type")

    def test_capacity_can_be_overridden(self):
        self.assertEqual(self.create(capacity_kg="35.5").json()["capacity_kg"], "35.50")

    def test_who_is_driving_is_kept_by_duty_and_cannot_be_written(self):
        someone = "0b1c3a52-6f0e-4d3e-9a55-1f2b7c9d4e10"
        created = self.create(current_driver_id=someone)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertIsNone(created.json()["current_driver_id"], "ignored on create")

        patched = self.client_.patch(f"/api/v1/vehicles/{created.json()['id']}", {"current_driver_id": someone}, format="json")
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertIsNone(patched.json()["current_driver_id"], "ignored on update")

    def test_the_input_is_validated(self):
        self.assertEqual(self.create(registration_number="ka 01 ab").status_code, 400)  # spaces
        self.assertEqual(self.create(capacity_kg="0").status_code, 400)
        self.assertEqual(self.create(vehicle_type_id="00000000-0000-0000-0000-000000000000").status_code, 400)
        self.assertEqual(self.client_.post("/api/v1/vehicles", {}, format="json").status_code, 400)

    def test_a_registration_is_unique_within_the_company(self):
        self.assertEqual(self.create().status_code, 201)
        duplicate = self.create(registration_number="KA01AB1234")
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("registration_number", duplicate.json()["error"]["details"])

    def test_an_inactive_vehicle_type_cannot_be_used(self):
        inactive = self.make_vehicle_type("Old", VehicleCategory.TWO_WHEELER, status="inactive")
        response = self.create(vehicle_type_id=str(inactive.id))
        self.assertEqual(response.status_code, 400)

    def test_another_companys_vehicle_type_cannot_be_used(self):
        foreign = VehicleType.objects.create(
            company=Company.objects.create(name="Other Co"), name="Bike", category="two_wheeler", default_capacity_kg=10
        )
        self.assertEqual(self.create(vehicle_type_id=str(foreign.id)).status_code, 400)

    def test_list_filters_and_search(self):
        bike = self.make_vehicle(registration_number="KA01BIKE0001")
        truck = self.make_vehicle(self.make_vehicle_type("Truck", VehicleCategory.FOUR_WHEELER), registration_number="KA02TRUCK001")
        self.make_vehicle(registration_number="KA03MAINT001", status=VehicleStatus.MAINTENANCE)

        regs = lambda **q: sorted(r["registration_number"] for r in self.client_.get("/api/v1/vehicles", q).json()["results"])
        self.assertEqual(len(regs()), 3)
        self.assertEqual(regs(category="four_wheeler"), ["KA02TRUCK001"])
        self.assertEqual(regs(status="maintenance"), ["KA03MAINT001"])
        self.assertEqual(regs(search="bike"), ["KA01BIKE0001"])
        row = self.client_.get("/api/v1/vehicles", {"search": "TRUCK"}).json()["results"][0]
        self.assertEqual(row["vehicle_type"]["category"], "four_wheeler", "list rows nest the vehicle type")
        self.assertNotIn("documents", row)
        self.assertEqual(truck.registration_number, "KA02TRUCK001")
        self.assertEqual(bike.registration_number, "KA01BIKE0001")

    def test_the_detail_view_includes_documents(self):
        vehicle = self.make_vehicle()
        VehicleDocument.objects.create(company=self.company, vehicle=vehicle, document_type="rc", file_url="https://files.example.com/rc.pdf")
        body = self.client_.get(f"/api/v1/vehicles/{vehicle.id}").json()
        self.assertEqual([d["document_type"] for d in body["documents"]], ["rc"])

    def test_disable_retires_the_vehicle(self):
        vehicle = self.make_vehicle()
        response = self.client_.post(f"/api/v1/vehicles/{vehicle.id}/disable")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "disabled")
        self.assertEqual(self.client_.get(f"/api/v1/vehicles/{vehicle.id}").status_code, 404, "a disabled vehicle drops out of the fleet")

    def test_a_vehicle_on_a_trip_cannot_be_disabled(self):
        vehicle = self.make_vehicle()
        self.make_trip(self.make_driver(), vehicle, status=TripStatus.IN_PROGRESS)
        response = self.client_.post(f"/api/v1/vehicles/{vehicle.id}/disable")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "VEHICLE_HAS_ACTIVE_TRIP")

    def test_patch_changes_only_what_is_sent_and_delete_removes_it(self):
        vehicle = self.make_vehicle()
        self.assertEqual(self.client_.patch(f"/api/v1/vehicles/{vehicle.id}", {"capacity_kg": "42"}, format="json").json()["capacity_kg"], "42.00")
        self.assertEqual(self.client_.delete(f"/api/v1/vehicles/{vehicle.id}").status_code, 204)
        self.assertFalse(Vehicle.objects.filter(pk=vehicle.pk).exists())

    def test_another_companys_vehicles_are_invisible(self):
        vehicle = self.make_vehicle()
        stranger = self.admin_client(Company.objects.create(name="Other Co"))
        self.assertEqual(stranger.get(f"/api/v1/vehicles/{vehicle.id}").status_code, 404)
        self.assertEqual(stranger.delete(f"/api/v1/vehicles/{vehicle.id}").status_code, 404)


@LOCMEM_CACHES
class VehicleDocumentApiTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_ = self.admin_client()
        self.vehicle = self.make_vehicle()
        self.url = f"/api/v1/vehicles/{self.vehicle.id}/documents"

    def test_add_list_and_update_a_document(self):
        expiry = (date.today() + timedelta(days=200)).isoformat()
        created = self.client_.post(self.url, {"document_type": "insurance", "file_url": "https://files.example.com/ins.pdf", "expiry_date": expiry}, format="json")
        self.assertEqual(created.status_code, 201, created.content)

        listed = self.client_.get(self.url).json()
        self.assertEqual(listed["count"], 1)
        renewed = (date.today() + timedelta(days=565)).isoformat()
        patched = self.client_.patch(f"{self.url}/{created.json()['id']}", {"expiry_date": renewed}, format="json")
        self.assertEqual(patched.json()["expiry_date"], renewed)

    def test_insurance_and_fitness_need_an_expiry_date_but_an_rc_does_not(self):
        for kind in ("insurance", "fitness"):
            response = self.client_.post(self.url, {"document_type": kind, "file_url": "https://files.example.com/a.pdf"}, format="json")
            self.assertEqual(response.status_code, 400, kind)
            self.assertIn("expiry_date", response.json()["error"]["details"])
        rc = self.client_.post(self.url, {"document_type": "rc", "file_url": "https://files.example.com/rc.pdf"}, format="json")
        self.assertEqual(rc.status_code, 201)

    def test_documents_cannot_be_deleted_or_replaced_wholesale(self):
        doc = VehicleDocument.objects.create(company=self.company, vehicle=self.vehicle, document_type="rc", file_url="https://files.example.com/rc.pdf")
        self.assertEqual(self.client_.delete(f"{self.url}/{doc.id}").status_code, 405)
        self.assertEqual(self.client_.put(f"{self.url}/{doc.id}", {}, format="json").status_code, 405)

    def test_another_companys_vehicle_is_a_404(self):
        stranger = self.admin_client(Company.objects.create(name="Other Co"))
        self.assertEqual(stranger.get(self.url).status_code, 404)
        self.assertEqual(stranger.post(self.url, {"document_type": "rc", "file_url": "https://x.example.com/a.pdf"}, format="json").status_code, 404)


@LOCMEM_CACHES
class DriverRosterApiTests(DriverTestMixin, TestCase):
    """Drivers the company creates and manages. Admin panel only: a machine
    ApiClient can book trips but can't manage people."""

    PAYLOAD = {
        "full_name": "Meena Rao",
        "phone_number": "+919777700001",
        "emergency_contact_name": "Ravi Rao",
        "emergency_contact_phone": "+919777700002",
    }

    def setUp(self):
        super().setUp()
        self.admin = self.admin_client()

    def test_create_a_driver_with_a_pending_kyc(self):
        response = self.admin.post("/api/v1/drivers", self.PAYLOAD, format="json")

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["full_name"], body["account_status"]), ("Meena Rao", "active"))
        self.assertEqual((body["aadhar_status"], body["dl_status"], body["police_status"]), ("pending",) * 3)
        # The company gave a name and emergency contact but not a date of birth,
        # so the driver still starts their own profile step in the app.
        self.assertEqual(body["onboarding_status"], "profile_incomplete")
        self.assertTrue(Driver.objects.get(phone_number="+919777700001").kyc)

    def test_every_field_of_the_payload_is_required_and_the_phone_is_checked(self):
        missing = self.admin.post("/api/v1/drivers", {}, format="json")
        self.assertEqual(set(missing.json()["error"]["details"]), set(self.PAYLOAD))
        bad = self.admin.post("/api/v1/drivers", {**self.PAYLOAD, "phone_number": "12345"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_a_phone_number_is_unique_within_the_company(self):
        self.admin.post("/api/v1/drivers", self.PAYLOAD, format="json")
        again = self.admin.post("/api/v1/drivers", self.PAYLOAD, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("phone_number", again.json()["error"]["details"])

    def test_list_filters_and_search(self):
        verified = self.make_driver(full_name="Verified Vic")
        pending = self.make_driver(full_name="Pending Pat", verified=False)

        ids = lambda **q: {r["id"] for r in self.admin.get("/api/v1/drivers", q).json()["results"]}
        self.assertEqual(ids(), {str(verified.id), str(pending.id)})
        self.assertEqual(ids(aadhar_status="verified"), {str(verified.id)})
        self.assertEqual(ids(dl_status="pending"), {str(pending.id)})
        self.assertEqual(ids(search="pat"), {str(pending.id)})
        self.assertEqual(ids(search=verified.phone_number), {str(verified.id)})
        row = self.admin.get("/api/v1/drivers", {"search": "vic"}).json()["results"][0]
        self.assertTrue(row["is_eligible_for_assignment"])

    def test_patch_and_put(self):
        driver = self.make_driver()
        self.assertEqual(self.admin.patch(f"/api/v1/drivers/{driver.id}", {"full_name": "New Name"}, format="json").json()["full_name"], "New Name")
        self.assertEqual(self.admin.put(f"/api/v1/drivers/{driver.id}", {"full_name": "Only"}, format="json").status_code, 400, "PUT needs every field")

    def test_status_and_kyc_cannot_be_changed_through_the_profile_endpoints(self):
        driver = self.make_driver(verified=False)
        self.admin.patch(f"/api/v1/drivers/{driver.id}", {"account_status": "disabled", "aadhar_status": "verified"}, format="json")
        driver.refresh_from_db()
        self.assertEqual(driver.account_status, DriverAccountStatus.ACTIVE)
        self.assertEqual(driver.kyc.aadhar_status, VerificationStatus.PENDING)

    def test_api_clients_cannot_manage_drivers(self):
        machine = self.api_client_client()
        self.assertEqual(machine.get("/api/v1/drivers").status_code, 403)
        self.assertEqual(machine.post("/api/v1/drivers", self.PAYLOAD, format="json").status_code, 403)

    def test_another_companys_drivers_are_invisible(self):
        driver = self.make_driver()
        stranger = self.admin_client(Company.objects.create(name="Other Co"))
        self.assertEqual(stranger.get(f"/api/v1/drivers/{driver.id}").status_code, 404)
        self.assertEqual(stranger.get("/api/v1/drivers").json()["count"], 0)

    def test_disable_takes_a_driver_off_the_roster_but_keeps_their_history(self):
        driver = self.make_driver()
        driver.is_online = True
        driver.save(update_fields=["is_online"])

        response = self.admin.post(f"/api/v1/drivers/{driver.id}/disable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_status"], "disabled")
        self.assertEqual(self.admin.get(f"/api/v1/drivers/{driver.id}").status_code, 404)
        driver = Driver.all_objects.get(pk=driver.pk)
        self.assertFalse(driver.is_online)

    def test_delete_is_the_same_soft_retirement_and_survives_a_wallet(self):
        driver = self.make_driver()
        WalletService.record_manual(driver, "bonus", Decimal("50"))  # a ledger row PROTECTs the driver

        response = self.admin.delete(f"/api/v1/drivers/{driver.id}")

        self.assertEqual(response.status_code, 204, "a hard delete would have been refused by the ledger")
        self.assertEqual(self.admin.get(f"/api/v1/drivers/{driver.id}").status_code, 404)
        self.assertEqual(WalletTransaction.objects.filter(driver_id=driver.id).count(), 1, "the statement is kept")

    def test_a_driver_on_a_trip_cannot_be_disabled_or_deleted(self):
        driver = self.make_driver()
        self.make_trip(driver)
        for call in (
            lambda: self.admin.post(f"/api/v1/drivers/{driver.id}/disable"),
            lambda: self.admin.delete(f"/api/v1/drivers/{driver.id}"),
        ):
            response = call()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "DRIVER_HAS_ACTIVE_TRIP")


@LOCMEM_CACHES
class KycReviewApiTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_client()
        self.driver = self.make_driver(verified=False)
        self.base = f"/api/v1/drivers/{self.driver.id}/kyc"

    def test_the_kyc_record_starts_all_pending(self):
        body = self.admin.get(self.base).json()
        self.assertEqual((body["aadhar_status"], body["dl_status"], body["police_status"]), ("pending",) * 3)
        self.assertEqual(body["dl_allowed_categories"], [])

    def test_verifying_aadhar_and_police(self):
        for part in ("aadhar", "police"):
            response = self.admin.patch(f"{self.base}/{part}", {"status": "verified"}, format="json")
            self.assertEqual(response.status_code, 200, part)
            self.assertEqual(response.json()[f"{part}_status"], "verified")
            self.assertEqual(response.json()[f"{part}_verified_by"], str(self.admin.admin.id))

    def test_a_rejection_needs_a_reason_and_the_driver_sees_it(self):
        blank = self.admin.patch(f"{self.base}/aadhar", {"status": "rejected"}, format="json")
        self.assertEqual(blank.status_code, 400)
        self.assertIn("note", blank.json()["error"]["details"])

        self.admin.patch(f"{self.base}/aadhar", {"status": "rejected", "note": "Blurry"}, format="json")
        me = self.driver_client(self.driver).get("/api/v1/driver/me").json()
        self.assertEqual(me["aadhar_rejection_note"], "Blurry")

    def test_only_verified_or_rejected_are_valid_decisions(self):
        self.assertEqual(self.admin.patch(f"{self.base}/aadhar", {"status": "pending"}, format="json").status_code, 400)

    def test_verifying_a_licence_needs_a_future_expiry_and_the_categories_it_covers(self):
        self.assertEqual(self.admin.patch(f"{self.base}/dl", {"status": "verified"}, format="json").status_code, 400)
        past = self.admin.patch(f"{self.base}/dl", {"status": "verified", "expiry_date": "2020-01-01", "allowed_categories": ["two_wheeler"]}, format="json")
        self.assertEqual(past.status_code, 400)
        self.assertIn("expiry_date", past.json()["error"]["details"])
        no_categories = self.admin.patch(f"{self.base}/dl", {"status": "verified", "expiry_date": (date.today() + timedelta(days=90)).isoformat()}, format="json")
        self.assertIn("allowed_categories", no_categories.json()["error"]["details"])

        expiry = (date.today() + timedelta(days=90)).isoformat()
        ok = self.admin.patch(f"{self.base}/dl", {"status": "verified", "expiry_date": expiry, "allowed_categories": ["two_wheeler", "three_wheeler"]}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual((ok.json()["dl_expiry_date"], ok.json()["dl_allowed_categories"]), (expiry, ["two_wheeler", "three_wheeler"]))

    def test_re_verifying_the_licence_unlocks_an_account_locked_for_expiry(self):
        self.driver.account_status = DriverAccountStatus.LOCKED_DL_EXPIRED
        self.driver.save(update_fields=["account_status"])

        self.admin.patch(
            f"{self.base}/dl",
            {"status": "verified", "expiry_date": (date.today() + timedelta(days=365)).isoformat(), "allowed_categories": ["two_wheeler"]},
            format="json",
        )

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.account_status, DriverAccountStatus.ACTIVE)

    def test_full_approval_makes_a_driver_eligible(self):
        expiry = (date.today() + timedelta(days=365)).isoformat()
        self.admin.patch(f"{self.base}/aadhar", {"status": "verified"}, format="json")
        self.admin.patch(f"{self.base}/police", {"status": "verified"}, format="json")
        self.admin.patch(f"{self.base}/dl", {"status": "verified", "expiry_date": expiry, "allowed_categories": ["two_wheeler"]}, format="json")
        self.assertTrue(Driver.objects.get(pk=self.driver.pk).is_eligible_for_assignment)
        row = next(r for r in self.admin.get("/api/v1/drivers").json()["results"] if r["id"] == str(self.driver.id))
        self.assertTrue(row["is_eligible_for_assignment"])
        self.assertEqual(row["onboarding_status"], "approved", "verified drivers are approved whatever their profile lacks")

    def test_a_driver_or_api_client_cannot_review(self):
        self.assertEqual(self.driver_client(self.driver).patch(f"{self.base}/aadhar", {"status": "verified"}, format="json").status_code, 403)
        self.assertEqual(self.api_client_client().patch(f"{self.base}/aadhar", {"status": "verified"}, format="json").status_code, 403)
        self.assertEqual(self.api_client_client().get(self.base).status_code, 403)
