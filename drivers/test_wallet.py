"""The driver wallet: earnings credited when a trip completes, the ledger, the
summary the wallet screen shows, and the company's payout/bonus entries."""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Company
from core.choices import PaymentMode, PaymentStatus, TripStatus, WalletTransactionKind as Kind
from core.testing import LOCMEM_CACHES, DriverTestMixin
from drivers.models import WalletTransaction
from drivers.wallet import WalletService

BASE = "/api/v1/driver/wallet"


def utc(*args):
    return datetime(*args, tzinfo=dt_timezone.utc)


class WalletTestCase(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver()
        self.vehicle = self.make_vehicle()
        self.client = self.driver_client(self.driver)

    def in_progress_trip(self, **overrides):
        fields = dict(
            status=TripStatus.IN_PROGRESS, payment_mode=PaymentMode.PREPAID, payment_status=PaymentStatus.PAID
        )
        fields.update(overrides)
        return self.make_trip(self.driver, self.vehicle, **fields)

    def complete(self, trip):
        return self.client.post(f"/api/v1/driver/trips/{trip.id}/complete", {}, format="json")

    def entry_at(self, kind, amount, when):
        entry = WalletService.add_entry(self.driver, kind, Decimal(str(amount)))
        WalletTransaction.objects.filter(pk=entry.pk).update(created_at=when)
        return entry


@LOCMEM_CACHES
class EarningTests(WalletTestCase):
    def test_completing_a_trip_pays_the_driver_their_share_of_the_fare(self):
        trip = self.in_progress_trip(total_fare=Decimal("85.00"))

        response = self.complete(trip)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["driver_earning"], "68.00")  # 80% of 85
        entry = WalletTransaction.objects.get(driver=self.driver)
        self.assertEqual((entry.kind, entry.amount, entry.balance_after), (Kind.TRIP_EARNING, Decimal("68.00"), Decimal("68.00")))
        self.assertEqual(entry.trip_id, trip.id)
        self.assertEqual(entry.description, "Delivery to Indiranagar, Bengaluru")
        self.assertEqual(self.client.get(BASE).json()["balance"], "68.00")

    def test_the_share_is_rounded_to_the_paisa(self):
        self.complete(self.in_progress_trip(total_fare=Decimal("111.62")))  # 89.296
        self.assertEqual(WalletService.balance(self.driver), Decimal("89.30"))

    def test_the_share_is_configurable(self):
        with override_settings(DRIVER_EARNING_PERCENT=Decimal("50")):
            self.complete(self.in_progress_trip(total_fare=Decimal("85.00")))
        self.assertEqual(WalletService.balance(self.driver), Decimal("42.50"))

    def test_a_trip_pays_only_once(self):
        trip = self.in_progress_trip()
        self.complete(trip)
        trip.refresh_from_db()

        again = WalletService.credit_trip_earning(trip)

        self.assertEqual(WalletTransaction.objects.filter(trip_id=trip.id).count(), 1)
        self.assertEqual(again.amount, Decimal("68.00"))

    def test_cod_and_prepaid_trips_both_pay_out_on_completion(self):
        cod = self.in_progress_trip(payment_mode=PaymentMode.COD, payment_status=PaymentStatus.PAID)
        from django.core.cache import cache

        cache.set(f"trip_delivery_otp:{cod.id}", "123456")
        response = self.client.post(f"/api/v1/driver/trips/{cod.id}/complete", {"otp": "123456"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WalletService.balance(self.driver), Decimal("68.00"))

    def test_a_failed_completion_pays_nothing(self):
        cod = self.in_progress_trip(payment_mode=PaymentMode.COD, payment_status=PaymentStatus.PENDING)
        response = self.complete(cod)  # payment not collected
        self.assertEqual(response.status_code, 409)
        self.assertFalse(WalletTransaction.objects.exists())

    def test_cancelled_trips_pay_nothing(self):
        trip = self.make_trip(self.driver, self.vehicle)  # assigned
        self.client.post(f"/api/v1/driver/trips/{trip.id}/cancel", {"reason": "Breakdown"}, format="json")
        self.assertFalse(WalletTransaction.objects.exists())

    def test_the_company_view_of_a_trip_does_not_reveal_what_the_driver_earned(self):
        trip = self.in_progress_trip()
        self.complete(trip)
        body = self.admin_client().get(f"/api/v1/trips/{trip.id}").json()
        self.assertNotIn("driver_earning", body)

    def test_todays_earnings_show_on_the_dashboard_stats_and_the_trip_history(self):
        trip = self.in_progress_trip()
        self.complete(trip)

        stats = self.client.get("/api/v1/driver/stats").json()
        self.assertEqual(stats["today"]["earnings"], "68.00")
        self.assertEqual(stats["all_time"]["earnings"], "68.00")

        row = self.client.get("/api/v1/driver/trips?status=completed").json()["results"][0]
        self.assertEqual(row["driver_earning"], "68.00")


@LOCMEM_CACHES
class LedgerTests(WalletTestCase):
    def test_each_row_records_the_running_balance(self):
        WalletService.add_entry(self.driver, Kind.TRIP_EARNING, Decimal("100"))
        WalletService.add_entry(self.driver, Kind.BONUS, Decimal("50"))
        WalletService.add_entry(self.driver, Kind.PAYOUT, Decimal("-40"))

        rows = list(WalletTransaction.objects.filter(driver=self.driver).order_by("created_at"))

        self.assertEqual([r.balance_after for r in rows], [Decimal("100"), Decimal("150"), Decimal("110")])
        self.assertEqual(WalletService.balance(self.driver), Decimal("110"))

    def test_transactions_are_listed_newest_first_with_a_direction(self):
        WalletService.add_entry(self.driver, Kind.TRIP_EARNING, Decimal("100"), description="Delivery")
        WalletService.add_entry(self.driver, Kind.PAYOUT, Decimal("-40"), reference="UTR123")

        body = self.client.get(f"{BASE}/transactions").json()

        self.assertEqual(body["count"], 2)
        first, second = body["results"]
        self.assertEqual((first["kind"], first["type"], first["amount"], first["reference"]), ("payout", "debit", "-40.00", "UTR123"))
        self.assertEqual((second["kind"], second["type"], second["amount"]), ("trip_earning", "credit", "100.00"))
        self.assertEqual(first["balance_after"], "60.00")
        self.assertEqual(first["kind_label"], "Payout")

    def test_the_statement_can_be_filtered_by_kind_and_is_paginated(self):
        for _ in range(25):
            WalletService.add_entry(self.driver, Kind.TRIP_EARNING, Decimal("10"))
        WalletService.add_entry(self.driver, Kind.BONUS, Decimal("5"))

        page1 = self.client.get(f"{BASE}/transactions").json()
        self.assertEqual((page1["count"], len(page1["results"])), (26, 20))
        self.assertIsNotNone(page1["next"])

        bonuses = self.client.get(f"{BASE}/transactions", {"kind": "bonus"}).json()
        self.assertEqual(bonuses["count"], 1)
        both = self.client.get(f"{BASE}/transactions", {"kind": "bonus,payout"}).json()
        self.assertEqual(both["count"], 1)

    def test_a_driver_only_ever_sees_their_own_wallet(self):
        other = self.make_driver()
        WalletService.add_entry(other, Kind.BONUS, Decimal("999"))
        self.assertEqual(self.client.get(f"{BASE}/transactions").json()["count"], 0)
        self.assertEqual(self.client.get(BASE).json()["balance"], "0.00")

    def test_a_zero_entry_is_refused(self):
        with self.assertRaises(Exception) as caught:
            WalletService.add_entry(self.driver, Kind.BONUS, Decimal("0"))
        self.assertEqual(caught.exception.code, "INVALID_AMOUNT")

    def test_needs_a_driver_token(self):
        self.assertEqual(APIClient().get(BASE).status_code, 401)
        self.assertEqual(APIClient().get(f"{BASE}/transactions").status_code, 401)
        self.assertEqual(self.admin_client().get(BASE).status_code, 403)


@LOCMEM_CACHES
class SummaryTests(WalletTestCase):
    """Fixed clock: Wednesday 23 Sep 2026, 10:00 UTC (15:30 IST)."""

    NOW = utc(2026, 9, 23, 10, 0)

    def setUp(self):
        super().setUp()
        self.entry_at(Kind.TRIP_EARNING, 100, utc(2026, 9, 23, 9, 0))
        self.entry_at(Kind.BONUS, 50, utc(2026, 9, 23, 2, 0))
        self.entry_at(Kind.TRIP_EARNING, 25, utc(2026, 9, 22, 20, 0))  # IST: already the 23rd
        self.entry_at(Kind.TRIP_EARNING, 70, utc(2026, 9, 22, 12, 0))
        self.entry_at(Kind.TRIP_EARNING, 30, utc(2026, 9, 21, 0, 30))  # Monday
        self.entry_at(Kind.TRIP_EARNING, 20, utc(2026, 9, 20, 12, 0))  # the Sunday before
        self.entry_at(Kind.TRIP_EARNING, 10, utc(2026, 8, 30, 12, 0))  # last month
        self.entry_at(Kind.PAYOUT, -40, utc(2026, 9, 23, 8, 0))
        self.entry_at(Kind.PENALTY, -5, utc(2026, 9, 23, 9, 30))

    def summary(self, offset=0):
        return WalletService.summary(self.driver, offset, now=self.NOW)

    def test_periods_in_utc(self):
        s = self.summary()

        self.assertEqual(s["balance"], "260.00")  # 305 earned - 40 payout - 5 penalty
        self.assertEqual(s["today"], {"earnings": "150.00", "trips": 1})  # bonus counts, penalty/payout don't
        self.assertEqual(s["week"], {"earnings": "275.00", "trips": 4})  # from Monday 21st
        self.assertEqual(s["month"], {"earnings": "295.00", "trips": 5})
        self.assertEqual(s["lifetime"], {"earnings": "305.00", "trips": 6, "payouts": "40.00"})

    def test_days_are_the_drivers_days_not_the_servers(self):
        s = self.summary(offset=330)  # IST

        # 20:00 UTC on the 22nd is 01:30 on the 23rd for this driver.
        self.assertEqual(s["today"], {"earnings": "175.00", "trips": 2})

    def test_the_last_seven_days_are_zero_filled_and_in_order(self):
        days = self.summary()["last_7_days"]

        self.assertEqual([d["date"] for d in days], [f"2026-09-{n}" for n in range(17, 24)])
        self.assertEqual(
            [d["earnings"] for d in days],
            ["0.00", "0.00", "0.00", "20.00", "30.00", "95.00", "150.00"],
        )
        self.assertEqual([d["trips"] for d in days], [0, 0, 0, 1, 1, 2, 1])

    def test_the_last_seven_days_follow_the_drivers_offset(self):
        by_day = {d["date"]: d["earnings"] for d in self.summary(offset=330)["last_7_days"]}
        self.assertEqual(by_day["2026-09-23"], "175.00")
        self.assertEqual(by_day["2026-09-22"], "70.00")

    def test_an_empty_wallet_is_zeroes(self):
        s = WalletService.summary(self.make_driver())
        self.assertEqual(s["balance"], "0.00")
        self.assertEqual(s["lifetime"], {"earnings": "0.00", "trips": 0, "payouts": "0.00"})
        self.assertEqual(len(s["last_7_days"]), 7)

    def test_the_endpoint_takes_the_drivers_offset_and_validates_it(self):
        self.assertEqual(self.client.get(BASE, {"utc_offset_minutes": 330}).status_code, 200)
        self.assertEqual(self.client.get(BASE, {"utc_offset_minutes": 5000}).status_code, 400)


@LOCMEM_CACHES
class CompanyWalletTests(WalletTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_client()
        WalletService.add_entry(self.driver, Kind.TRIP_EARNING, Decimal("200"))

    def post(self, body, admin=None):
        return (admin or self.admin).post(f"/api/v1/drivers/{self.driver.id}/wallet/transactions", body, format="json")

    def test_a_payout_reduces_the_balance_and_keeps_its_reference(self):
        response = self.post({"kind": "payout", "amount": "150.00", "reference": "UTR998877", "description": "Weekly"})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["kind"], body["amount"], body["balance_after"]), ("payout", "-150.00", "50.00"))
        self.assertEqual(body["reference"], "UTR998877")
        entry = WalletTransaction.objects.get(pk=body["id"])
        self.assertEqual(entry.created_by, self.admin.admin.id)  # who recorded it
        self.assertEqual(self.client.get(BASE).json()["lifetime"]["payouts"], "150.00")

    def test_a_payout_cannot_exceed_the_balance(self):
        response = self.post({"kind": "payout", "amount": "200.01"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "INSUFFICIENT_BALANCE")
        self.assertEqual(WalletService.balance(self.driver), Decimal("200"))

    def test_bonus_and_penalty_apply_the_right_sign_to_a_positive_amount(self):
        self.assertEqual(self.post({"kind": "bonus", "amount": "25"}).json()["amount"], "25.00")
        self.assertEqual(self.post({"kind": "penalty", "amount": "10"}).json()["amount"], "-10.00")
        self.assertEqual(WalletService.balance(self.driver), Decimal("215"))

    def test_an_adjustment_keeps_the_sign_it_was_given(self):
        self.assertEqual(self.post({"kind": "adjustment", "amount": "-30"}).json()["amount"], "-30.00")
        self.assertEqual(self.post({"kind": "adjustment", "amount": "5"}).json()["amount"], "5.00")

    def test_nonsense_entries_are_refused(self):
        for body in (
            {"kind": "payout", "amount": "-10"},  # sign comes from the kind
            {"kind": "bonus", "amount": "0"},
            {"kind": "trip_earning", "amount": "10"},  # only ever credited by completing a trip
            {"kind": "nope", "amount": "10"},
        ):
            self.assertEqual(self.post(body).status_code, 400, body)
        self.assertEqual(WalletTransaction.objects.count(), 1)

    def test_the_company_sees_the_balance_and_recent_statement(self):
        self.post({"kind": "payout", "amount": "50"})
        body = self.admin.get(f"/api/v1/drivers/{self.driver.id}/wallet").json()
        self.assertEqual(body["balance"], "150.00")
        self.assertEqual([r["kind"] for r in body["recent"]], ["payout", "trip_earning"])

    def test_another_companys_admin_cannot_touch_this_wallet(self):
        stranger = self.admin_client(Company.objects.create(name="Other Co"))
        self.assertEqual(stranger.get(f"/api/v1/drivers/{self.driver.id}/wallet").status_code, 404)
        self.assertEqual(self.post({"kind": "bonus", "amount": "5"}, admin=stranger).status_code, 404)

    def test_a_driver_cannot_pay_themselves(self):
        response = self.client.post(
            f"/api/v1/drivers/{self.driver.id}/wallet/transactions", {"kind": "bonus", "amount": "999"}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(WalletService.balance(self.driver), Decimal("200"))
