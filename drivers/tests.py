import json
import uuid
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Company

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
