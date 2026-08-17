from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from core.exceptions import DomainError
from drivers.models import Driver, VerificationStatus
from tenant_settings.services import set_tenant_setting
from trips import services as trip_services
from trips.models import StopType, TripStatus
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import anomaly_detection, services
from .models import AnomalyType, ShiftStatus, TripAnomalyAlert, TripLocationPing, VehicleStartPoint
from .tasks import detect_stationary_vehicles, purge_old_location_pings


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


class FixedStartPointTests(TrackingTestBase):
    """Point 6 gap fix — optional fixed start point on shift start."""

    def test_start_shift_without_start_point_leaves_fields_null(self):
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"), actor=self.actor
        )
        self.assertIsNone(shift.fixed_start_latitude)
        self.assertIsNone(shift.fixed_start_longitude)
        self.assertIsNone(shift.fixed_start_label)

    def test_start_shift_with_start_point_copies_fields(self):
        start_point = VehicleStartPoint.objects.create(
            company=self.company, label="Koramangala Hub", latitude=Decimal("12.935200"), longitude=Decimal("77.624600")
        )
        shift = services.start_shift(
            driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"),
            actor=self.actor, start_point_id=start_point.id,
        )
        self.assertEqual(shift.fixed_start_latitude, Decimal("12.935200"))
        self.assertEqual(shift.fixed_start_longitude, Decimal("77.624600"))
        self.assertEqual(shift.fixed_start_label, "Koramangala Hub")

    def test_unknown_start_point_id_is_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            services.start_shift(
                driver_id=self.driver.id, vehicle_id=self.vehicle.id, start_odometer=Decimal("1000.00"),
                actor=self.actor, start_point_id="00000000-0000-0000-0000-000000000000",
            )
        self.assertEqual(ctx.exception.code, "START_POINT_NOT_FOUND")


class StartPointHttpTests(TrackingTestBase):
    def setUp(self):
        super().setUp()
        self.admin = AdminUser.objects.create_user(
            email="startpointadmin@test.invalid", company=self.company, password="pass12345"
        )

    def test_admin_crud_and_shift_start_referencing_it(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)

        r1 = client.post("/api/v1/start-points", {
            "label": "Whitefield Hub", "latitude": "12.969500", "longitude": "77.749800",
        }, format="json")
        self.assertEqual(r1.status_code, 201)
        start_point_id = r1.data["id"]

        r2 = client.get("/api/v1/start-points")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["count"], 1)

        r3 = client.post("/api/v1/shifts/start", {
            "driver_id": str(self.driver.id), "vehicle_id": str(self.vehicle.id),
            "start_odometer": "500.00", "start_point_id": start_point_id,
        }, format="json")
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.data["fixed_start_label"], "Whitefield Hub")

    def test_driver_cannot_manage_start_points(self):
        client = APIClient()
        client.force_authenticate(user=self.driver)
        r = client.post("/api/v1/start-points", {
            "label": "Sneaky Hub", "latitude": "12.9", "longitude": "77.6",
        }, format="json")
        self.assertEqual(r.status_code, 403)


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
