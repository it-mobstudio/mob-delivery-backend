from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Company
from core.exceptions import DomainError
from drivers.models import Driver, VerificationStatus
from tenant_settings.services import set_tenant_setting
from trips import services as trip_services
from trips.models import StopStatus, StopType, Trip, TripStatus, TripStop
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import anomaly_detection, services
from .models import AnomalyType, DriverShift, ShiftStatus, TripAnomalyAlert, TripLocationPing
from .tasks import detect_offline_vehicles, detect_stationary_vehicles, purge_old_location_pings


def _addr(address="Address", lat="1.000000", lng="1.000000"):
    return {"address": address, "latitude": Decimal(lat), "longitude": Decimal(lng)}


class TrackingTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Tracking Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

        self.vehicle_type = VehicleType.objects.create(
            company=self.company,
            name="Mini Van",
            category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company,
            vehicle_type=self.vehicle_type,
            registration_number="KA02CD5678",
            capacity_kg=Decimal("500.00"),
        )
        self.driver = Driver.objects.create(
            company=self.company,
            full_name="Track Driver",
            phone_number="+919111111111",
            emergency_contact_name="EC",
            emergency_contact_phone="+919111111112",
            aadhar_status=VerificationStatus.VERIFIED,
            police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED,
            dl_expiry_date=date.today() + timedelta(days=365),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )

    def _make_assigned_trip(self):
        result = trip_services.intake_order(
            company_id=self.company.id,
            order_ref="ORD-TRK-1",
            parent_order_ref=None,
            pickup=_addr("Pickup"),
            delivery=_addr("Delivery"),
            weight_kg=Decimal("5.00"),
            actor=self.actor,
        )
        trip_services.complete_stop(
            stop_id=result["pickup_stop_id"], proof_photo_url="https://x.test/p.jpg", actor=self.actor
        )
        trip_services.lock_pickups(trip_id=result["trip_id"], actor=self.actor)
        trip = trip_services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )
        return trip, result


class ShiftLifecycleTests(TrackingTestBase):
    def test_start_shift_then_reject_second_active_shift(self):
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )
        self.assertEqual(shift.status, ShiftStatus.ACTIVE)

        with self.assertRaises(DomainError) as ctx:
            services.start_shift(
                driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
            )
        self.assertEqual(ctx.exception.code, "SHIFT_ALREADY_ACTIVE")

    def test_end_shift_requires_both_photos_distinctly(self):
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )

        with self.assertRaises(DomainError) as ctx:
            services.end_shift(
                shift_id=shift.id,
                end_odometer=Decimal("1050.00"),
                cleanliness_photo_url=None,
                charging_plugged_photo_url="https://x.test/charge.jpg",
                actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "CLEANLINESS_PHOTO_REQUIRED")

        with self.assertRaises(DomainError) as ctx:
            services.end_shift(
                shift_id=shift.id,
                end_odometer=Decimal("1050.00"),
                cleanliness_photo_url="https://x.test/clean.jpg",
                charging_plugged_photo_url=None,
                actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "CHARGING_PHOTO_REQUIRED")

    def test_end_shift_succeeds_with_both_photos_and_computes_totals(self):
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )
        shift.started_at = timezone.now() - timedelta(hours=3)
        shift.save(update_fields=["started_at"])

        result = services.end_shift(
            shift_id=shift.id,
            end_odometer=Decimal("1075.50"),
            cleanliness_photo_url="https://x.test/clean.jpg",
            charging_plugged_photo_url="https://x.test/charge.jpg",
            actor=self.actor,
        )

        self.assertEqual(result["total_km"], Decimal("75.50"))
        self.assertEqual(result["total_working_minutes"], 180)
        self.assertEqual(result["trips_completed_today"], 0)

        shift.refresh_from_db()
        self.assertEqual(shift.status, ShiftStatus.ENDED)

        self.vehicle.refresh_from_db()
        self.driver.refresh_from_db()
        self.assertIsNone(self.vehicle.current_driver_id)
        self.assertIsNone(self.driver.current_vehicle_id)

    def test_end_shift_blocked_while_trip_in_progress(self):
        trip, _ = self._make_assigned_trip()
        self.assertEqual(trip.status, TripStatus.PICKUPS_LOCKED)  # still active, not delivered

        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )

        with self.assertRaises(DomainError) as ctx:
            services.end_shift(
                shift_id=shift.id,
                end_odometer=Decimal("1050.00"),
                cleanliness_photo_url="https://x.test/clean.jpg",
                charging_plugged_photo_url="https://x.test/charge.jpg",
                actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "TRIP_IN_PROGRESS")

        shift.refresh_from_db()
        self.assertEqual(shift.status, ShiftStatus.ACTIVE, "shift must not be silently closed")


class PickupPhotoGateAtTransitTests(TrackingTestBase):
    """Point 10 hardening — record_location_ping re-verifies a completed,
    photographed pickup exists right at the pickups_locked -> in_transit
    transition, instead of trusting only the upstream chain (complete_stop's
    first-stop gate + lock_pickups' "at least one completed pickup" rule)
    to have held."""

    def test_first_ping_is_rejected_if_pickups_locked_without_a_photo(self):
        result = trip_services.intake_order(
            company_id=self.company.id, order_ref="ORD-NOPHOTO", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("5.00"), actor=self.actor,
        )
        # Bypasses complete_stop/lock_pickups entirely — simulates some
        # other path reaching pickups_locked without ever attaching a gate
        # photo (an admin status-fix tool, a bulk import, etc).
        pickup_stop = TripStop.objects.get(pk=result["pickup_stop_id"])
        pickup_stop.status = StopStatus.COMPLETED
        pickup_stop.save(update_fields=["status"])
        trip = Trip.objects.get(pk=result["trip_id"])
        trip.status = TripStatus.PICKUPS_LOCKED
        trip.save(update_fields=["status"])
        trip = trip_services.assign_vehicle(
            trip_id=trip.id, vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )

        with self.assertRaises(DomainError) as ctx:
            services.record_location_ping(
                trip_id=trip.id, latitude=Decimal("12.9"), longitude=Decimal("77.6"), speed_kmph=None,
                actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "PICKUP_PHOTO_REQUIRED")

        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.PICKUPS_LOCKED, "must not silently flip to in_transit")

    def test_first_ping_succeeds_through_the_normal_flow(self):
        trip, _ = self._make_assigned_trip()  # goes through complete_stop (with a photo) + lock_pickups

        services.record_location_ping(
            trip_id=trip.id, latitude=Decimal("12.9"), longitude=Decimal("77.6"), speed_kmph=None, actor=self.actor,
        )

        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.IN_TRANSIT)


class AnomalyDetectionTests(TrackingTestBase):
    def _put_trip_in_transit(self):
        trip, result = self._make_assigned_trip()
        # First location ping is what flips pickups_locked -> in_transit.
        services.record_location_ping(
            trip_id=trip.id, latitude=Decimal("12.900000"), longitude=Decimal("77.600000"), speed_kmph=None,
            actor=self.actor,
        )
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.IN_TRANSIT)
        return trip

    def test_stationary_pings_create_one_alert_not_duplicated(self):
        trip = self._put_trip_in_transit()

        now = timezone.now()
        for i in range(5):
            TripLocationPing.objects.create(
                company_id=self.company.id,
                trip=trip,
                vehicle_id=self.vehicle.id,
                latitude=Decimal("12.900000"),
                longitude=Decimal("77.600000"),
                recorded_at=now - timedelta(minutes=i),
            )

        created_first = detect_stationary_vehicles()
        self.assertEqual(created_first, 1)
        self.assertEqual(
            TripAnomalyAlert.objects.filter(trip=trip, alert_type=AnomalyType.STATIONARY_TOO_LONG).count(), 1
        )

        created_second = detect_stationary_vehicles()
        self.assertEqual(created_second, 0, "must not create a duplicate alert on a second run")
        self.assertEqual(
            TripAnomalyAlert.objects.filter(trip=trip, alert_type=AnomalyType.STATIONARY_TOO_LONG).count(), 1
        )

    def test_custom_tenant_stationary_radius_is_actually_consulted(self):
        """Fix 3 — stationary_radius_meters is now a per-tenant TenantSetting,
        not a hardcoded constant. Pings ~133m apart: not stationary under the
        default 50m radius, but flagged once this tenant widens it to 200m.
        """
        trip = self._put_trip_in_transit()

        now = timezone.now()
        for i, lat in enumerate(["12.900000", "12.901200"]):
            TripLocationPing.objects.create(
                company_id=self.company.id,
                trip=trip,
                vehicle_id=self.vehicle.id,
                latitude=Decimal(lat),
                longitude=Decimal("77.600000"),
                recorded_at=now - timedelta(minutes=(1 - i)),
            )

        self.assertEqual(detect_stationary_vehicles(), 0, "133m spread should exceed the default 50m radius")

        set_tenant_setting(self.company.id, "stationary_radius_meters", "200")

        self.assertEqual(
            detect_stationary_vehicles(), 1, "widened tenant radius should now flag the same pings as stationary"
        )

    def test_moving_pings_do_not_trigger_stationary_alert(self):
        trip = self._put_trip_in_transit()

        now = timezone.now()
        for i, lat in enumerate(["12.900000", "12.950000", "13.000000", "13.050000"]):
            TripLocationPing.objects.create(
                company_id=self.company.id,
                trip=trip,
                vehicle_id=self.vehicle.id,
                latitude=Decimal(lat),
                longitude=Decimal("77.600000"),
                recorded_at=now - timedelta(minutes=(3 - i)),
            )

        created = detect_stationary_vehicles()
        self.assertEqual(created, 0)


class AnomalyGeometryTests(TestCase):
    def test_haversine_distance_zero_for_identical_points(self):
        self.assertAlmostEqual(anomaly_detection.haversine_distance_m(12.9, 77.6, 12.9, 77.6), 0.0, places=3)

    def test_is_stationary_true_within_radius(self):
        pings = [
            SimpleNamespace(latitude=Decimal("12.900000"), longitude=Decimal("77.600000")),
            SimpleNamespace(latitude=Decimal("12.900050"), longitude=Decimal("77.600050")),
        ]
        self.assertTrue(anomaly_detection.is_stationary(pings, radius_m=50))

    def test_is_stationary_false_when_far_apart(self):
        pings = [
            SimpleNamespace(latitude=Decimal("12.900000"), longitude=Decimal("77.600000")),
            SimpleNamespace(latitude=Decimal("13.000000"), longitude=Decimal("77.700000")),
        ]
        self.assertFalse(anomaly_detection.is_stationary(pings, radius_m=50))


class LocationPingRetentionTests(TrackingTestBase):
    """Fix 7 — TripLocationPing data retention."""

    def setUp(self):
        super().setUp()
        self.trip, _ = self._make_assigned_trip()

    def _ping(self, recorded_at):
        return TripLocationPing.objects.create(
            company_id=self.company.id, trip=self.trip, vehicle_id=self.vehicle.id,
            latitude=Decimal("12.9"), longitude=Decimal("77.6"), recorded_at=recorded_at,
        )

    def test_purges_only_pings_older_than_the_retention_window(self):
        now = timezone.now()
        old_ping = self._ping(now - timedelta(days=91))
        recent_ping = self._ping(now - timedelta(days=10))

        deleted = purge_old_location_pings()

        self.assertEqual(deleted, 1)
        self.assertFalse(TripLocationPing.objects.filter(pk=old_ping.pk).exists())
        self.assertTrue(TripLocationPing.objects.filter(pk=recent_ping.pk).exists())

    @override_settings(LOCATION_PING_RETENTION_DAYS=30)
    def test_retention_window_is_configurable(self):
        now = timezone.now()
        self._ping(now - timedelta(days=45))  # older than the overridden 30-day window
        self._ping(now - timedelta(days=10))

        deleted = purge_old_location_pings()
        self.assertEqual(deleted, 1)

    def test_does_not_touch_anomaly_alerts(self):
        alert = TripAnomalyAlert.objects.create(
            company_id=self.company.id, trip=self.trip, vehicle_id=self.vehicle.id,
            alert_type=AnomalyType.STATIONARY_TOO_LONG, detected_at=timezone.now() - timedelta(days=200),
        )
        self._ping(timezone.now() - timedelta(days=200))

        purge_old_location_pings()

        self.assertTrue(TripAnomalyAlert.objects.filter(pk=alert.pk).exists())


class ShiftVarianceReconciliationTests(TrackingTestBase):
    """Remaining gaps #1 — KM variance reconciliation on end_shift."""

    def test_needs_variance_review_flips_true_past_the_threshold(self):
        trip, result = self._make_assigned_trip()
        trip_services.complete_stop(
            stop_id=result["drop_stop_id"], proof_photo_url="https://x.test/d.jpg", actor=self.actor
        )
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )
        shift.started_at = timezone.now() - timedelta(hours=2)
        shift.save(update_fields=["started_at"])

        # Odometer delta will be 100km; GPS-derived distance is only 10km —
        # a 90% variance, well past the default 20% threshold.
        TripLocationPing.objects.create(
            company_id=self.company.id, trip=trip, vehicle_id=self.vehicle.id,
            latitude=Decimal("12.9"), longitude=Decimal("77.6"),
            recorded_at=timezone.now() - timedelta(hours=1),
            distance_from_previous_km=Decimal("10.000"),
        )

        services.end_shift(
            shift_id=shift.id, end_odometer=Decimal("1100.00"),
            cleanliness_photo_url="https://x.test/clean.jpg",
            charging_plugged_photo_url="https://x.test/charge.jpg",
            actor=self.actor,
        )

        shift.refresh_from_db()
        self.assertEqual(shift.gps_distance_km, Decimal("10.00"))
        self.assertEqual(shift.variance_km, Decimal("90.00"))
        self.assertTrue(shift.needs_variance_review)

    def test_needs_variance_review_stays_false_within_the_threshold(self):
        trip, result = self._make_assigned_trip()
        trip_services.complete_stop(
            stop_id=result["drop_stop_id"], proof_photo_url="https://x.test/d.jpg", actor=self.actor
        )
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )
        shift.started_at = timezone.now() - timedelta(hours=2)
        shift.save(update_fields=["started_at"])

        # 100km odometer delta vs. 95km GPS-derived — a 5% variance, well
        # under the default 20% threshold.
        TripLocationPing.objects.create(
            company_id=self.company.id, trip=trip, vehicle_id=self.vehicle.id,
            latitude=Decimal("12.9"), longitude=Decimal("77.6"),
            recorded_at=timezone.now() - timedelta(hours=1),
            distance_from_previous_km=Decimal("95.000"),
        )

        services.end_shift(
            shift_id=shift.id, end_odometer=Decimal("1100.00"),
            cleanliness_photo_url="https://x.test/clean.jpg",
            charging_plugged_photo_url="https://x.test/charge.jpg",
            actor=self.actor,
        )

        shift.refresh_from_db()
        self.assertFalse(shift.needs_variance_review)


class GpsOfflineDetectionTests(TrackingTestBase):
    """Remaining gaps #2 — GPS/driver offline alert, distinct from the
    stationary-too-long check (pings arriving but not moving)."""

    def _put_trip_in_transit(self):
        trip, _ = self._make_assigned_trip()
        services.record_location_ping(
            trip_id=trip.id, latitude=Decimal("12.900000"), longitude=Decimal("77.600000"), speed_kmph=None,
            actor=self.actor,
        )
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.IN_TRANSIT)
        return trip

    def test_trip_level_alert_created_once_pings_stop_and_not_duplicated(self):
        trip = self._put_trip_in_transit()
        # Backdate the transition ping past the default 5-minute window —
        # simulates "no pings arriving at all" rather than a stationary vehicle.
        TripLocationPing.objects.filter(vehicle_id=self.vehicle.id).update(
            recorded_at=timezone.now() - timedelta(minutes=10)
        )

        created_first = detect_offline_vehicles()
        self.assertEqual(created_first, 1)
        alert = TripAnomalyAlert.objects.get(trip=trip, alert_type=AnomalyType.GPS_OFFLINE)
        self.assertIsNone(alert.shift)

        created_second = detect_offline_vehicles()
        self.assertEqual(created_second, 0, "must not create a duplicate alert on a second run")
        self.assertEqual(
            TripAnomalyAlert.objects.filter(trip=trip, alert_type=AnomalyType.GPS_OFFLINE).count(), 1
        )

    def test_no_alert_while_pings_are_still_arriving(self):
        self._put_trip_in_transit()
        self.assertEqual(detect_offline_vehicles(), 0)

    def test_shift_level_alert_when_no_active_trip_and_no_pings_at_all(self):
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )

        created = detect_offline_vehicles()
        self.assertEqual(created, 1)
        alert = TripAnomalyAlert.objects.get(alert_type=AnomalyType.GPS_OFFLINE)
        self.assertEqual(alert.shift_id, shift.id)
        self.assertIsNone(alert.trip)

        self.assertEqual(detect_offline_vehicles(), 0, "must not create a duplicate alert on a second run")


class DutyOnlyLocationTrackingTests(TrackingTestBase):
    """Remaining gaps #4 — a location ping is only accepted while the trip
    is genuinely active AND the driver is on duty (active trip or shift)."""

    def test_ping_rejected_when_trip_is_not_active(self):
        trip, _ = self._make_assigned_trip()  # pickups_locked, not yet in_transit
        trip.status = TripStatus.DELIVERED
        trip.save(update_fields=["status"])

        with self.assertRaises(DomainError) as ctx:
            services.record_location_ping(
                trip_id=trip.id, latitude=Decimal("12.9"), longitude=Decimal("77.6"), speed_kmph=None,
                actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "TRIP_NOT_ACTIVE")

    def test_ping_rejected_when_driver_has_no_active_trip_or_shift(self):
        trip, _ = self._make_assigned_trip()
        # Simulates a trip whose driver association was cleared/never set,
        # bypassing the normal assign_vehicle flow that always sets both.
        trip.driver = None
        trip.status = TripStatus.IN_TRANSIT
        trip.save(update_fields=["driver", "status"])

        with self.assertRaises(DomainError) as ctx:
            services.record_location_ping(
                trip_id=trip.id, latitude=Decimal("12.9"), longitude=Decimal("77.6"), speed_kmph=None,
                actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "NOT_ON_DUTY")
