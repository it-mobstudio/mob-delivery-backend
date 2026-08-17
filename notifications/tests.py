from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from drivers import services as driver_services
from drivers.models import Driver, VerificationStatus
from drivers.tasks import lock_expired_driver_licenses
from sos import services as sos_services
from trips import services as trip_services
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import services
from .models import DevicePlatform, DriverDevice


def _addr(address="Address", lat="12.900000", lng="77.600000"):
    return {"address": address, "latitude": Decimal(lat), "longitude": Decimal(lng)}


class NotificationsTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Push Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)
        self.driver = Driver.objects.create(
            company=self.company, full_name="Push Driver", phone_number="+919111000001",
            emergency_contact_name="EC", emergency_contact_phone="+919111000002",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        self.admin = AdminUser.objects.create_user(
            email="pushadmin@test.invalid", company=self.company, password="pass12345"
        )


class RegisterDeviceServiceTests(NotificationsTestBase):
    def test_register_creates_new_device(self):
        device = services.register_device(self.driver, "token-1", DevicePlatform.ANDROID)
        self.assertEqual(DriverDevice.objects.filter(driver=self.driver).count(), 1)
        self.assertEqual(device.fcm_token, "token-1")

    def test_register_twice_with_same_token_upserts_not_duplicates(self):
        services.register_device(self.driver, "token-1", DevicePlatform.ANDROID)
        first_seen = DriverDevice.objects.get(driver=self.driver, fcm_token="token-1").last_active_at

        services.register_device(self.driver, "token-1", DevicePlatform.ANDROID)

        self.assertEqual(DriverDevice.objects.filter(driver=self.driver, fcm_token="token-1").count(), 1)
        second_seen = DriverDevice.objects.get(driver=self.driver, fcm_token="token-1").last_active_at
        self.assertGreaterEqual(second_seen, first_seen)

    def test_second_distinct_token_is_a_second_device(self):
        services.register_device(self.driver, "token-1", DevicePlatform.ANDROID)
        services.register_device(self.driver, "token-2", DevicePlatform.IOS)
        self.assertEqual(DriverDevice.objects.filter(driver=self.driver).count(), 2)

    def test_deregister_removes_the_device(self):
        services.register_device(self.driver, "token-1", DevicePlatform.ANDROID)
        services.deregister_device(self.driver, "token-1")
        self.assertFalse(DriverDevice.objects.filter(driver=self.driver, fcm_token="token-1").exists())


class SendPushToDriverTests(NotificationsTestBase):
    def setUp(self):
        super().setUp()
        services.register_device(self.driver, "good-token", DevicePlatform.ANDROID)
        services.register_device(self.driver, "dead-token", DevicePlatform.IOS)

    @patch("notifications.services.messaging.send")
    def test_sends_to_every_registered_device(self, mock_send):
        services.send_push_to_driver(self.driver.id, "Title", "Body", data={"type": "test"})
        self.assertEqual(mock_send.call_count, 2)

    @patch("notifications.services.messaging.send")
    def test_unregistered_token_is_pruned(self, mock_send):
        from firebase_admin import messaging

        def side_effect(message, *args, **kwargs):
            if message.token == "dead-token":
                raise messaging.UnregisteredError("gone")
            return "ok"

        mock_send.side_effect = side_effect

        services.send_push_to_driver(self.driver.id, "Title", "Body")

        self.assertFalse(DriverDevice.objects.filter(driver=self.driver, fcm_token="dead-token").exists())
        self.assertTrue(DriverDevice.objects.filter(driver=self.driver, fcm_token="good-token").exists())

    @patch("notifications.services.messaging.send")
    def test_generic_failure_is_swallowed_not_raised(self, mock_send):
        mock_send.side_effect = RuntimeError("network exploded")
        try:
            services.send_push_to_driver(self.driver.id, "Title", "Body")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"send_push_to_driver must never raise, but raised: {exc}")

    @patch("notifications.services.messaging.send")
    def test_no_registered_devices_is_a_silent_noop(self, mock_send):
        other_driver = Driver.objects.create(
            company=self.company, full_name="No Device Driver", phone_number="+919111000099",
            emergency_contact_name="EC", emergency_contact_phone="+919111000098",
        )
        services.send_push_to_driver(other_driver.id, "Title", "Body")
        mock_send.assert_not_called()


class DeviceHttpTests(NotificationsTestBase):
    def test_driver_can_register_and_deregister_via_http(self):
        client = APIClient()
        client.force_authenticate(user=self.driver)

        r1 = client.post("/api/v1/driver/devices/register", {
            "fcm_token": "http-token-1", "platform": "android",
        }, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertTrue(DriverDevice.objects.filter(driver=self.driver, fcm_token="http-token-1").exists())

        r2 = client.delete("/api/v1/driver/devices/http-token-1")
        self.assertEqual(r2.status_code, 204)
        self.assertFalse(DriverDevice.objects.filter(driver=self.driver, fcm_token="http-token-1").exists())

    def test_admin_cannot_register_a_device(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.post("/api/v1/driver/devices/register", {
            "fcm_token": "sneaky-token", "platform": "android",
        }, format="json")
        self.assertEqual(r.status_code, 403)


class RetrofitPointsTests(NotificationsTestBase):
    """Confirms all eight notification call sites actually fire — patches
    the shared task object once so it doesn't matter which module imported
    it (they all bind the same underlying object)."""

    def setUp(self):
        super().setUp()
        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA16RR0001",
            capacity_kg=Decimal("500"),
        )

    def _assigned_trip(self, order_ref):
        result = trip_services.intake_order(
            company_id=self.company.id, order_ref=order_ref, parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("4.0"), actor=self.actor,
        )
        trip = trip_services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )
        return trip, result

    @patch("notifications.tasks.send_push_notification.delay")
    def test_assign_vehicle_notifies_driver(self, mock_delay):
        # assign_vehicle registers its notification via transaction.on_commit,
        # which never fires inside TestCase's wrapping (never-committed)
        # transaction unless explicitly captured.
        with self.captureOnCommitCallbacks(execute=True):
            self._assigned_trip("PUSH-ORD-1")
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs["driver_id"], self.driver.id)
        self.assertEqual(mock_delay.call_args.kwargs["data"]["type"], "trip_assigned")

    @patch("notifications.tasks.send_push_notification.delay")
    def test_reassign_vehicle_notifies_both_old_and_new_driver(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            trip, _ = self._assigned_trip("PUSH-ORD-2")
        mock_delay.reset_mock()

        new_driver = Driver.objects.create(
            company=self.company, full_name="New Push Driver", phone_number="+919111000077",
            emergency_contact_name="EC", emergency_contact_phone="+919111000076",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        with self.captureOnCommitCallbacks(execute=True):
            trip_services.reassign_vehicle(
                trip_id=trip.id, new_vehicle_id=self.vehicle.id, new_driver_id=new_driver.id,
                reason="Original driver called in sick", actor=self.actor,
            )

        self.assertEqual(mock_delay.call_count, 2)
        notified_driver_ids = {call.kwargs["driver_id"] for call in mock_delay.call_args_list}
        notified_types = {call.kwargs["data"]["type"] for call in mock_delay.call_args_list}
        self.assertEqual(notified_driver_ids, {self.driver.id, new_driver.id})
        self.assertEqual(notified_types, {"trip_reassigned_away", "trip_reassigned_to"})

    @patch("notifications.tasks.send_push_notification.delay")
    def test_reassign_vehicle_same_driver_notifies_nobody(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            trip, _ = self._assigned_trip("PUSH-ORD-3")
        mock_delay.reset_mock()

        other_vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA16RR0002",
            capacity_kg=Decimal("500"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            trip_services.reassign_vehicle(
                trip_id=trip.id, new_vehicle_id=other_vehicle.id, reason="Vehicle broke down", actor=self.actor,
            )
        mock_delay.assert_not_called()

    @patch("notifications.tasks.send_push_notification.delay")
    def test_update_delivery_address_notifies_driver(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            trip, result = self._assigned_trip("PUSH-ORD-4")
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            trip_services.update_delivery_address(
                stop_id=result["drop_stop_id"], new_address="New Address", new_lat=Decimal("13.0"),
                new_lng=Decimal("77.7"), actor=self.actor,
            )
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs["driver_id"], self.driver.id)
        self.assertEqual(mock_delay.call_args.kwargs["data"]["type"], "delivery_address_updated")

    @patch("notifications.tasks.send_push_notification.delay")
    def test_kyc_rejection_notifies_driver_verified_does_not(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            driver_services.verify_aadhar(
                self.driver, VerificationStatus.REJECTED, self.admin.id, note="Blurry photo"
            )
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs["data"]["type"], "kyc_rejected")
        self.assertEqual(mock_delay.call_args.kwargs["data"]["doc_type"], "Aadhar")

        mock_delay.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            driver_services.verify_police(self.driver, VerificationStatus.VERIFIED, self.admin.id)
        mock_delay.assert_not_called()

    @patch("notifications.tasks.send_push_notification.delay")
    def test_dl_expiry_lock_notifies_each_locked_driver(self, mock_delay):
        self.driver.dl_expiry_date = date.today() - timedelta(days=1)
        self.driver.save(update_fields=["dl_expiry_date"])

        lock_expired_driver_licenses()

        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs["driver_id"], self.driver.id)
        self.assertEqual(mock_delay.call_args.kwargs["data"]["type"], "account_locked")

    @patch("notifications.tasks.send_push_notification.delay")
    def test_sos_acknowledge_and_resolve_notify_driver(self, mock_delay):
        alert = sos_services.trigger_sos(driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"))

        sos_services.acknowledge_sos(alert_id=alert.id, actor=self.admin)
        self.assertEqual(mock_delay.call_args.kwargs["data"]["type"], "sos_acknowledged")

        sos_services.resolve_sos(alert_id=alert.id, resolution_note="False alarm", actor=self.admin)
        self.assertEqual(mock_delay.call_args.kwargs["data"]["type"], "sos_resolved")

        self.assertEqual(mock_delay.call_count, 2)


class PushFailureDoesNotBlockTheTriggeringOperationTests(NotificationsTestBase):
    """Production-readiness Part 3, Push Notifications section. Unlike
    RetrofitPointsTests above (which mocks send_push_notification.delay
    itself), this runs the real task end to end — CELERY_TASK_ALWAYS_EAGER
    (settings_test.py) makes .delay() execute synchronously in-process, and
    a simulated Firebase failure inside it must still leave trip assignment
    fully committed, not rolled back or raised.
    """

    def setUp(self):
        super().setUp()
        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA16RR9999",
            capacity_kg=Decimal("500"),
        )
        services.register_device(self.driver, "some-token", DevicePlatform.ANDROID)

    @patch("notifications.services.messaging.send")
    def test_trip_assignment_succeeds_even_when_the_push_notification_fails(self, mock_send):
        mock_send.side_effect = RuntimeError("invalid Firebase config")

        result = trip_services.intake_order(
            company_id=self.company.id, order_ref="PUSH-FAIL-1", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("4.0"), actor=self.actor,
        )

        with self.captureOnCommitCallbacks(execute=True):
            trip = trip_services.assign_vehicle(
                trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
            )

        self.assertTrue(mock_send.called, "the push attempt must actually have been made (and failed)")

        trip.refresh_from_db()
        self.vehicle.refresh_from_db()
        self.driver.refresh_from_db()
        self.assertEqual(trip.vehicle_id, self.vehicle.id)
        self.assertEqual(trip.driver_id, self.driver.id)
        self.assertEqual(self.vehicle.current_driver_id, self.driver.id)
        self.assertEqual(self.driver.current_vehicle_id, self.vehicle.id)
