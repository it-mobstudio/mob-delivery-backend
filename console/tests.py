"""The operations console (/admin/): every screen opens with real data, the
filters work, who sees what, and every action changes the right thing."""

from datetime import date
from decimal import Decimal
from itertools import count

from django.test import Client, TestCase

from accounts.models import AdminUser, Company
from core.choices import AdminRole, PaymentMode, PaymentStatus, TripStatus, VerificationStatus
from core.testing import LOCMEM_CACHES, DriverTestMixin
from drivers.wallet import WalletService
from trips.models import Trip, TripItem

_logins = count()


@LOCMEM_CACHES
class ConsoleTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver(full_name="Ramesh Kumar")
        self.vehicle = self.make_vehicle()
        self.done = self.make_trip(
            self.driver, self.vehicle, status=TripStatus.COMPLETED, order_number="OD20260925000901",
            payment_mode=PaymentMode.PREPAID, payment_status=PaymentStatus.PAID, total_fare=Decimal("420"),
            driver_earning=Decimal("336"), delivery_photo_url="/media/x/delivery.jpg", drop_contact_name="Priya Sharma",
        )
        TripItem.objects.create(company=self.company, trip=self.done, name="TMT bar", quantity=20, status="not_delivered",
                                driver_note="Damaged", proof_image_url="/media/x/check.jpg")
        self.waiting = self.make_trip(None, None, status=TripStatus.NO_DRIVER_AVAILABLE, order_number="OD20260925000902")

    def login(self, *, superuser=False, company=None, role=AdminRole.OWNER):
        user = AdminUser.objects.create_user(
            email=f"u{next(_logins)}@example.com", company=company or self.company, password="x",
            is_staff=True, is_superuser=superuser, role=role)
        client = Client(HTTP_HOST="localhost")
        client.force_login(user)
        return client

    def test_every_screen_opens(self):
        client = self.login(superuser=True)
        for url in [
            "/admin/", "/admin/?period=today", "/admin/?from=2026-09-01&to=2026-09-30", "/admin/live/", "/admin/live/data/",
            "/admin/orders/", "/admin/orders/?status=unassigned", "/admin/orders/?status=active&photos=has&issues=1&sort=fare",
            f"/admin/orders/{self.done.pk}/", f"/admin/orders/{self.waiting.pk}/", "/admin/gallery/", "/admin/gallery/?kind=check",
            "/admin/drivers/", "/admin/drivers/?tab=kyc", f"/admin/drivers/{self.driver.pk}/",
            *[f"/admin/drivers/{self.driver.pk}/?tab={t}" for t in ("kyc", "trips", "wallet", "vehicles")],
            "/admin/kyc/", "/admin/vehicles/", f"/admin/vehicles/{self.vehicle.pk}/", "/admin/fares/",
            "/admin/payouts/", "/admin/ledger/", "/admin/companies/", "/admin/team/", "/admin/api-clients/",
            "/admin/search/?q=Ramesh", "/admin/db/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_signing_in(self):
        self.assertRedirects(Client(HTTP_HOST="localhost").get("/admin/orders/"), "/admin/login/?next=/admin/orders/")
        AdminUser.objects.create_user(email="ops@example.com", company=self.company, password="s3cret-pass", is_staff=True)
        response = Client(HTTP_HOST="localhost").post("/admin/login/", {"username": "ops@example.com", "password": "s3cret-pass"})
        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)

    def test_the_dashboard_counts_what_happened(self):
        page = self.login(superuser=True).get("/admin/?period=30d").content.decode()
        self.assertIn("₹420", page)
        self.assertIn("Orders waiting for a driver", page)
        self.assertIn("Items reported not delivered", page)

    def test_order_filters_and_search(self):
        client = self.login(superuser=True)
        self.assertIn("OD20260925000902", client.get("/admin/orders/?status=unassigned").content.decode())
        self.assertNotIn("OD20260925000901", client.get("/admin/orders/?status=unassigned").content.decode())
        self.assertIn("OD20260925000901", client.get("/admin/orders/?q=Priya").content.decode())
        self.assertIn("OD20260925000901", client.get("/admin/orders/?issues=1").content.decode())
        csv = client.get("/admin/orders/?export=csv")
        self.assertEqual(csv["Content-Type"], "text/csv")
        self.assertIn("OD20260925000901", csv.content.decode())
        # An exact order number in the global search goes straight to it.
        self.assertRedirects(client.get("/admin/search/?q=OD20260925000901"), f"/admin/orders/{self.done.pk}/")

    def test_the_gallery_shows_every_proof_photo(self):
        page = self.login(superuser=True).get("/admin/gallery/").content.decode()
        self.assertIn("/media/x/delivery.jpg", page)
        self.assertIn("/media/x/check.jpg", page)
        self.assertNotIn("/media/x/delivery.jpg", self.login(superuser=True).get("/admin/gallery/?kind=check").content.decode())

    def test_a_company_only_sees_its_own_data(self):
        other = self.login(company=Company.objects.create(name="Other Co"))
        self.assertNotIn("OD20260925000901", other.get("/admin/orders/").content.decode())
        self.assertEqual(other.get(f"/admin/orders/{self.done.pk}/").status_code, 404)
        self.assertEqual(other.get(f"/admin/drivers/{self.driver.pk}/").status_code, 404)
        self.assertEqual(other.get("/admin/companies/").status_code, 403)

    def test_staff_can_look_but_not_act(self):
        staff = self.login(role=AdminRole.STAFF)
        self.assertEqual(staff.get(f"/admin/orders/{self.waiting.pk}/").status_code, 200)
        self.assertEqual(staff.post(f"/admin/orders/{self.waiting.pk}/cancel/", {"reason": "Duplicate order"}).status_code, 403)
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, TripStatus.NO_DRIVER_AVAILABLE)

    def test_cancelling_an_order(self):
        self.login().post(f"/admin/orders/{self.waiting.pk}/cancel/", {"reason": "Duplicate order"})
        self.waiting.refresh_from_db()
        self.assertEqual((self.waiting.status, self.waiting.cancelled_by, self.waiting.cancellation_reason),
                         (TripStatus.CANCELLED, "company", "Duplicate order"))

    def test_assigning_an_order_to_a_chosen_driver(self):
        self.go_online(self.driver)
        self.login().post(f"/admin/orders/{self.waiting.pk}/assign/", {"driver": str(self.driver.pk)})
        self.waiting.refresh_from_db()
        self.assertEqual((self.waiting.status, self.waiting.driver_id), (TripStatus.ASSIGNED, self.driver.pk))

    def test_an_offline_driver_cannot_be_assigned(self):
        response = self.login().post(f"/admin/orders/{self.waiting.pk}/assign/", {"driver": str(self.driver.pk)}, follow=True)
        self.assertIn("can&#x27;t take this order", response.content.decode())
        self.waiting.refresh_from_db()
        self.assertIsNone(self.waiting.driver_id)

    def test_kyc_review(self):
        new = self.make_driver(verified=False, full_name="New Driver")
        new.kyc.aadhar_doc_url, new.kyc.dl_doc_url = "/media/a.jpg", "/media/dl.jpg"
        new.kyc.save()
        client = self.login()
        self.assertIn("New Driver", client.get("/admin/kyc/").content.decode())
        client.post("/admin/kyc/reject/", {"driver": str(new.pk), "doc": "aadhar", "decision": "reject", "note": "Blurry"})
        client.post(f"/admin/drivers/{new.pk}/kyc/", {"doc": "dl", "decision": "approve", "dl_expiry": "2031-01-01",
                                                     "dl_categories": ["two_wheeler"]})
        new.kyc.refresh_from_db()
        self.assertEqual((new.kyc.aadhar_status, new.kyc.aadhar_rejection_note), (VerificationStatus.REJECTED, "Blurry"))
        self.assertEqual((new.kyc.dl_status, new.kyc.dl_expiry_date), (VerificationStatus.VERIFIED, date(2031, 1, 1)))

    def test_rejecting_needs_a_note(self):
        new = self.make_driver(verified=False)
        self.login().post("/admin/kyc/reject/", {"driver": str(new.pk), "doc": "aadhar", "decision": "reject", "note": ""})
        new.kyc.refresh_from_db()
        self.assertEqual(new.kyc.aadhar_status, VerificationStatus.PENDING)

    def test_payouts_and_blocking(self):
        client = self.login()
        WalletService.record_manual(self.driver, "bonus", Decimal("100"))
        client.post(f"/admin/drivers/{self.driver.pk}/wallet/", {"kind": "payout", "amount": "60", "reference": "UTR1"})
        self.assertEqual(WalletService.balance(self.driver), Decimal("40"))
        client.post(f"/admin/drivers/{self.driver.pk}/wallet/", {"kind": "payout", "amount": "999"})
        self.assertEqual(WalletService.balance(self.driver), Decimal("40"), "can't pay out more than the balance")
        client.post(f"/admin/drivers/{self.driver.pk}/status/", {"action": "block"})
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.account_status, "disabled")

    def test_fare_cards_can_be_edited(self):
        vt = self.vehicle_type
        self.login().post("/admin/fares/", {"id": str(vt.pk), "base_fare": "35", "per_km_rate": "12", "per_min_rate": "1.5",
                                            "min_fare": "60", "status": "active"})
        vt.refresh_from_db()
        self.assertEqual((vt.base_fare, vt.per_km_rate, vt.min_fare), (Decimal("35"), Decimal("12"), Decimal("60")))

    def test_an_owner_adds_a_teammate_to_their_own_company(self):
        self.login().post("/admin/team/save/", {"email": "new@example.com", "password": "longpassword", "role": "staff"})
        user = AdminUser.objects.get(email="new@example.com")
        self.assertEqual((user.company_id, user.role, user.is_staff), (self.company.pk, "staff", True))
        self.assertEqual(self.login(role=AdminRole.STAFF).get("/admin/team/").status_code, 403)

    def test_a_dispatcher_records_a_voice_note_for_the_driver(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
        client = self.login()
        client.post(f"/admin/orders/{self.waiting.pk}/voice/", {
            "audio": SimpleUploadedFile("voice-note.wav", wav, content_type="audio/wav"), "seconds": "12"})
        self.waiting.refresh_from_db()
        self.assertIn("voice-notes", self.waiting.voice_note_url)
        self.assertEqual(self.waiting.voice_note_seconds, 12)
        self.assertIn(self.waiting.voice_note_url, client.get(f"/admin/orders/{self.waiting.pk}/").content.decode())

        client.post(f"/admin/orders/{self.waiting.pk}/voice/", {
            "audio": SimpleUploadedFile("x.wav", b"garbage", content_type="audio/wav"), "seconds": "3"})
        self.waiting.refresh_from_db()
        self.assertIn("voice-notes", self.waiting.voice_note_url, "a bad file doesn't replace a good note")

        client.post(f"/admin/orders/{self.waiting.pk}/voice/", {"remove": "1"})
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.voice_note_url, "")

    def go_online(self, driver):
        from drivers.services import DriverService

        DriverService.go_online(driver, self.vehicle, Decimal("12.97"), Decimal("77.59"))
