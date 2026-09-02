import json
import shutil
import tempfile
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from .models import Driver, DriverAccountStatus, VerificationStatus
from .tokens import issue_driver_token


def _body(response):
    # response.data is the PRE-render dict — only response.content reflects
    # the camelCase keys the client actually receives on the wire.
    return json.loads(response.content)


class DriverRefreshTokenTests(TestCase):
    """Fix 4 — JWT refresh tokens (Driver side)."""

    def setUp(self):
        self.company = Company.objects.create(name="Driver Auth Test Co")
        self.driver = Driver.objects.create(
            company=self.company, full_name="Refresh Driver", phone_number="+919444000001",
            emergency_contact_name="EC", emergency_contact_phone="+919444000002",
        )

    def test_issue_driver_token_returns_access_and_refresh(self):
        access, refresh, expires_in = issue_driver_token(self.driver)
        self.assertTrue(access)
        self.assertTrue(refresh)
        self.assertNotEqual(access, refresh)
        self.assertEqual(expires_in, settings.DRIVER_TOKEN_LIFETIME_MINUTES * 60)

    def test_otp_verify_response_includes_refresh_token(self):
        client = APIClient()
        r1 = client.post(
            "/api/v1/driver/auth/otp/request", {"phone_number": self.driver.phone_number}, format="json"
        )
        self.assertEqual(r1.status_code, 200)
        otp = r1.data.get("otp")
        self.assertTrue(otp, "DRIVER_OTP_DEBUG_RESPONSE must be enabled for this test env")

        r2 = client.post(
            "/api/v1/driver/auth/otp/verify", {"phone_number": self.driver.phone_number, "otp": otp}, format="json"
        )
        self.assertEqual(r2.status_code, 200)
        self.assertIn("refreshToken", _body(r2))

    def test_driver_refresh_token_yields_a_working_new_access_token(self):
        access, refresh, expires_in = issue_driver_token(self.driver)

        client = APIClient()
        r = client.post("/api/v1/auth/refresh", {"refreshToken": refresh}, format="json")
        self.assertEqual(r.status_code, 200)
        access_token = _body(r)["accessToken"]

        authed_client = APIClient()
        authed_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        me = authed_client.get("/api/v1/driver/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(_body(me)["id"], str(self.driver.id))


class DriverEligibilityTests(TestCase):
    """Production-readiness Part 3, Drivers section — is_eligible_for_assignment."""

    def setUp(self):
        self.company = Company.objects.create(name="Eligibility Test Co")
        self.driver = Driver.objects.create(
            company=self.company, full_name="Eligible Driver", phone_number="+919876543210",
            emergency_contact_name="EC", emergency_contact_phone="+919876500000",
            aadhar_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED,
            police_status=VerificationStatus.VERIFIED,
            account_status=DriverAccountStatus.ACTIVE,
            dl_expiry_date=date.today() + timedelta(days=30),
        )

    def test_fully_verified_active_driver_with_future_dl_expiry_is_eligible(self):
        self.assertTrue(self.driver.is_eligible_for_assignment)

    def test_ineligible_if_aadhar_not_verified(self):
        self.driver.aadhar_status = VerificationStatus.PENDING
        self.assertFalse(self.driver.is_eligible_for_assignment)

    def test_ineligible_if_dl_not_verified(self):
        self.driver.dl_status = VerificationStatus.REJECTED
        self.assertFalse(self.driver.is_eligible_for_assignment)

    def test_ineligible_if_police_not_verified(self):
        self.driver.police_status = VerificationStatus.PENDING
        self.assertFalse(self.driver.is_eligible_for_assignment)

    def test_ineligible_if_account_is_locked(self):
        self.driver.account_status = DriverAccountStatus.LOCKED_DL_EXPIRED
        self.assertFalse(self.driver.is_eligible_for_assignment)

    def test_ineligible_if_account_is_disabled(self):
        self.driver.account_status = DriverAccountStatus.DISABLED
        self.assertFalse(self.driver.is_eligible_for_assignment)

    def test_ineligible_if_dl_has_expired(self):
        self.driver.dl_expiry_date = date.today() - timedelta(days=1)
        self.assertFalse(self.driver.is_eligible_for_assignment)

    def test_eligible_when_dl_expiry_date_is_unset(self):
        self.driver.dl_expiry_date = None
        self.assertTrue(self.driver.is_eligible_for_assignment)


class OtpLoginFlowTests(TestCase):
    """Production-readiness Part 3, Drivers section — OTP login end to end."""

    def setUp(self):
        self.company = Company.objects.create(name="OTP Loop Test Co")
        self.driver = Driver.objects.create(
            company=self.company, full_name="OTP Driver", phone_number="+919876500011",
            emergency_contact_name="EC", emergency_contact_phone="+919876500012",
        )
        cache.clear()

    def test_request_then_verify_loop_works_end_to_end(self):
        client = APIClient()
        r1 = client.post(
            "/api/v1/driver/auth/otp/request", {"phone_number": self.driver.phone_number}, format="json"
        )
        self.assertEqual(r1.status_code, 200)
        otp = _body(r1)["otp"]

        r2 = client.post(
            "/api/v1/driver/auth/otp/verify", {"phone_number": self.driver.phone_number, "otp": otp}, format="json"
        )
        self.assertEqual(r2.status_code, 200)
        self.assertIn("accessToken", _body(r2))

    def test_locked_account_cannot_request_an_otp(self):
        self.driver.account_status = DriverAccountStatus.LOCKED_DL_EXPIRED
        self.driver.save(update_fields=["account_status"])

        r = APIClient().post(
            "/api/v1/driver/auth/otp/request", {"phone_number": self.driver.phone_number}, format="json"
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(_body(r)["error"]["code"], "ACCOUNT_LOCKED")

    def test_wrong_otp_is_rejected(self):
        client = APIClient()
        r1 = client.post(
            "/api/v1/driver/auth/otp/request", {"phone_number": self.driver.phone_number}, format="json"
        )
        real_otp = _body(r1)["otp"]
        wrong_otp = "111111" if real_otp != "111111" else "222222"

        r2 = client.post(
            "/api/v1/driver/auth/otp/verify",
            {"phone_number": self.driver.phone_number, "otp": wrong_otp},
            format="json",
        )
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(_body(r2)["error"]["code"], "INVALID_OTP")

    def test_expired_otp_is_rejected(self):
        client = APIClient()
        r1 = client.post(
            "/api/v1/driver/auth/otp/request", {"phone_number": self.driver.phone_number}, format="json"
        )
        otp = _body(r1)["otp"]
        cache.delete(f"driver_otp:{self.driver.phone_number}")  # simulate the OTP's TTL having elapsed

        r2 = client.post(
            "/api/v1/driver/auth/otp/verify", {"phone_number": self.driver.phone_number, "otp": otp}, format="json"
        )
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(_body(r2)["error"]["code"], "INVALID_OTP")


class DlExpiryLockTaskTests(TestCase):
    """Production-readiness Part 3, Drivers section — the DL-expiry Celery
    Beat task and its unlock counterpart in drivers.services.verify_dl.
    """

    def setUp(self):
        self.company = Company.objects.create(name="DL Expiry Lock Test Co")
        self.admin_id = uuid.uuid4()  # verify_dl only stores the id, doesn't require a real AdminUser row
        self.driver = Driver.objects.create(
            company=self.company, full_name="Expiring Driver", phone_number="+919876500021",
            emergency_contact_name="EC", emergency_contact_phone="+919876500022",
            dl_status=VerificationStatus.VERIFIED,
            dl_expiry_date=date.today() - timedelta(days=1),
            account_status=DriverAccountStatus.ACTIVE,
        )

    def test_task_locks_a_driver_whose_dl_has_expired(self):
        from .tasks import lock_expired_driver_licenses

        locked_count = lock_expired_driver_licenses()

        self.driver.refresh_from_db()
        self.assertEqual(locked_count, 1)
        self.assertEqual(self.driver.account_status, DriverAccountStatus.LOCKED_DL_EXPIRED)

    def test_task_does_not_touch_a_driver_with_a_future_dl_expiry(self):
        from .tasks import lock_expired_driver_licenses

        self.driver.dl_expiry_date = date.today() + timedelta(days=10)
        self.driver.save(update_fields=["dl_expiry_date"])

        lock_expired_driver_licenses()

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.account_status, DriverAccountStatus.ACTIVE)

    def test_reverifying_dl_with_a_future_expiry_date_unlocks_the_driver(self):
        from . import services
        from .tasks import lock_expired_driver_licenses

        lock_expired_driver_licenses()
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.account_status, DriverAccountStatus.LOCKED_DL_EXPIRED)

        services.verify_dl(
            self.driver,
            status=VerificationStatus.VERIFIED,
            admin_id=self.admin_id,
            expiry_date=date.today() + timedelta(days=365),
            allowed_categories=["four_wheeler"],
        )

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.account_status, DriverAccountStatus.ACTIVE)


class DriverKycViaPatchTests(TestCase):
    """Onboarding gap fix — attaching an uploaded KYC document's URL to the
    driver record. This capability lived on three dedicated `kyc/{docType}`
    endpoints for a while, then got folded into the single general-purpose
    `PATCH /drivers/{id}` (true partial-update semantics: only whichever
    fields are sent get changed)."""

    def setUp(self):
        self.company = Company.objects.create(name="KYC Document Test Co")
        self.admin = AdminUser.objects.create_user(
            email="kycadmin@test.invalid", company=self.company, password="pass12345"
        )
        self.driver = Driver.objects.create(
            company=self.company, full_name="Onboarding Driver", phone_number="+919876500031",
            emergency_contact_name="EC", emergency_contact_phone="+919876500032",
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def test_submitting_aadhar_doc_url_alone_attaches_url_and_sets_pending(self):
        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {"aadharDocUrl": "https://x.test/aadhar.pdf"}, format="json",
        )
        self.assertEqual(r.status_code, 200)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.aadhar_doc_url, "https://x.test/aadhar.pdf")
        self.assertEqual(self.driver.aadhar_status, VerificationStatus.PENDING)

    def test_unrelated_fields_are_left_untouched_by_a_kyc_only_patch(self):
        self.driver.dl_status = VerificationStatus.VERIFIED
        self.driver.dl_expiry_date = date.today() + timedelta(days=100)
        self.driver.save(update_fields=["dl_status", "dl_expiry_date"])

        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {"policeDocUrl": "https://x.test/police.pdf"}, format="json",
        )
        self.assertEqual(r.status_code, 200)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.police_doc_url, "https://x.test/police.pdf")
        # untouched — this PATCH never mentioned the driver's name or DL.
        self.assertEqual(self.driver.full_name, "Onboarding Driver")
        self.assertEqual(self.driver.dl_status, VerificationStatus.VERIFIED)

    def test_resubmitting_after_rejection_clears_note_and_resets_to_pending(self):
        from . import services

        services.verify_aadhar(self.driver, status=VerificationStatus.REJECTED, admin_id=self.admin.id, note="blurry")
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.aadhar_status, VerificationStatus.REJECTED)

        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {"aadharDocUrl": "https://x.test/aadhar-v2.pdf"}, format="json",
        )
        self.assertEqual(r.status_code, 200)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.aadhar_doc_url, "https://x.test/aadhar-v2.pdf")
        self.assertEqual(self.driver.aadhar_status, VerificationStatus.PENDING)
        self.assertIsNone(self.driver.aadhar_rejection_note)
        self.assertIsNone(self.driver.aadhar_verified_by)

    def test_doc_url_and_status_together_attaches_and_decides_in_one_call(self):
        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {"aadharDocUrl": "https://x.test/aadhar-final.pdf", "aadharStatus": "verified"}, format="json",
        )
        self.assertEqual(r.status_code, 200)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.aadhar_doc_url, "https://x.test/aadhar-final.pdf")
        self.assertEqual(self.driver.aadhar_status, VerificationStatus.VERIFIED)
        self.assertEqual(self.driver.aadhar_verified_by, self.admin.id)

    def test_all_three_doc_types_can_be_updated_in_one_call(self):
        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {
                "aadharDocUrl": "https://x.test/aadhar.pdf",
                "dlDocUrl": "https://x.test/dl.pdf",
                "policeDocUrl": "https://x.test/police.pdf",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.aadhar_doc_url, "https://x.test/aadhar.pdf")
        self.assertEqual(self.driver.dl_doc_url, "https://x.test/dl.pdf")
        self.assertEqual(self.driver.police_doc_url, "https://x.test/police.pdf")
        self.assertEqual(self.driver.aadhar_status, VerificationStatus.PENDING)
        self.assertEqual(self.driver.dl_status, VerificationStatus.PENDING)
        self.assertEqual(self.driver.police_status, VerificationStatus.PENDING)

    def test_a_lone_rejection_note_with_no_status_or_doc_is_rejected(self):
        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}", {"aadharNote": "looks blurry"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_doc_url_and_direct_file_together_is_rejected(self):
        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {
                "aadharDocUrl": "https://x.test/aadhar.pdf",
                "aadharDoc": SimpleUploadedFile("aadhar.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(r.status_code, 400)

    def test_direct_file_upload_uploads_to_storage_and_attaches_the_resulting_url(self):
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media_root):
            r = self.client_api.patch(
                f"/api/v1/drivers/{self.driver.id}",
                {"policeDoc": SimpleUploadedFile("police.pdf", b"%PDF-1.4 fake", content_type="application/pdf")},
                format="multipart",
            )
        self.assertEqual(r.status_code, 200)

        self.driver.refresh_from_db()
        self.assertTrue(self.driver.police_doc_url)
        self.assertIn("driver-documents", self.driver.police_doc_url)
        self.assertEqual(self.driver.police_status, VerificationStatus.PENDING)

    def test_verifying_dl_falls_back_to_the_expiry_and_categories_already_on_file(self):
        r1 = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {"dlDocUrl": "https://x.test/dl.pdf", "dlExpiryDate": "2030-01-01", "dlAllowedCategories": ["four_wheeler"]},
            format="json",
        )
        self.assertEqual(r1.status_code, 200)

        # Second call only decides the status — doesn't repeat expiry/categories.
        r2 = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}", {"dlStatus": "verified"}, format="json"
        )
        self.assertEqual(r2.status_code, 200)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.dl_status, VerificationStatus.VERIFIED)
        self.assertEqual(str(self.driver.dl_expiry_date), "2030-01-01")
        self.assertEqual(self.driver.dl_allowed_categories, ["four_wheeler"])

    def test_verifying_dl_with_neither_fresh_nor_on_file_expiry_is_rejected(self):
        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}", {"dlStatus": "verified"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_now_a_full_onboarding_kyc_loop_can_reach_eligibility(self):
        """End-to-end regression for the reported gap: submit all three docs,
        then verify all three, and the driver becomes assignment-eligible."""
        r1 = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {
                "aadharDocUrl": "https://x.test/aadhar.pdf",
                "dlDocUrl": "https://x.test/dl.pdf",
                "policeDocUrl": "https://x.test/police.pdf",
            },
            format="json",
        )
        self.assertEqual(r1.status_code, 200)

        r2 = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}",
            {
                "aadharStatus": "verified",
                "policeStatus": "verified",
                "dlStatus": "verified",
                "dlExpiryDate": str(date.today() + timedelta(days=365)),
                "dlAllowedCategories": ["four_wheeler"],
            },
            format="json",
        )
        self.assertEqual(r2.status_code, 200)

        self.driver.refresh_from_db()
        self.assertTrue(self.driver.is_eligible_for_assignment)


class DriverOneShotCreationTests(TestCase):
    """POST /drivers can optionally take all three document URLs (+
    dl_expiry_date) at creation time for an admin who has everything ready
    up front — none of it auto-verifies."""

    def setUp(self):
        self.company = Company.objects.create(name="One Shot Creation Co")
        self.admin = AdminUser.objects.create_user(
            email="oneshotadmin@test.invalid", company=self.company, password="pass12345"
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def _base_payload(self, **extra):
        payload = {
            "fullName": "One Shot Driver",
            "phoneNumber": "+919876500040",
            "emergencyContactName": "EC",
            "emergencyContactPhone": "+919876500041",
        }
        payload.update(extra)
        return payload

    def test_minimal_flow_still_works_with_just_the_four_required_fields(self):
        r = self.client_api.post("/api/v1/drivers", self._base_payload(), format="json")
        self.assertEqual(r.status_code, 201)

        driver = Driver.objects.get(pk=r.data["id"])
        self.assertIsNone(driver.aadhar_doc_url)
        self.assertEqual(driver.aadhar_status, VerificationStatus.PENDING)

    def test_one_shot_creation_stores_documents_but_leaves_everything_pending(self):
        r = self.client_api.post(
            "/api/v1/drivers",
            self._base_payload(
                aadharDocUrl="https://x.test/aadhar.pdf",
                dlDocUrl="https://x.test/dl.pdf",
                dlExpiryDate="2030-01-01",
                policeDocUrl="https://x.test/police.pdf",
            ),
            format="json",
        )
        self.assertEqual(r.status_code, 201)

        driver = Driver.objects.get(pk=r.data["id"])
        self.assertEqual(driver.aadhar_doc_url, "https://x.test/aadhar.pdf")
        self.assertEqual(driver.dl_doc_url, "https://x.test/dl.pdf")
        self.assertEqual(str(driver.dl_expiry_date), "2030-01-01")
        self.assertEqual(driver.police_doc_url, "https://x.test/police.pdf")

        self.assertEqual(driver.aadhar_status, VerificationStatus.PENDING)
        self.assertEqual(driver.dl_status, VerificationStatus.PENDING)
        self.assertEqual(driver.police_status, VerificationStatus.PENDING)
        self.assertFalse(driver.is_eligible_for_assignment)

    def test_dl_expiry_date_without_dl_doc_url_is_allowed(self):
        r = self.client_api.post(
            "/api/v1/drivers", self._base_payload(dlExpiryDate="2030-06-15"), format="json"
        )
        self.assertEqual(r.status_code, 201)
        driver = Driver.objects.get(pk=r.data["id"])
        self.assertIsNone(driver.dl_doc_url)
        self.assertEqual(str(driver.dl_expiry_date), "2030-06-15")

    def test_kyc_update_still_works_unchanged_on_a_document_attached_at_creation(self):
        r = self.client_api.post(
            "/api/v1/drivers",
            self._base_payload(aadharDocUrl="https://x.test/aadhar.pdf"),
            format="json",
        )
        driver_id = r.data["id"]

        r2 = self.client_api.patch(
            f"/api/v1/drivers/{driver_id}", {"aadharStatus": "verified"}, format="json"
        )
        self.assertEqual(r2.status_code, 200)

        driver = Driver.objects.get(pk=driver_id)
        self.assertEqual(driver.aadhar_status, VerificationStatus.VERIFIED)
        self.assertEqual(driver.aadhar_doc_url, "https://x.test/aadhar.pdf")

    def test_client_cannot_set_status_or_account_fields_at_creation(self):
        r = self.client_api.post(
            "/api/v1/drivers",
            self._base_payload(aadharStatus="verified", accountStatus="disabled"),
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        driver = Driver.objects.get(pk=r.data["id"])
        self.assertEqual(driver.aadhar_status, VerificationStatus.PENDING)
        self.assertEqual(driver.account_status, DriverAccountStatus.ACTIVE)


class DriverDeleteSafetyTests(TestCase):
    """DELETE /drivers/{id} must go through the same disable_driver safety
    check (active-trip refusal, soft delete) as the disable action — not a
    raw instance.delete() that bypasses both."""

    def setUp(self):
        self.company = Company.objects.create(name="Driver Delete Safety Co")
        self.admin = AdminUser.objects.create_user(
            email="driverdeletesafety@test.invalid", company=self.company, password="pass12345"
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def test_delete_soft_deletes_and_deactivates_instead_of_hard_deleting(self):
        driver = Driver.objects.create(
            company=self.company, full_name="Delete Safety Driver", phone_number="+919700000010",
            emergency_contact_name="EC", emergency_contact_phone="+919700000011",
        )

        r = self.client_api.delete(f"/api/v1/drivers/{driver.id}")
        self.assertEqual(r.status_code, 204)

        driver.refresh_from_db()
        self.assertTrue(driver.is_deleted)
        self.assertEqual(driver.account_status, DriverAccountStatus.DISABLED)

    def test_delete_is_refused_with_an_active_trip_same_as_disable(self):
        from trips import services as trip_services

        vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=vehicle_type,
            registration_number="KA01DD0001", capacity_kg=Decimal("400"),
        )
        driver = Driver.objects.create(
            company=self.company, full_name="Busy Driver", phone_number="+919700000012",
            emergency_contact_name="EC", emergency_contact_phone="+919700000013",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        actor = SimpleNamespace(company_id=self.company.id)
        result = trip_services.intake_order(
            company_id=self.company.id, order_ref="ORD-DRV-DEL-SAFETY", parent_order_ref=None,
            pickup={"address": "P", "latitude": Decimal("1"), "longitude": Decimal("1")},
            delivery={"address": "D", "latitude": Decimal("1"), "longitude": Decimal("1")},
            weight_kg=Decimal("5.00"), actor=actor,
        )
        trip_services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=vehicle.id, driver_id=driver.id, actor=actor
        )

        r = self.client_api.delete(f"/api/v1/drivers/{driver.id}")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "DRIVER_HAS_ACTIVE_TRIP")

        driver.refresh_from_db()
        self.assertFalse(driver.is_deleted, "must not be deleted, soft or hard, when refused")


class DriverMeExtendedFieldsTests(TestCase):
    """GET /driver/me should expose account_status, the per-document
    rejection notes, and the document URLs themselves, so a driver's app can
    show its own verification state and why a document was rejected."""

    def setUp(self):
        self.company = Company.objects.create(name="Driver Me Extended Co")
        self.driver = Driver.objects.create(
            company=self.company, full_name="Me Endpoint Driver", phone_number="+919876500050",
            emergency_contact_name="EC", emergency_contact_phone="+919876500051",
            aadhar_doc_url="https://x.test/aadhar.pdf",
            aadhar_status=VerificationStatus.REJECTED,
            aadhar_rejection_note="Document is blurry.",
            dl_doc_url="https://x.test/dl.pdf",
            dl_status=VerificationStatus.REJECTED,
            dl_rejection_note="Expired at time of submission.",
            police_doc_url="https://x.test/police.pdf",
            police_status=VerificationStatus.REJECTED,
            police_rejection_note="Name mismatch.",
        )
        access, _refresh, _expires_in = issue_driver_token(self.driver)
        self.client_api = APIClient()
        self.client_api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_me_response_includes_account_status_and_rejection_notes_and_doc_urls(self):
        r = self.client_api.get("/api/v1/driver/me")
        self.assertEqual(r.status_code, 200)

        body = _body(r)
        self.assertEqual(body["accountStatus"], DriverAccountStatus.ACTIVE)
        self.assertEqual(body["aadharDocUrl"], "https://x.test/aadhar.pdf")
        self.assertEqual(body["aadharRejectionNote"], "Document is blurry.")
        self.assertEqual(body["dlDocUrl"], "https://x.test/dl.pdf")
        self.assertEqual(body["dlRejectionNote"], "Expired at time of submission.")
        self.assertEqual(body["policeDocUrl"], "https://x.test/police.pdf")
        self.assertEqual(body["policeRejectionNote"], "Name mismatch.")

    def test_rejection_notes_are_null_when_nothing_was_rejected(self):
        clean_driver = Driver.objects.create(
            company=self.company, full_name="Clean Driver", phone_number="+919876500052",
            emergency_contact_name="EC", emergency_contact_phone="+919876500053",
        )
        access, _refresh, _expires_in = issue_driver_token(clean_driver)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        r = client.get("/api/v1/driver/me")
        self.assertEqual(r.status_code, 200)
        body = _body(r)
        self.assertIsNone(body["aadharRejectionNote"])
        self.assertIsNone(body["dlRejectionNote"])
        self.assertIsNone(body["policeRejectionNote"])


class PutMethodRemovedTests(TestCase):
    """Data-safety fix — every update spec here was PATCH (partial); PUT
    (full replacement) was only ever reachable as an accidental side effect
    of ModelViewSet's defaults, and on fields like KYC status/doc URLs an
    accidental full-replacement call could silently wipe real review work."""

    def setUp(self):
        self.company = Company.objects.create(name="Driver Put Removed Co")
        self.admin = AdminUser.objects.create_user(
            email="driverputremoved@test.invalid", company=self.company, password="pass12345"
        )
        self.driver = Driver.objects.create(
            company=self.company, full_name="Put Removed Driver", phone_number="+919700000020",
            emergency_contact_name="EC", emergency_contact_phone="+919700000021",
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def test_put_on_driver_is_405(self):
        r = self.client_api.put(
            f"/api/v1/drivers/{self.driver.id}", {"fullName": "Full Replace Attempt"}, format="json"
        )
        self.assertEqual(r.status_code, 405)

    def test_patch_on_driver_still_works_and_leaves_other_fields_alone(self):
        r = self.client_api.patch(
            f"/api/v1/drivers/{self.driver.id}", {"fullName": "Renamed Driver"}, format="json"
        )
        self.assertEqual(r.status_code, 200)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.full_name, "Renamed Driver")
        self.assertEqual(self.driver.phone_number, "+919700000020", "untouched by the PATCH")
