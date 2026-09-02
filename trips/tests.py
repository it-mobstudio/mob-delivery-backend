from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient as DrfApiTestClient

from accounts.models import AdminUser, ApiClient, Company
from core.exceptions import DomainError
from drivers.models import Driver, VerificationStatus
from issues.models import TripIssue
from tenant_settings.services import set_tenant_setting
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import services
from .models import StopStatus, StopType, Trip, TripStatus, TripStop


def _addr(address="Address", lat="1.000000", lng="1.000000"):
    return {"address": address, "latitude": Decimal(lat), "longitude": Decimal(lng)}


class IntakeOrderTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme Logistics")
        self.actor = SimpleNamespace(company_id=self.company.id)

    def test_staggered_acceptance_groups_into_one_trip(self):
        results = []
        for i in range(1, 4):
            result = services.intake_order(
                company_id=self.company.id,
                order_ref=f"ORDER-{i}",
                parent_order_ref="PARENT-1",
                pickup=_addr(f"Pickup {i}"),
                delivery=_addr("Shared Delivery"),
                weight_kg=Decimal("5.00"),
                actor=self.actor,
            )
            results.append(result)

        trip_ids = {r["trip_id"] for r in results}
        self.assertEqual(len(trip_ids), 1, "all three suborders should land on the same trip, not three")

        trip = Trip.objects.get(pk=trip_ids.pop())
        self.assertEqual(trip.stops.filter(stop_type=StopType.PICKUP).count(), 3)
        self.assertEqual(trip.stops.filter(stop_type=StopType.DROP).count(), 1)
        self.assertEqual(trip.total_weight_kg, Decimal("15.00"))

    def test_intake_is_idempotent_on_order_ref(self):
        kwargs = dict(
            company_id=self.company.id,
            order_ref="DUP-1",
            parent_order_ref=None,
            pickup=_addr("Pickup"),
            delivery=_addr("Delivery"),
            weight_kg=Decimal("2.00"),
            actor=self.actor,
        )
        first = services.intake_order(**kwargs)
        second = services.intake_order(**kwargs)

        self.assertEqual(first, second)
        # One pickup + one drop stop — the drop reuses the pickup's order_ref
        # since TripStop.order_ref is required, so two rows is correct here;
        # the replay must not have created a second pickup/drop pair.
        self.assertEqual(TripStop.objects.filter(order_ref="DUP-1", stop_type=StopType.PICKUP).count(), 1)
        self.assertEqual(TripStop.objects.filter(order_ref="DUP-1").count(), 2)


class AssignVehicleTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme Logistics")
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
            registration_number="KA-01-AB-1234",
            capacity_kg=Decimal("500.00"),
        )
        self.driver = Driver.objects.create(
            company=self.company,
            full_name="Ravi Kumar",
            phone_number="+919999999999",
            emergency_contact_name="Sita",
            emergency_contact_phone="+918888888888",
            aadhar_status=VerificationStatus.VERIFIED,
            police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED,
            dl_expiry_date=date.today() + timedelta(days=365),
            dl_allowed_categories=[VehicleCategory.TWO_WHEELER],
        )
        result = services.intake_order(
            company_id=self.company.id,
            order_ref="ORD-1",
            parent_order_ref=None,
            pickup=_addr("Pickup"),
            delivery=_addr("Delivery"),
            weight_kg=Decimal("10.00"),
            actor=self.actor,
        )
        self.trip_id = result["trip_id"]

    def test_vehicle_type_mismatch_is_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            services.assign_vehicle(
                trip_id=self.trip_id, vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
            )
        self.assertEqual(ctx.exception.code, "VEHICLE_TYPE_NOT_PERMITTED")

    def test_matching_category_succeeds(self):
        self.driver.dl_allowed_categories = [VehicleCategory.FOUR_WHEELER]
        self.driver.save(update_fields=["dl_allowed_categories"])

        trip = services.assign_vehicle(
            trip_id=self.trip_id, vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )

        self.assertEqual(trip.vehicle_id, self.vehicle.id)
        self.assertEqual(trip.driver_id, self.driver.id)

        self.vehicle.refresh_from_db()
        self.driver.refresh_from_db()
        self.assertEqual(self.vehicle.current_driver_id, self.driver.id)
        self.assertEqual(self.driver.current_vehicle_id, self.vehicle.id)


class CompleteStopPhotoGateTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme Logistics")
        self.actor = SimpleNamespace(company_id=self.company.id)

        result = services.intake_order(
            company_id=self.company.id,
            order_ref="ORD-PG",
            parent_order_ref=None,
            pickup=_addr("Pickup"),
            delivery=_addr("Delivery"),
            weight_kg=Decimal("3.00"),
            actor=self.actor,
        )
        self.pickup_stop_id = result["pickup_stop_id"]
        self.drop_stop_id = result["drop_stop_id"]

    def test_first_stop_requires_photo(self):
        with self.assertRaises(DomainError) as ctx:
            services.complete_stop(stop_id=self.pickup_stop_id, proof_photo_url=None, actor=self.actor)
        self.assertEqual(ctx.exception.code, "PROOF_PHOTO_REQUIRED")

    def test_first_stop_succeeds_with_photo(self):
        stop = services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://example.com/p.jpg", actor=self.actor
        )
        self.assertEqual(stop.status, StopStatus.COMPLETED)

    def test_last_stop_requires_photo(self):
        services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://example.com/p.jpg", actor=self.actor
        )
        with self.assertRaises(DomainError) as ctx:
            services.complete_stop(stop_id=self.drop_stop_id, proof_photo_url=None, actor=self.actor)
        self.assertEqual(ctx.exception.code, "PROOF_PHOTO_REQUIRED")

    def test_last_stop_succeeds_with_photo_and_completes_trip(self):
        services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://example.com/p.jpg", actor=self.actor
        )
        stop = services.complete_stop(
            stop_id=self.drop_stop_id, proof_photo_url="https://example.com/d.jpg", actor=self.actor
        )

        self.assertEqual(stop.status, StopStatus.COMPLETED)
        trip = Trip.objects.get(pk=stop.trip_id)
        self.assertEqual(trip.status, TripStatus.DELIVERED)
        self.assertIsNotNone(trip.completed_at)


class DeliveryGeofenceTests(TestCase):
    """Fix 1 — soft flag only, never blocks completion."""

    def setUp(self):
        self.company = Company.objects.create(name="Geofence Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA18JJ0001",
            capacity_kg=Decimal("500"),
        )
        self.driver = Driver.objects.create(
            company=self.company, full_name="Geofence Driver", phone_number="+919980000001",
            emergency_contact_name="EC", emergency_contact_phone="+919980000002",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )

        result = services.intake_order(
            company_id=self.company.id, order_ref="ORD-GEO-1", parent_order_ref=None,
            # Stop registered at (1.000000, 1.000000).
            pickup=_addr("Pickup", lat="1.000000", lng="1.000000"), delivery=_addr("Delivery"),
            weight_kg=Decimal("3.00"), actor=self.actor,
        )
        self.pickup_stop_id = result["pickup_stop_id"]
        self.trip = services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )

    def _ping_at(self, lat, lng):
        from tracking.models import TripLocationPing

        return TripLocationPing.objects.create(
            company_id=self.company.id, trip=self.trip, vehicle_id=self.vehicle.id,
            latitude=Decimal(lat), longitude=Decimal(lng), recorded_at=timezone.now(),
        )

    def test_far_ping_flags_mismatch_but_still_completes(self):
        self._ping_at("1.004500", "1.000000")  # ~500m north of the stop

        stop = services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://x.test/p.jpg", actor=self.actor
        )

        self.assertEqual(stop.status, StopStatus.COMPLETED, "geofence mismatch must never block completion")
        self.assertTrue(stop.location_mismatch)
        self.assertGreater(stop.location_mismatch_meters, 100)

    def test_near_ping_does_not_flag(self):
        self._ping_at("1.000050", "1.000000")  # ~5.5m from the stop

        stop = services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://x.test/p.jpg", actor=self.actor
        )

        self.assertFalse(stop.location_mismatch)
        self.assertIsNone(stop.location_mismatch_meters)

    def test_no_recent_ping_does_not_flag(self):
        stop = services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://x.test/p.jpg", actor=self.actor
        )
        self.assertFalse(stop.location_mismatch)

    def test_stale_ping_outside_the_window_does_not_flag(self):
        from tracking.models import TripLocationPing

        TripLocationPing.objects.create(
            company_id=self.company.id, trip=self.trip, vehicle_id=self.vehicle.id,
            latitude=Decimal("1.004500"), longitude=Decimal("1.000000"),
            recorded_at=timezone.now() - timedelta(minutes=10),
        )

        stop = services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://x.test/p.jpg", actor=self.actor
        )
        self.assertFalse(stop.location_mismatch)

    def test_custom_tenant_geofence_threshold_is_consulted(self):
        set_tenant_setting(self.company.id, "geofence_meters", "1000")
        self._ping_at("1.004500", "1.000000")  # ~500m — under the widened 1000m threshold

        stop = services.complete_stop(
            stop_id=self.pickup_stop_id, proof_photo_url="https://x.test/p.jpg", actor=self.actor
        )
        self.assertFalse(stop.location_mismatch)


class CancelOrderStopTests(TestCase):
    """Scenario A — cancel a single suborder pre-pickup."""

    def setUp(self):
        self.company = Company.objects.create(name="Cancel Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

    def test_cancel_pending_suborder_in_a_group_keeps_trip_alive(self):
        for i in range(1, 3):
            services.intake_order(
                company_id=self.company.id, order_ref=f"CANCEL-GRP-{i}", parent_order_ref="CANCEL-PARENT",
                pickup=_addr(f"Pickup {i}"), delivery=_addr("Shared Delivery"),
                weight_kg=Decimal("5.00"), actor=self.actor,
            )

        result = services.cancel_order_stop(order_ref="CANCEL-GRP-1", reason="Customer changed their mind", actor=self.actor)

        trip = Trip.objects.get(pk=result["trip_id"])
        self.assertEqual(trip.status, TripStatus.COLLECTING_PICKUPS)
        self.assertEqual(trip.total_weight_kg, Decimal("5.00"))
        self.assertEqual(trip.stops.filter(stop_type=StopType.PICKUP).count(), 1)
        # The pickup stop itself is gone...
        self.assertFalse(
            TripStop.objects.filter(order_ref="CANCEL-GRP-1", stop_type=StopType.PICKUP).exists()
        )
        self.assertTrue(
            TripStop.all_objects.filter(order_ref="CANCEL-GRP-1", stop_type=StopType.PICKUP, is_deleted=True).exists()
        )
        # ...but the shared drop stop survives (reused from this suborder's
        # order_ref) since GRP-2's pickup still needs it.
        self.assertTrue(TripStop.objects.filter(order_ref="CANCEL-GRP-1", stop_type=StopType.DROP).exists())

    def test_cancel_only_pickup_cancels_whole_trip(self):
        result = services.intake_order(
            company_id=self.company.id, order_ref="CANCEL-SOLO-1", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("3.00"), actor=self.actor,
        )
        cancel_result = services.cancel_order_stop(order_ref="CANCEL-SOLO-1", reason="No longer needed", actor=self.actor)

        trip = Trip.objects.get(pk=result["trip_id"])
        self.assertEqual(trip.status, TripStatus.CANCELLED)
        self.assertEqual(cancel_result["trip_status"], TripStatus.CANCELLED)

    def test_cancel_after_pickup_is_rejected(self):
        result = services.intake_order(
            company_id=self.company.id, order_ref="CANCEL-PICKED-1", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("3.00"), actor=self.actor,
        )
        services.complete_stop(
            stop_id=result["pickup_stop_id"], proof_photo_url="https://example.com/p.jpg", actor=self.actor
        )

        with self.assertRaises(DomainError) as ctx:
            services.cancel_order_stop(order_ref="CANCEL-PICKED-1", reason="Too late now", actor=self.actor)
        self.assertEqual(ctx.exception.code, "CANNOT_CANCEL_AFTER_PICKUP")

    def test_cancel_unknown_order_ref(self):
        with self.assertRaises(DomainError) as ctx:
            services.cancel_order_stop(order_ref="DOES-NOT-EXIST", reason="n/a reason", actor=self.actor)
        self.assertEqual(ctx.exception.code, "ORDER_NOT_FOUND")


class CancelTripTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Cancel Trip Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA12GG0001",
            capacity_kg=Decimal("500"),
        )
        self.driver = Driver.objects.create(
            company=self.company, full_name="Cancel Driver", phone_number="+919990000001",
            emergency_contact_name="EC", emergency_contact_phone="+919990000002",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )

    def _assign_trip(self, order_ref):
        result = services.intake_order(
            company_id=self.company.id, order_ref=order_ref, parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("5.00"), actor=self.actor,
        )
        trip = services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )
        return trip, result


class CancelTripTests(CancelTripTestBase):
    """Scenario B — cancel an entire trip."""

    def test_cancel_with_completed_pickup_and_incomplete_drop_creates_issue(self):
        trip, result = self._assign_trip("CANCEL-TRIP-1")
        services.complete_stop(
            stop_id=result["pickup_stop_id"], proof_photo_url="https://example.com/p.jpg", actor=self.actor
        )

        cancelled_trip = services.cancel_trip(trip_id=trip.id, reason="Vehicle broke down badly", actor=self.actor)

        self.assertEqual(cancelled_trip.status, TripStatus.CANCELLED)

        issues = TripIssue.objects.filter(trip=trip)
        self.assertEqual(issues.count(), 1)
        issue = issues.first()
        self.assertEqual(issue.issue_type, "transit")
        self.assertEqual(issue.severity, "high")
        self.assertEqual(issue.trip_stop_id, result["pickup_stop_id"])
        self.assertIn("requires manual reassignment", issue.note)

        self.vehicle.refresh_from_db()
        self.driver.refresh_from_db()
        self.assertIsNone(self.vehicle.current_driver_id)
        self.assertIsNone(self.driver.current_vehicle_id)

    def test_cancel_without_completed_pickups_creates_no_issue(self):
        trip, _ = self._assign_trip("CANCEL-TRIP-2")

        cancelled_trip = services.cancel_trip(trip_id=trip.id, reason="Order no longer needed", actor=self.actor)

        self.assertEqual(cancelled_trip.status, TripStatus.CANCELLED)
        self.assertEqual(TripIssue.objects.filter(trip=trip).count(), 0)

    def test_cancel_delivered_trip_is_rejected(self):
        trip, result = self._assign_trip("CANCEL-TRIP-3")
        services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url="https://example.com/p.jpg", actor=self.actor)
        services.complete_stop(stop_id=result["drop_stop_id"], proof_photo_url="https://example.com/d.jpg", actor=self.actor)

        with self.assertRaises(DomainError) as ctx:
            services.cancel_trip(trip_id=trip.id, reason="Too late, already delivered", actor=self.actor)
        self.assertEqual(ctx.exception.code, "INVALID_TRIP_STATUS")

    def test_cancelling_twice_is_rejected(self):
        trip, _ = self._assign_trip("CANCEL-TRIP-4")
        services.cancel_trip(trip_id=trip.id, reason="First cancellation", actor=self.actor)

        with self.assertRaises(DomainError) as ctx:
            services.cancel_trip(trip_id=trip.id, reason="Second cancellation attempt", actor=self.actor)
        self.assertEqual(ctx.exception.code, "INVALID_TRIP_STATUS")


class CancelPermissionHttpTests(CancelTripTestBase):
    def setUp(self):
        super().setUp()
        self.admin = AdminUser.objects.create_user(
            email="canceladmin@test.invalid", company=self.company, password="pass12345"
        )
        self.api_client_principal = ApiClient(company=self.company, name="Partner Integration")
        self.api_client_principal.set_secret("dummy-secret")
        self.api_client_principal.save()

    def test_trip_cancel_rejects_api_client(self):
        trip, _ = self._assign_trip("CANCEL-HTTP-1")
        client = DrfApiTestClient()
        client.force_authenticate(user=self.api_client_principal)
        r = client.post(f"/api/v1/trips/{trip.id}/cancel", {"reason": "Should not be allowed"}, format="json")
        self.assertEqual(r.status_code, 403)

        trip.refresh_from_db()
        self.assertNotEqual(trip.status, TripStatus.CANCELLED)

    def test_trip_cancel_allows_admin(self):
        trip, _ = self._assign_trip("CANCEL-HTTP-2")
        client = DrfApiTestClient()
        client.force_authenticate(user=self.admin)
        r = client.post(f"/api/v1/trips/{trip.id}/cancel", {"reason": "Admin cancelling this trip"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "cancelled")

    def test_order_cancel_allows_api_client(self):
        services.intake_order(
            company_id=self.company.id, order_ref="CANCEL-HTTP-ORDER-1", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("2.00"), actor=self.actor,
        )
        client = DrfApiTestClient()
        client.force_authenticate(user=self.api_client_principal)
        r = client.post(
            "/api/v1/orders/CANCEL-HTTP-ORDER-1/cancel", {"reason": "Customer cancelled via app"}, format="json"
        )
        self.assertEqual(r.status_code, 200)


class StaggeredAcceptanceAfterLockTests(TestCase):
    """Production-readiness Part 3, Trips & Assignment section."""

    def setUp(self):
        self.company = Company.objects.create(name="Late Suborder Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

    def test_suborder_accepted_after_sibling_trip_is_locked_does_not_attach(self):
        first = services.intake_order(
            company_id=self.company.id, order_ref="LATE-1", parent_order_ref="LATE-PARENT",
            pickup=_addr("Pickup 1"), delivery=_addr("Shared Delivery"),
            weight_kg=Decimal("5.00"), actor=self.actor,
        )
        locked_trip = Trip.objects.get(pk=first["trip_id"])
        TripStop.objects.filter(pk=first["pickup_stop_id"]).update(status=StopStatus.COMPLETED)
        services.lock_pickups(trip_id=locked_trip.id, actor=self.actor)
        locked_trip.refresh_from_db()
        self.assertEqual(locked_trip.status, TripStatus.PICKUPS_LOCKED)

        late = services.intake_order(
            company_id=self.company.id, order_ref="LATE-2", parent_order_ref="LATE-PARENT",
            pickup=_addr("Pickup 2"), delivery=_addr("Shared Delivery"),
            weight_kg=Decimal("5.00"), actor=self.actor,
        )

        self.assertNotEqual(
            late["trip_id"], locked_trip.id,
            "a suborder arriving after its sibling trip is pickups_locked must not silently attach to it",
        )
        # And it must not be silently dropped either — it lands on its own new trip.
        new_trip = Trip.objects.get(pk=late["trip_id"])
        self.assertEqual(new_trip.status, TripStatus.COLLECTING_PICKUPS)
        self.assertEqual(new_trip.stops.filter(stop_type=StopType.PICKUP).count(), 1)


class ReassignVehicleTests(TestCase):
    """Production-readiness Part 3, Trips & Assignment section — reassignment
    must write a complete TripVehicleHistory row (old AND new vehicle/driver).
    """

    def setUp(self):
        self.company = Company.objects.create(name="Reassignment Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )

        def _make_driver(suffix):
            return Driver.objects.create(
                company=self.company, full_name=f"Driver {suffix}", phone_number=f"+91900000{suffix}",
                emergency_contact_name="EC", emergency_contact_phone="+918888888888",
                aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
                dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=365),
                dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
            )

        self.old_vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type,
            registration_number="KA-01-OLD-0001", capacity_kg=Decimal("500.00"),
        )
        self.new_vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type,
            registration_number="KA-01-NEW-0002", capacity_kg=Decimal("500.00"),
        )
        self.old_driver = _make_driver("0001")
        self.new_driver = _make_driver("0002")

        result = services.intake_order(
            company_id=self.company.id, order_ref="REASSIGN-1", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("10.00"), actor=self.actor,
        )
        self.trip = services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.old_vehicle.id, driver_id=self.old_driver.id,
            actor=self.actor,
        )

    def test_reassignment_writes_a_complete_history_row_and_updates_the_trip(self):
        from .models import TripVehicleHistory

        trip = services.reassign_vehicle(
            trip_id=self.trip.id, new_vehicle_id=self.new_vehicle.id, new_driver_id=self.new_driver.id,
            reason="Old vehicle broke down", actor=self.actor,
        )

        self.assertEqual(trip.vehicle_id, self.new_vehicle.id)
        self.assertEqual(trip.driver_id, self.new_driver.id)

        history = TripVehicleHistory.objects.get(trip=trip)
        self.assertEqual(history.previous_vehicle_id, self.old_vehicle.id)
        self.assertEqual(history.new_vehicle_id, self.new_vehicle.id)
        self.assertEqual(history.previous_driver_id, self.old_driver.id)
        self.assertEqual(history.new_driver_id, self.new_driver.id)
        self.assertEqual(history.reason, "Old vehicle broke down")

        self.old_vehicle.refresh_from_db()
        self.old_driver.refresh_from_db()
        self.new_vehicle.refresh_from_db()
        self.new_driver.refresh_from_db()
        self.assertIsNone(self.old_vehicle.current_driver_id)
        self.assertIsNone(self.old_driver.current_vehicle_id)
        self.assertEqual(self.new_vehicle.current_driver_id, self.new_driver.id)
        self.assertEqual(self.new_driver.current_vehicle_id, self.new_vehicle.id)

    def test_reassignment_publishes_a_webhook_event_per_affected_order(self):
        from webhooks.models import WebhookEvent

        services.reassign_vehicle(
            trip_id=self.trip.id, new_vehicle_id=self.new_vehicle.id, new_driver_id=self.new_driver.id,
            reason="Old vehicle broke down", actor=self.actor,
        )

        event = WebhookEvent.objects.get(order_ref="REASSIGN-1", event_type="order.driver_reassigned")
        self.assertEqual(event.payload["newDriverName"], self.new_driver.full_name)
        self.assertEqual(event.payload["reason"], "Old vehicle broke down")


class AssignmentCandidatesEtaTests(TestCase):
    """Point 8 — vehicles busy on an in-transit trip but expected to free up
    within the requested window should still be offered as candidates."""

    def setUp(self):
        from tracking.models import TripLocationPing

        self.TripLocationPing = TripLocationPing
        self.company = Company.objects.create(name="ETA Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type,
            registration_number="KA-01-ETA-0001", capacity_kg=Decimal("500.00"),
        )
        self.driver = Driver.objects.create(
            company=self.company, full_name="Eta Driver", phone_number="+919000000099",
            emergency_contact_name="EC", emergency_contact_phone="+918888888899",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=365),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )

        # Second incoming order looking for a vehicle.
        self.incoming = services.intake_order(
            company_id=self.company.id, order_ref="ETA-INCOMING", parent_order_ref=None,
            pickup=_addr("New Pickup"), delivery=_addr("New Delivery"), weight_kg=Decimal("5.00"),
            actor=self.actor,
        )

    def _put_vehicle_in_transit(self, ping_lat, ping_lng, remaining_stop_lat, remaining_stop_lng):
        """Assigns the vehicle to its own trip, completes the pickup (so
        only the delivery stop remains), puts it in transit, and drops a
        ping at (ping_lat, ping_lng). What actually drives the ETA is the
        distance from that ping to the remaining delivery stop — not
        anything about the *new* incoming order these tests are matching
        against.
        """
        result = services.intake_order(
            company_id=self.company.id, order_ref="ETA-CURRENT", parent_order_ref=None,
            pickup=_addr("Current Pickup", ping_lat, ping_lng),
            delivery=_addr("Current Delivery", remaining_stop_lat, remaining_stop_lng),
            weight_kg=Decimal("5.00"), actor=self.actor,
        )
        services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url="https://x.test/p.jpg", actor=self.actor)
        services.lock_pickups(trip_id=result["trip_id"], actor=self.actor)
        trip = services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )
        # Deliberately not tracking.services.record_location_ping — it also
        # writes a live-location cache key for realtime broadcast, which
        # isn't what this ETA logic reads. Replicating just the DB-visible
        # effect (status flip + a stored ping) is what's actually under test.
        trip.status = TripStatus.IN_TRANSIT
        trip.save(update_fields=["status"])
        self.TripLocationPing.objects.create(
            company_id=self.company.id, trip=trip, vehicle_id=self.vehicle.id,
            latitude=Decimal(ping_lat), longitude=Decimal(ping_lng), recorded_at=timezone.now(),
        )
        return trip

    def test_free_vehicle_is_available_now(self):
        result = services.get_assignment_candidates(company_id=self.company.id, trip_id=self.incoming["trip_id"])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["available_in_minutes"], 0)

    def test_vehicle_assigned_but_not_yet_moving_has_no_honest_eta_and_is_excluded(self):
        result = services.intake_order(
            company_id=self.company.id, order_ref="ETA-NOT-MOVING", parent_order_ref=None,
            pickup=_addr("P"), delivery=_addr("D"), weight_kg=Decimal("5.00"), actor=self.actor,
        )
        services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url="https://x.test/p.jpg", actor=self.actor)
        services.lock_pickups(trip_id=result["trip_id"], actor=self.actor)
        services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )
        # Pickups locked but no location ping yet — hasn't started moving.

        matches = services.get_assignment_candidates(company_id=self.company.id, trip_id=self.incoming["trip_id"])
        self.assertEqual(matches["matches"], [])

    def test_vehicle_nearby_and_in_transit_is_offered_with_a_positive_eta(self):
        # Ping and its remaining delivery stop are ~2km apart.
        self._put_vehicle_in_transit("1.000000", "1.000000", "1.018000", "1.000000")

        result = services.get_assignment_candidates(
            company_id=self.company.id, trip_id=self.incoming["trip_id"], within_minutes=30
        )
        self.assertEqual(len(result["matches"]), 1)
        self.assertGreater(result["matches"][0]["available_in_minutes"], 0)
        self.assertLessEqual(result["matches"][0]["available_in_minutes"], 30)

    def test_vehicle_far_away_and_in_transit_exceeds_the_window_and_is_excluded(self):
        # Ping and its remaining delivery stop are hundreds of km apart.
        self._put_vehicle_in_transit("1.000000", "1.000000", "15.000000", "80.000000")

        result = services.get_assignment_candidates(
            company_id=self.company.id, trip_id=self.incoming["trip_id"], within_minutes=30
        )
        self.assertEqual(result["matches"], [])

    def test_vehicle_in_transit_with_no_recent_ping_is_excluded(self):
        result = services.intake_order(
            company_id=self.company.id, order_ref="ETA-STALE", parent_order_ref=None,
            pickup=_addr("P", "1.000100", "1.000100"), delivery=_addr("D", "1.000100", "1.000100"),
            weight_kg=Decimal("5.00"), actor=self.actor,
        )
        services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url="https://x.test/p.jpg", actor=self.actor)
        services.lock_pickups(trip_id=result["trip_id"], actor=self.actor)
        trip = services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )
        # Already in transit, but its only ping is well outside ETA_PING_FRESHNESS.
        trip.status = TripStatus.IN_TRANSIT
        trip.save(update_fields=["status"])
        self.TripLocationPing.objects.create(
            company_id=self.company.id, trip=trip, vehicle_id=self.vehicle.id,
            latitude=Decimal("1.000100"), longitude=Decimal("1.000100"),
            recorded_at=timezone.now() - timedelta(minutes=45),
        )

        result = services.get_assignment_candidates(company_id=self.company.id, trip_id=self.incoming["trip_id"])
        self.assertEqual(result["matches"], [])

    def test_wider_tenant_eta_speed_shrinks_the_estimate(self):
        # ~50km between the ping and the remaining delivery stop, so the
        # configured speed actually moves the estimate.
        self._put_vehicle_in_transit("2.000000", "2.000000", "2.450000", "2.000000")

        default_result = services.get_assignment_candidates(
            company_id=self.company.id, trip_id=self.incoming["trip_id"], within_minutes=999
        )
        default_minutes = default_result["matches"][0]["available_in_minutes"]

        set_tenant_setting(self.company.id, "assignment_eta_speed_kmph", "250")
        faster_result = services.get_assignment_candidates(
            company_id=self.company.id, trip_id=self.incoming["trip_id"], within_minutes=999
        )
        faster_minutes = faster_result["matches"][0]["available_in_minutes"]

        self.assertLess(faster_minutes, default_minutes)
