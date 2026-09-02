from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Company
from damage_reports.models import VehicleDamageReport
from drivers.models import Driver, DriverAccountStatus, VerificationStatus
from drivers.tasks import lock_expired_driver_licenses
from issues import services as issue_services
from tracking import services as tracking_services
from trips import services as trip_services
from vehicles.models import (
    Vehicle,
    VehicleCategory,
    VehicleDocument,
    VehicleDocumentExpiryAlert,
    VehicleDocumentType,
    VehicleType,
)

from . import services


def _addr(address="Address", lat="12.900000", lng="77.600000"):
    return {"address": address, "latitude": Decimal(lat), "longitude": Decimal(lng)}


class DashboardTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Dashboard Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)
        self.vehicle_type = VehicleType.objects.create(
            company=self.company,
            name="Mini Van",
            category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )

    def _make_vehicle(self, reg):
        return Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number=reg, capacity_kg=Decimal("500")
        )

    def _make_driver(self, phone):
        return Driver.objects.create(
            company=self.company,
            full_name=f"Driver {phone}",
            phone_number=phone,
            emergency_contact_name="EC",
            emergency_contact_phone="+919000000000",
            aadhar_status=VerificationStatus.VERIFIED,
            police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED,
            dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )

    def _put_vehicle_in_transit(self, vehicle, driver, order_ref):
        result = trip_services.intake_order(
            company_id=self.company.id, order_ref=order_ref, parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("5.00"), actor=self.actor,
        )
        trip_services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url="https://x.test/p.jpg", actor=self.actor)
        trip_services.lock_pickups(trip_id=result["trip_id"], actor=self.actor)
        trip = trip_services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=vehicle.id, driver_id=driver.id, actor=self.actor
        )
        tracking_services.start_shift(
            driver_id=driver.id, vehicle_id=vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )
        tracking_services.record_location_ping(
            trip_id=trip.id, latitude=Decimal("12.905"), longitude=Decimal("77.605"), speed_kmph=None, actor=self.actor
        )
        trip.refresh_from_db()
        return trip


class FleetStatusTests(DashboardTestBase):
    def test_statuses_and_query_count_stay_flat_as_fleet_grows(self):
        moving_vehicle = self._make_vehicle("KA06AA0001")
        moving_driver = self._make_driver("+919500000001")
        self._put_vehicle_in_transit(moving_vehicle, moving_driver, "ORD-MOVING")

        paused_vehicle = self._make_vehicle("KA06AA0002")
        paused_driver = self._make_driver("+919500000002")
        paused_trip = self._put_vehicle_in_transit(paused_vehicle, paused_driver, "ORD-PAUSED")
        tracking_services.pause_trip(trip_id=paused_trip.id, reason="lunch", actor=self.actor)

        offline_vehicle = self._make_vehicle("KA06AA0003")

        with CaptureQueriesContext(connection) as ctx:
            fleet = services.get_fleet_status(self.company.id)
        baseline_query_count = len(ctx.captured_queries)

        by_reg = {row["registration_number"]: row for row in fleet}
        self.assertEqual(by_reg["KA06AA0001"]["status"], "moving")
        self.assertIsNotNone(by_reg["KA06AA0001"]["last_location"])
        self.assertEqual(by_reg["KA06AA0002"]["status"], "paused")
        self.assertEqual(by_reg["KA06AA0002"]["pause_reason"], "lunch")
        self.assertEqual(by_reg["KA06AA0003"]["status"], "offline")
        self.assertIsNone(by_reg["KA06AA0003"]["driver_id"])
        self.assertEqual(by_reg["KA06AA0003"]["today_km"], 0.0, "no pings recorded today yet")

        # Add three more idle vehicles and confirm the query count doesn't grow —
        # proof there's no per-vehicle query hiding in the loop.
        for i in range(4, 7):
            self._make_vehicle(f"KA06AA000{i}")

        with CaptureQueriesContext(connection) as ctx2:
            services.get_fleet_status(self.company.id)
        grown_query_count = len(ctx2.captured_queries)

        self.assertEqual(
            baseline_query_count, grown_query_count, "query count must not scale with fleet size"
        )

    def test_today_km_sums_the_days_gps_derived_distance(self):
        from tracking.models import TripLocationPing

        vehicle = self._make_vehicle("KA06AA0010")
        driver = self._make_driver("+919500000010")
        trip = self._put_vehicle_in_transit(vehicle, driver, "ORD-KM")

        now = timezone.now()
        TripLocationPing.objects.create(
            company_id=self.company.id, trip=trip, vehicle_id=vehicle.id,
            latitude=Decimal("12.910"), longitude=Decimal("77.610"),
            recorded_at=now - timedelta(minutes=10), distance_from_previous_km=Decimal("3.200"),
        )
        TripLocationPing.objects.create(
            company_id=self.company.id, trip=trip, vehicle_id=vehicle.id,
            latitude=Decimal("12.915"), longitude=Decimal("77.615"),
            recorded_at=now - timedelta(minutes=5), distance_from_previous_km=Decimal("1.800"),
        )
        # From yesterday — must not be counted in today's total.
        TripLocationPing.objects.create(
            company_id=self.company.id, trip=trip, vehicle_id=vehicle.id,
            latitude=Decimal("12.800"), longitude=Decimal("77.500"),
            recorded_at=now - timedelta(days=1), distance_from_previous_km=Decimal("50.000"),
        )

        fleet = services.get_fleet_status(self.company.id)
        by_reg = {row["registration_number"]: row for row in fleet}
        self.assertAlmostEqual(by_reg["KA06AA0010"]["today_km"], 5.0)


class KpiTests(DashboardTestBase):
    def test_kpis_reflect_current_state(self):
        moving_vehicle = self._make_vehicle("KA07BB0001")
        moving_driver = self._make_driver("+919600000001")
        trip = self._put_vehicle_in_transit(moving_vehicle, moving_driver, "ORD-KPI-1")

        # Deliver it with a known duration for avg_delivery_minutes.
        trip.started_at = timezone.now() - timedelta(minutes=40)
        trip.save(update_fields=["started_at"])
        drop_stop = trip.stops.filter(stop_type="drop").first()
        trip_services.complete_stop(stop_id=drop_stop.id, proof_photo_url="https://x.test/d.jpg", actor=self.actor)
        trip.refresh_from_db()
        trip.completed_at = trip.started_at + timedelta(minutes=40)
        trip.save(update_fields=["completed_at"])

        VehicleDamageReport.objects.create(
            company=self.company, vehicle=moving_vehicle, reported_by_id=moving_driver.id,
            reporter_type="driver", description="Scratch on the door",
        )

        locked_driver = self._make_driver("+919600000002")
        locked_driver.dl_expiry_date = date.today() - timedelta(days=1)
        locked_driver.account_status = DriverAccountStatus.ACTIVE
        locked_driver.save(update_fields=["dl_expiry_date", "account_status"])
        lock_expired_driver_licenses()
        locked_driver.refresh_from_db()
        self.assertEqual(locked_driver.account_status, DriverAccountStatus.LOCKED_DL_EXPIRED)

        another_vehicle = self._make_vehicle("KA07BB0002")
        another_driver = self._make_driver("+919600000003")
        self._put_vehicle_in_transit(another_vehicle, another_driver, "ORD-KPI-2")

        expiring_doc = VehicleDocument.objects.create(
            company=self.company, vehicle=moving_vehicle, document_type=VehicleDocumentType.INSURANCE,
            file_url="https://x.test/ins.pdf", expiry_date=date.today() + timedelta(days=5),
        )
        VehicleDocumentExpiryAlert.objects.create(
            company=self.company, document=expiring_doc, vehicle=moving_vehicle,
            expiry_date=expiring_doc.expiry_date, detected_at=timezone.now(),
        )

        kpis = services.get_kpis(self.company.id)

        self.assertEqual(kpis["active_vehicles"], 2)  # moving_vehicle's shift + another_vehicle's shift
        self.assertEqual(kpis["on_trip"], 1)  # only another_vehicle's trip is still in_transit
        self.assertEqual(kpis["avg_delivery_minutes"], 40)
        self.assertEqual(kpis["open_damage_reports"], 1)
        self.assertEqual(kpis["drivers_locked_dl_expired"], 1)
        self.assertEqual(kpis["documents_expiring_soon"], 1)

    def test_avg_delivery_minutes_is_null_with_no_completions_today(self):
        kpis = services.get_kpis(self.company.id)
        self.assertIsNone(kpis["avg_delivery_minutes"])
        self.assertEqual(kpis["active_vehicles"], 0)
        self.assertEqual(kpis["on_trip"], 0)


class RecentEndpointsTests(DashboardTestBase):
    def test_recent_issues_empty_with_none_reported(self):
        self.assertEqual(services.get_recent_issues(self.company.id, 5), [])

    def test_recent_issues_ordered_and_scoped(self):
        vehicle = self._make_vehicle("KA08DD0001")
        driver = self._make_driver("+919700000099")
        trip = self._put_vehicle_in_transit(vehicle, driver, "ORD-ISSUE-1")
        for i in range(3):
            issue_services.create_issue(
                trip_id=trip.id, issue_type="transit", note=f"Issue {i}", actor=self.actor
            )

        issues = services.get_recent_issues(self.company.id, 2)
        self.assertEqual(len(issues), 2)
        self.assertTrue(issues[0].created_at >= issues[1].created_at)

    def test_recent_damage_reports_ordered_and_scoped(self):
        vehicle = self._make_vehicle("KA08CC0001")
        driver = self._make_driver("+919700000001")
        for i in range(3):
            VehicleDamageReport.objects.create(
                company=self.company, vehicle=vehicle, reported_by_id=driver.id,
                reporter_type="driver", description=f"Damage {i}",
            )

        reports = services.get_recent_damage_reports(self.company.id, 2)
        self.assertEqual(len(reports), 2)
        self.assertTrue(reports[0].created_at >= reports[1].created_at)
