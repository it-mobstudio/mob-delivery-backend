"""The operations admin (/admin/): the dashboard, every screen loading with
real data, search, company scoping, roles, and the actions that change things."""

from decimal import Decimal
from itertools import count

from django.test import Client, TestCase

from accounts.models import AdminUser, Company
from core.choices import AdminRole, PaymentMode, PaymentStatus, TripStatus, VerificationStatus
from core.testing import LOCMEM_CACHES, DriverTestMixin
from drivers.models import WalletTransaction
from drivers.wallet import WalletService
from trips.models import Trip, TripItem, TripNumber


_logins = count()


@LOCMEM_CACHES
class AdminTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver(full_name="Ramesh Kumar")
        self.vehicle = self.make_vehicle()
        self.trip = self.make_trip(
            self.driver, self.vehicle, status=TripStatus.COMPLETED, payment_mode=PaymentMode.PREPAID,
            payment_status=PaymentStatus.PAID, pickup_photo_url="/media/x/pickup-proofs/a.jpg",
            order_number=TripNumber.order_number_for(__import__("datetime").date(2026, 9, 25), 777),
            total_fare=Decimal("420.00"), driver_earning=Decimal("336.00"),
        )
        TripItem.objects.create(company=self.company, trip=self.trip, name="TMT bar 12mm", quantity=20,
                                status="not_delivered", driver_note="Damaged", proof_image_url="/media/x/p.jpg")
        self.open = self.make_trip(self.driver, self.vehicle, status=TripStatus.ASSIGNED, order_number="OD2026092500778")

    def login(self, *, superuser=False, company=None, role=AdminRole.OWNER):
        user = AdminUser.objects.create_user(
            email=f"{role}-{next(_logins)}@example.com", company=company or self.company,
            password="x", is_staff=True, is_superuser=superuser, role=role,
        )
        client = Client(HTTP_HOST="localhost")
        client.force_login(user)
        return client

    # -- it all opens ------------------------------------------------------------

    def test_the_dashboard_shows_the_numbers_and_what_needs_attention(self):
        page = self.login(superuser=True).get("/admin/db/?period=30d").content.decode()
        self.assertIn("Operations", page)
        self.assertIn("₹420", page)  # order value of the completed order
        self.assertIn("Items reported not delivered", page)
        self.assertIn("OD20260925000777", page)  # recent orders
        for period in ("today", "7d", "90d", "nonsense"):
            self.assertEqual(self.login(superuser=True).get(f"/admin/db/?period={period}").status_code, 200)

    def test_every_screen_opens(self):
        client = self.login(superuser=True)
        item = TripItem.objects.get()
        wallet = WalletService.record_manual(self.driver, "bonus", Decimal("50"))
        for url in [
            "/admin/db/trips/trip/", f"/admin/db/trips/trip/{self.trip.pk}/change/", "/admin/db/trips/tripitem/",
            f"/admin/db/trips/tripitem/{item.pk}/change/", "/admin/db/drivers/driver/", f"/admin/db/drivers/driver/{self.driver.pk}/change/",
            "/admin/db/drivers/vehicle/", f"/admin/db/drivers/vehicle/{self.vehicle.pk}/change/", "/admin/db/drivers/vehicletype/",
            "/admin/db/drivers/wallettransaction/", "/admin/db/drivers/wallettransaction/add/",
            f"/admin/db/drivers/wallettransaction/{wallet.pk}/change/", "/admin/db/accounts/company/", "/admin/db/accounts/adminuser/",
            "/admin/db/accounts/apiclient/", "/admin/db/drivers/driver/?review=pending", "/admin/db/drivers/driver/?review=expiring",
            "/admin/db/trips/trip/?photos=any", "/admin/db/trips/trip/?photos=missing",
        ]:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_an_order_page_shows_its_proof_photos_and_timeline(self):
        page = self.login(superuser=True).get(f"/admin/db/trips/trip/{self.trip.pk}/change/").content.decode()
        self.assertIn("/media/x/pickup-proofs/a.jpg", page)
        self.assertIn("Checked · TMT bar 12mm", page)
        self.assertIn("Booked", page)

    def test_search_finds_orders_by_number_driver_and_customer(self):
        client = self.login(superuser=True)
        for q in ("OD20260925000777", "Ramesh", self.driver.phone_number):
            with self.subTest(q=q):
                self.assertIn("OD20260925000777", client.get(f"/admin/db/trips/trip/?q={q}").content.decode())
        self.assertNotIn("OD20260925000777", client.get("/admin/db/trips/trip/?q=nobody-by-this-name").content.decode())

    # -- who sees what --------------------------------------------------------------

    def test_a_company_operator_only_sees_their_own_company(self):
        other = Company.objects.create(name="Other Co")
        client = self.login(company=other)
        self.assertNotIn("OD20260925000777", client.get("/admin/db/trips/trip/").content.decode())
        self.assertEqual(client.get(f"/admin/db/trips/trip/{self.trip.pk}/change/").status_code, 302)  # not theirs
        self.assertNotIn("₹420", client.get("/admin/db/?period=30d").content.decode())
        # ...and companies / API credentials are the platform's alone.
        self.assertEqual(client.get("/admin/db/accounts/company/").status_code, 403)
        self.assertEqual(client.get("/admin/db/accounts/apiclient/").status_code, 403)

    def test_staff_can_look_but_not_change(self):
        client = self.login(role=AdminRole.STAFF)
        self.assertEqual(client.get(f"/admin/db/trips/trip/{self.trip.pk}/change/").status_code, 200)
        response = client.post(f"/admin/db/trips/trip/{self.trip.pk}/change/", {"notes": "hacked"})
        self.assertEqual(response.status_code, 403)
        self.trip.refresh_from_db()
        self.assertNotEqual(self.trip.notes, "hacked")

    def test_the_admin_needs_a_staff_login(self):
        self.assertEqual(Client(HTTP_HOST="localhost").get("/admin/db/").status_code, 302)

    # -- actions --------------------------------------------------------------------

    def test_cancelling_from_the_admin_goes_through_the_service(self):
        self.login(superuser=True).post("/admin/db/trips/trip/", {
            "action": "cancel_orders", "_selected_action": [str(self.open.pk), str(self.trip.pk)],
        })
        self.open.refresh_from_db()
        self.trip.refresh_from_db()
        self.assertEqual((self.open.status, self.open.cancelled_by), (TripStatus.CANCELLED, "company"))
        self.assertEqual(self.trip.status, TripStatus.COMPLETED, "a delivered order can't be cancelled")

    def test_orders_export_as_csv(self):
        response = self.login(superuser=True).post("/admin/db/trips/trip/", {
            "action": "export_orders_csv", "_selected_action": [str(self.trip.pk)],
        })
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("OD20260925000777", response.content.decode())

    def test_approving_kyc_unlocks_a_driver(self):
        pending = self.make_driver(verified=False, full_name="New Driver")
        pending.kyc.dl_expiry_date = __import__("datetime").date(2030, 1, 1)
        pending.kyc.save()
        self.login(superuser=True).post("/admin/db/drivers/driver/", {
            "action": "approve_kyc", "_selected_action": [str(pending.pk)],
        })
        pending.kyc.refresh_from_db()
        self.assertEqual(
            (pending.kyc.aadhar_status, pending.kyc.dl_status, pending.kyc.police_status),
            (VerificationStatus.VERIFIED,) * 3,
        )

    def test_a_payout_is_recorded_through_the_wallet_and_cannot_overdraw(self):
        client = self.login(superuser=True)
        WalletService.record_manual(self.driver, "bonus", Decimal("100"))
        client.post("/admin/db/drivers/wallettransaction/add/", {
            "driver": str(self.driver.pk), "kind": "payout", "amount": "60", "description": "UPI", "reference": "UTR1",
        })
        self.assertEqual(WalletService.balance(self.driver), Decimal("40"))
        payout = WalletTransaction.objects.get(reference="UTR1")
        self.assertEqual(payout.amount, Decimal("-60.00"))

        response = client.post("/admin/db/drivers/wallettransaction/add/", {
            "driver": str(self.driver.pk), "kind": "payout", "amount": "999",
        }, follow=True)
        self.assertIn("more than the driver", response.content.decode())
        self.assertEqual(WalletService.balance(self.driver), Decimal("40"))

    def test_blocking_and_reactivating_a_driver(self):
        client = self.login(superuser=True)
        idle = self.make_driver(full_name="Idle Driver")
        client.post("/admin/db/drivers/driver/", {"action": "block_accounts", "_selected_action": [str(idle.pk)]})
        idle.refresh_from_db()
        self.assertEqual(idle.account_status, "disabled")
        client.post("/admin/db/drivers/driver/", {"action": "reactivate_accounts", "_selected_action": [str(idle.pk)]})
        idle.refresh_from_db()
        self.assertEqual(idle.account_status, "active")
