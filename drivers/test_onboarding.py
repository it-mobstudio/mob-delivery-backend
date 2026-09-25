"""Self-service driver onboarding: sign-up on first login, filling in details,
submitting documents, and the derived onboarding status the app steers by."""

from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Company
from core.choices import CompanyStatus, DriverAccountStatus, VerificationStatus
from core.testing import LOCMEM_CACHES, DriverTestMixin, image_file, pdf_file
from drivers.models import Driver
from drivers.services import DriverKycService
from drivers.wallet import WalletService

PHONE = "+919555500001"

DETAILS = {
    "full_name": "  Ravi   Kumar ",
    "date_of_birth": "1994-03-12",
    "email": "ravi@example.com",
    "address_line": "12 MG Road",
    "city": "Bengaluru",
    "pincode": "560001",
    "emergency_contact_name": "Sunita Kumar",
    "emergency_contact_phone": "+919555500002",
}


class SignupMixin(DriverTestMixin):
    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(DRIVER_SIGNUP_COMPANY_ID=str(self.company.id)))

    def sign_in(self, phone=PHONE):
        """The whole first-login path, over HTTP. Returns (client, response)."""
        client = APIClient()
        otp = client.post("/api/v1/driver/auth/otp/request", {"phone_number": phone}).json()["otp"]
        response = client.post("/api/v1/driver/auth/otp/verify", {"phone_number": phone, "otp": otp})
        if response.status_code == 200:
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['accessToken']}")
        return client, response

    def new_driver_client(self):
        client, _ = self.sign_in()
        return client

    def me(self, client):
        return client.get("/api/v1/driver/me").json()

    def fill_profile(self, client, **overrides):
        return client.patch("/api/v1/driver/me", {**DETAILS, **overrides}, format="json")

    def submit_aadhar(self, client, number="234567890123"):
        return client.post(
            "/api/v1/driver/me/kyc/aadhar",
            {"number": number, "front": image_file("front.jpg"), "back": image_file("back.jpg")},
            format="multipart",
        )

    def submit_dl(self, client, expiry=None, **extra):
        expiry = expiry or (date.today() + timedelta(days=400))
        return client.post(
            "/api/v1/driver/me/kyc/dl",
            {"number": "ka01 20110012345", "expiry_date": expiry.isoformat(), "front": image_file("dl.jpg"), **extra},
            format="multipart",
        )


@LOCMEM_CACHES
class SignupTests(SignupMixin, TestCase):
    def test_a_new_number_becomes_a_driver_of_the_signup_company(self):
        client, response = self.sign_in()

        self.assertEqual(response.status_code, 200)
        driver = Driver.objects.get(phone_number=PHONE)
        self.assertEqual(driver.company_id, self.company.id)
        self.assertEqual(driver.kyc.aadhar_status, VerificationStatus.PENDING)
        me = response.json()["driver"]
        self.assertEqual(me["id"], str(driver.id))
        self.assertEqual(me["full_name"], "")
        self.assertEqual(me["onboarding_status"], "profile_incomplete")
        self.assertFalse(me["is_eligible_for_assignment"])
        self.assertEqual(client.get("/api/v1/driver/me").status_code, 200)

    def test_signing_in_again_is_the_same_driver_not_a_second_one(self):
        self.sign_in()
        self.sign_in()
        self.assertEqual(Driver.objects.filter(phone_number=PHONE).count(), 1)

    def test_requesting_an_otp_looks_the_same_for_known_and_unknown_numbers(self):
        known = self.make_driver()
        client = APIClient()
        unknown_body = client.post("/api/v1/driver/auth/otp/request", {"phone_number": PHONE}).json()
        known_body = client.post("/api/v1/driver/auth/otp/request", {"phone_number": known.phone_number}).json()
        self.assertEqual(set(unknown_body), set(known_body))

    def test_a_wrong_otp_does_not_create_an_account(self):
        client = APIClient()
        client.post("/api/v1/driver/auth/otp/request", {"phone_number": PHONE})
        response = client.post("/api/v1/driver/auth/otp/verify", {"phone_number": PHONE, "otp": "0000"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Driver.objects.filter(phone_number=PHONE).exists())

    def test_existing_drivers_still_sign_in_exactly_as_before(self):
        driver = self.make_driver()
        _, response = self.sign_in(driver.phone_number)
        self.assertEqual(response.json()["driver"]["id"], str(driver.id))
        self.assertEqual(response.json()["driver"]["onboarding_status"], "approved")

    def test_signup_is_closed_unless_a_company_is_configured(self):
        with override_settings(DRIVER_SIGNUP_COMPANY_ID=""):
            response = APIClient().post("/api/v1/driver/auth/otp/request", {"phone_number": PHONE})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "DRIVER_NOT_FOUND")

            # …and verifying can't sneak an account in either.
            response = APIClient().post("/api/v1/driver/auth/otp/verify", {"phone_number": PHONE, "otp": "1234"})
            self.assertEqual(response.status_code, 404)
            self.assertFalse(Driver.objects.filter(phone_number=PHONE).exists())

    def test_signup_is_closed_for_a_garbage_or_inactive_company(self):
        for value in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
            with override_settings(DRIVER_SIGNUP_COMPANY_ID=value):
                response = APIClient().post("/api/v1/driver/auth/otp/request", {"phone_number": PHONE})
                self.assertEqual(response.status_code, 404, value)

        Company.objects.filter(pk=self.company.pk).update(status=CompanyStatus.SUSPENDED)
        response = APIClient().post("/api/v1/driver/auth/otp/request", {"phone_number": PHONE})
        self.assertEqual(response.status_code, 404)

    def test_in_debug_the_only_company_is_used_without_any_setup(self):
        with override_settings(DRIVER_SIGNUP_COMPANY_ID="", DEBUG=True):
            self.assertEqual(
                APIClient().post("/api/v1/driver/auth/otp/request", {"phone_number": PHONE}).status_code, 200
            )

            Company.objects.create(name="Second Co")  # now it's ambiguous, so closed
            response = APIClient().post("/api/v1/driver/auth/otp/request", {"phone_number": "+919555500009"})
            self.assertEqual(response.status_code, 404)

    def test_a_locked_account_still_cannot_sign_in(self):
        driver = self.make_driver()
        driver.account_status = DriverAccountStatus.LOCKED_DL_EXPIRED
        driver.save(update_fields=["account_status"])
        response = APIClient().post("/api/v1/driver/auth/otp/request", {"phone_number": driver.phone_number})
        self.assertEqual(response.status_code, 403)


@LOCMEM_CACHES
class ProfileTests(SignupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_ = self.new_driver_client()

    def test_filling_in_the_details_completes_the_profile(self):
        response = self.fill_profile(self.client_)

        self.assertEqual(response.status_code, 200)
        me = response.json()
        self.assertEqual(me["full_name"], "Ravi Kumar")  # whitespace tidied
        self.assertEqual(me["date_of_birth"], "1994-03-12")
        self.assertEqual(me["city"], "Bengaluru")
        self.assertTrue(me["is_profile_complete"])
        self.assertEqual(me["onboarding_status"], "documents_required")

    def test_a_partial_update_only_touches_what_was_sent(self):
        self.fill_profile(self.client_)
        me = self.client_.patch("/api/v1/driver/me", {"city": "Mysuru"}, format="json").json()
        self.assertEqual(me["city"], "Mysuru")
        self.assertEqual(me["full_name"], "Ravi Kumar")

    def test_the_form_is_validated(self):
        today = date.today()
        cases = {
            "date_of_birth": [
                today.replace(year=today.year - 17).isoformat(),  # too young
                today.replace(year=today.year - 90).isoformat(),  # implausible
            ],
            "pincode": ["12345", "abcdef"],
            "full_name": ["", "A"],
            "emergency_contact_phone": ["12345", PHONE],  # bad, and the driver's own number
            "payout_upi_id": ["no-at-sign"],
            "bank_ifsc": ["HDFC123"],
            "bank_account_number": ["12ab"],
        }
        for field, values in cases.items():
            for value in values:
                response = self.client_.patch("/api/v1/driver/me", {field: value}, format="json")
                self.assertEqual(response.status_code, 400, f"{field}={value!r}")
                self.assertIn(field, response.json()["error"]["details"])

    def test_someone_who_just_turned_eighteen_is_accepted(self):
        today = date.today()
        response = self.client_.patch(
            "/api/v1/driver/me", {"date_of_birth": today.replace(year=today.year - 18).isoformat()}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_bank_details_only_count_as_a_whole(self):
        response = self.client_.patch("/api/v1/driver/me", {"bank_account_number": "123456789012"}, format="json")
        self.assertEqual(response.status_code, 400)
        details = response.json()["error"]["details"]
        self.assertIn("bank_ifsc", details)
        self.assertIn("bank_account_holder", details)

        me = self.client_.patch(
            "/api/v1/driver/me",
            {"bank_account_holder": "Ravi Kumar", "bank_account_number": "123456789012", "bank_ifsc": "hdfc0001234"},
            format="json",
        ).json()
        # The full number is never sent back — only enough to recognise the account.
        self.assertEqual(
            me["payout"],
            {
                "upi_id": None,
                "bank_account_holder": "Ravi Kumar",
                "bank_account_last4": "9012",
                "bank_ifsc": "HDFC0001234",
                "is_set": True,
            },
        )
        self.assertNotIn("123456789012", str(me))

    def test_upi_payout_details(self):
        me = self.client_.patch("/api/v1/driver/me", {"payout_upi_id": "ravi@okhdfc"}, format="json").json()
        self.assertEqual(me["payout"]["upi_id"], "ravi@okhdfc")
        self.assertTrue(me["payout"]["is_set"])

    def test_name_and_dob_are_locked_once_the_id_is_verified_but_the_rest_is_not(self):
        self.fill_profile(self.client_)
        driver = Driver.objects.get(phone_number=PHONE)
        DriverKycService.verify_aadhar(driver, VerificationStatus.VERIFIED, admin_id=None)

        locked = self.client_.patch("/api/v1/driver/me", {"full_name": "Someone Else"}, format="json")
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(locked.json()["error"]["code"], "PROFILE_LOCKED")

        # Re-sending the same value isn't a change.
        self.assertEqual(
            self.client_.patch("/api/v1/driver/me", {"full_name": "Ravi Kumar"}, format="json").status_code, 200
        )
        self.assertEqual(self.client_.patch("/api/v1/driver/me", {"city": "Mysuru"}, format="json").status_code, 200)

    def test_a_driver_cannot_edit_their_phone_number_or_account_status(self):
        self.client_.patch(
            "/api/v1/driver/me", {"phone_number": "+919000000099", "account_status": "disabled"}, format="json"
        )
        driver = Driver.objects.get(phone_number=PHONE)
        self.assertEqual(driver.account_status, DriverAccountStatus.ACTIVE)


@LOCMEM_CACHES
class DocumentSubmissionTests(SignupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_ = self.new_driver_client()
        self.fill_profile(self.client_)
        self.driver = Driver.objects.get(phone_number=PHONE)

    def test_aadhar_keeps_only_the_last_four_digits_and_both_scans(self):
        response = self.submit_aadhar(self.client_)

        self.assertEqual(response.status_code, 200)
        aadhar = response.json()["kyc"]["aadhar"]
        self.assertEqual(aadhar["number_last4"], "0123")
        self.assertTrue(aadhar["submitted"])
        self.assertEqual(aadhar["status"], "pending")
        self.assertNotIn("234567890123", str(response.json()))
        self.driver.kyc.refresh_from_db()
        self.assertEqual(self.driver.kyc.aadhar_number_last4, "0123")
        self.assertNotIn("234567890123", str(self.driver.kyc.__dict__))

    def test_uploaded_scans_come_back_as_urls_the_phone_can_load(self):
        aadhar = self.submit_aadhar(self.client_).json()["kyc"]["aadhar"]
        # Stored relative (/media/...), but served resolved against the host the
        # request came in on — an emulator and a browser reach us differently.
        self.assertTrue(aadhar["front_url"].startswith("http://testserver/media/"), aadhar["front_url"])
        self.assertTrue(aadhar["back_url"].startswith("http://testserver/media/"))
        self.assertNotEqual(aadhar["front_url"], aadhar["back_url"])

    def test_aadhar_number_must_be_twelve_digits(self):
        for bad in ("12345", "23456789012a", "2345 6789 01234"):
            response = self.submit_aadhar(self.client_, number=bad)
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(self.submit_aadhar(self.client_, number="2345 6789 0123").status_code, 200)  # spaces are fine

    def test_both_aadhar_sides_are_required(self):
        response = self.client_.post(
            "/api/v1/driver/me/kyc/aadhar",
            {"number": "234567890123", "front": image_file("front.jpg")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("back", response.json()["error"]["details"])

    def test_a_file_that_is_not_really_an_image_is_refused(self):
        fake = SimpleUploadedFile("front.jpg", b"definitely not a jpeg", content_type="image/jpeg")
        response = self.client_.post(
            "/api/v1/driver/me/kyc/aadhar",
            {"number": "234567890123", "front": fake, "back": image_file("back.jpg")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_UPLOAD")

    def test_driving_licence_records_the_number_expiry_and_scans(self):
        response = self.submit_dl(self.client_)

        self.assertEqual(response.status_code, 200)
        dl = response.json()["kyc"]["dl"]
        self.assertEqual(dl["number"], "KA01 20110012345")  # tidied and upper-cased
        self.assertIsNotNone(dl["expiry_date"])
        self.assertIsNotNone(dl["front_url"])
        self.assertIsNone(dl["back_url"])  # the back is optional
        self.assertEqual(dl["status"], "pending")

        with_back = self.submit_dl(self.client_, back=image_file("dl-back.jpg")).json()["kyc"]["dl"]
        self.assertIsNotNone(with_back["back_url"])

    def test_an_expired_licence_is_refused_up_front(self):
        response = self.submit_dl(self.client_, expiry=date.today() - timedelta(days=1))
        self.assertEqual(response.status_code, 400)
        self.assertIn("expiry_date", response.json()["error"]["details"])

    def test_police_certificate_can_be_a_pdf_or_an_image(self):
        for upload in (pdf_file("pcc.pdf"), image_file("pcc.png")):
            response = self.client_.post("/api/v1/driver/me/kyc/police", {"document": upload}, format="multipart")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["kyc"]["police"]["submitted"])

    def test_a_pdf_that_is_not_a_pdf_is_refused(self):
        response = self.client_.post(
            "/api/v1/driver/me/kyc/police",
            {"document": SimpleUploadedFile("pcc.pdf", b"<html>nope</html>")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_profile_photo(self):
        response = self.client_.post("/api/v1/driver/me/photo", {"photo": image_file("me.jpg")}, format="multipart")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["profile_photo_url"].startswith("http://testserver/media/"))

    def test_a_verified_document_cannot_be_replaced(self):
        self.submit_aadhar(self.client_)
        DriverKycService.verify_aadhar(self.driver, VerificationStatus.VERIFIED, admin_id=None)

        response = self.submit_aadhar(self.client_)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "KYC_ALREADY_VERIFIED")

    def test_resubmitting_a_rejected_document_puts_it_back_in_review(self):
        self.submit_aadhar(self.client_)
        DriverKycService.verify_aadhar(self.driver, VerificationStatus.REJECTED, admin_id=None, note="Blurry")
        rejected = self.me(self.client_)
        self.assertEqual(rejected["aadhar_status"], "rejected")
        self.assertEqual(rejected["kyc"]["aadhar"]["rejection_note"], "Blurry")

        me = self.submit_aadhar(self.client_).json()

        self.assertEqual(me["kyc"]["aadhar"]["status"], "pending")
        self.assertIsNone(me["kyc"]["aadhar"]["rejection_note"])

    def test_only_a_driver_can_submit(self):
        response = APIClient().post("/api/v1/driver/me/kyc/police", {"document": pdf_file()}, format="multipart")
        self.assertEqual(response.status_code, 401)


@LOCMEM_CACHES
class OnboardingStatusTests(SignupMixin, TestCase):
    """The status the app steers by: forced sign-up flow only while it's the
    driver's turn; after that it's waiting on the company."""

    def setUp(self):
        super().setUp()
        self.client_ = self.new_driver_client()
        self.driver = Driver.objects.get(phone_number=PHONE)

    def status(self):
        return self.me(self.client_)["onboarding_status"]

    def test_the_whole_journey(self):
        self.assertEqual(self.status(), "profile_incomplete")

        self.fill_profile(self.client_)
        self.assertEqual(self.status(), "documents_required")

        self.submit_aadhar(self.client_)
        self.assertEqual(self.status(), "documents_required")  # still no licence

        self.submit_dl(self.client_)
        self.assertEqual(self.status(), "under_review")  # police cert is optional to upload

        DriverKycService.verify_aadhar(self.driver, VerificationStatus.VERIFIED, admin_id=None)
        DriverKycService.verify_police(self.driver, VerificationStatus.VERIFIED, admin_id=None)
        self.assertEqual(self.status(), "under_review")  # licence still to be decided

        DriverKycService.verify_dl(
            self.driver,
            VerificationStatus.VERIFIED,
            admin_id=None,
            expiry_date=date.today() + timedelta(days=300),
            allowed_categories=["two_wheeler"],
        )
        me = self.me(self.client_)
        self.assertEqual(me["onboarding_status"], "approved")
        self.assertTrue(me["is_eligible_for_assignment"])

    def test_a_rejection_means_action_required_until_it_is_fixed(self):
        self.fill_profile(self.client_)
        self.submit_aadhar(self.client_)
        self.submit_dl(self.client_)
        DriverKycService.verify_dl(self.driver, VerificationStatus.REJECTED, admin_id=None, note="Expiry unreadable")
        self.assertEqual(self.status(), "action_required")

        self.submit_dl(self.client_)
        self.assertEqual(self.status(), "under_review")

    def test_a_rejected_police_certificate_also_needs_action(self):
        self.fill_profile(self.client_)
        self.submit_aadhar(self.client_)
        self.submit_dl(self.client_)
        DriverKycService.verify_police(self.driver, VerificationStatus.REJECTED, admin_id=None, note="Not attested")
        self.assertEqual(self.status(), "action_required")

    def test_a_driver_the_company_created_and_verified_is_never_sent_back_through_onboarding(self):
        # No date of birth, no scans — created before this feature existed.
        veteran = self.make_driver()
        self.assertFalse(veteran.is_profile_complete)
        self.assertEqual(self.me(self.driver_client(veteran))["onboarding_status"], "approved")

    def test_a_company_created_driver_who_is_not_verified_yet_starts_at_the_profile(self):
        pending = self.make_driver(verified=False)
        self.assertEqual(self.me(self.driver_client(pending))["onboarding_status"], "profile_incomplete")

    def test_an_unapproved_driver_cannot_go_on_duty(self):
        self.fill_profile(self.client_)
        vehicle = self.make_vehicle()
        response = self.client_.post("/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id)}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "DRIVER_NOT_ELIGIBLE")

    def test_admin_sees_what_the_driver_submitted(self):
        self.fill_profile(self.client_)
        self.submit_aadhar(self.client_)
        self.submit_dl(self.client_)

        admin = self.admin_client()
        kyc = admin.get(f"/api/v1/drivers/{self.driver.id}/kyc").json()

        self.assertEqual(kyc["aadhar_number_last4"], "0123")
        self.assertTrue(kyc["aadhar_doc_url"].startswith("http://testserver/media/"))
        self.assertTrue(kyc["aadhar_back_doc_url"].startswith("http://testserver/media/"))
        self.assertEqual(kyc["dl_number"], "KA01 20110012345")

        listed = admin.get("/api/v1/drivers").json()["results"]
        self.assertEqual(listed[0]["onboarding_status"], "under_review")

        # …and their decision flows straight back to the driver.
        admin.patch(
            f"/api/v1/drivers/{self.driver.id}/kyc/aadhar", {"status": "rejected", "note": "Blurry"}, format="json"
        )
        self.assertEqual(self.status(), "action_required")

    def test_a_company_can_still_create_a_driver_with_a_name(self):
        admin = self.admin_client()
        payload = {
            "phone_number": "+919555500077",
            "emergency_contact_name": "E",
            "emergency_contact_phone": "+919555500078",
        }
        # The name became blankable on the model (self-signup) but stays
        # mandatory when a company creates the driver.
        self.assertEqual(admin.post("/api/v1/drivers", payload, format="json").status_code, 400)
        self.assertEqual(
            admin.post("/api/v1/drivers", {**payload, "full_name": "Named"}, format="json").status_code, 201
        )


@LOCMEM_CACHES
class AccountDeletionTests(SignupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client_ = self.new_driver_client()
        self.driver = Driver.objects.get(phone_number=PHONE)

    def test_a_driver_can_delete_their_own_account(self):
        response = self.client_.delete("/api/v1/driver/me")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Driver.objects.filter(phone_number=PHONE).exists())
        # The old session is dead…
        self.assertEqual(self.client_.get("/api/v1/driver/me").status_code, 401)

    def test_the_number_can_register_again_afterwards_as_a_fresh_driver(self):
        self.client_.delete("/api/v1/driver/me")
        _, response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["driver"]["id"], str(self.driver.id))
        self.assertEqual(response.json()["driver"]["onboarding_status"], "profile_incomplete")

    def test_deletion_waits_for_an_active_trip_to_end(self):
        driver = self.make_driver()
        self.make_trip(driver)  # assigned
        response = self.driver_client(driver).delete("/api/v1/driver/me")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "DRIVER_HAS_ACTIVE_TRIP")

    def test_deletion_waits_for_money_owed_to_be_paid_out(self):
        WalletService.record_manual(self.driver, "bonus", 150)
        response = self.client_.delete("/api/v1/driver/me")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "WALLET_BALANCE_PENDING")
        self.assertIn("150", response.json()["error"]["message"])

        WalletService.record_manual(self.driver, "payout", 150)
        self.assertEqual(self.client_.delete("/api/v1/driver/me").status_code, 204)

    def test_only_a_driver_can_delete_a_driver_account(self):
        self.assertEqual(APIClient().delete("/api/v1/driver/me").status_code, 401)
        self.assertEqual(self.admin_client().delete("/api/v1/driver/me").status_code, 403)


@LOCMEM_CACHES
class ApproveDriverCommandTests(SignupMixin, TestCase):
    """`manage.py approve_driver` — what a company admin's review does, for
    local testing of a driver who signed up in the app."""

    def setUp(self):
        super().setUp()
        self.client_ = self.new_driver_client()
        self.fill_profile(self.client_)
        self.submit_aadhar(self.client_)
        self.submit_dl(self.client_)

    def run_command(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("approve_driver", *args, stdout=out)
        return out.getvalue()

    def test_approving_unlocks_the_driver(self):
        self.assertEqual(self.me(self.client_)["onboarding_status"], "under_review")

        output = self.run_command("--phone", PHONE)

        me = self.me(self.client_)
        self.assertEqual(me["onboarding_status"], "approved")
        self.assertTrue(me["is_eligible_for_assignment"])
        self.assertEqual(me["dl_allowed_categories"], ["two_wheeler"])
        self.assertIn("Approved", output)

    def test_categories_and_expiry_are_configurable(self):
        self.run_command("--phone", PHONE, "--categories", "two_wheeler", "four_wheeler", "--expiry-days", "30")
        me = self.me(self.client_)
        self.assertEqual(me["dl_allowed_categories"], ["two_wheeler", "four_wheeler"])
        self.assertEqual(me["dl_expiry_date"], (date.today() + timedelta(days=30)).isoformat())

    def test_rejecting_sends_the_driver_back_to_fix_it(self):
        self.run_command("--phone", PHONE, "--reject", "aadhar", "--note", "Blurry")
        me = self.me(self.client_)
        self.assertEqual(me["onboarding_status"], "action_required")
        self.assertEqual(me["kyc"]["aadhar"]["rejection_note"], "Blurry")

    def test_an_unknown_number_is_explained(self):
        from django.core.management.base import CommandError

        with self.assertRaisesRegex(CommandError, "Sign up in the app first"):
            self.run_command("--phone", "+910000000000")
