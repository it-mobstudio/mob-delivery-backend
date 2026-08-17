import asyncio
import time
from datetime import date, timedelta
from decimal import Decimal

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from accounts.serializers import AdminTokenObtainPairSerializer
from core.exceptions import DomainError
from drivers.models import Driver, DriverAccountStatus, VerificationStatus
from trips import services as trip_services
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import services
from .models import SosStatus
from .routing import websocket_urlpatterns


def _addr(address="Address", lat="12.900000", lng="77.600000"):
    return {"address": address, "latitude": Decimal(lat), "longitude": Decimal(lng)}


class SosTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="SOS Test Co")
        self.driver = Driver.objects.create(
            company=self.company, full_name="SOS Driver", phone_number="+919555000001",
            emergency_contact_name="EC", emergency_contact_phone="+919555000002",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        self.admin = AdminUser.objects.create_user(
            email="sosadmin@test.invalid", company=self.company, password="pass12345"
        )


class TriggerSosServiceTests(SosTestBase):
    def test_locked_driver_can_still_trigger_sos(self):
        self.driver.account_status = DriverAccountStatus.LOCKED_DL_EXPIRED
        self.driver.save(update_fields=["account_status"])

        alert = services.trigger_sos(driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"))

        self.assertEqual(alert.status, SosStatus.ACTIVE)
        self.assertEqual(alert.driver_id, self.driver.id)
        self.assertIsNone(alert.trip_id)

    def test_disabled_driver_can_still_trigger_sos(self):
        self.driver.account_status = DriverAccountStatus.DISABLED
        self.driver.save(update_fields=["account_status"])

        alert = services.trigger_sos(driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"))
        self.assertEqual(alert.status, SosStatus.ACTIVE)

    def test_valid_trip_id_is_linked(self):
        result = trip_services.intake_order(
            company_id=self.company.id, order_ref="SOS-ORD-1", parent_order_ref=None,
            pickup=_addr("P"), delivery=_addr("D"), weight_kg=Decimal("2.0"), actor=self.driver,
        )
        alert = services.trigger_sos(
            driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"), trip_id=result["trip_id"]
        )
        self.assertEqual(alert.trip_id, result["trip_id"])

    def test_bogus_trip_id_does_not_fail_the_write(self):
        bogus_trip_id = "00000000-0000-0000-0000-000000000000"
        alert = services.trigger_sos(
            driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"), trip_id=bogus_trip_id
        )
        self.assertEqual(alert.status, SosStatus.ACTIVE)
        self.assertIsNone(alert.trip_id)


class AcknowledgeResolveServiceTests(SosTestBase):
    def setUp(self):
        super().setUp()
        self.alert = services.trigger_sos(driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"))

    def test_acknowledge_sets_fields(self):
        acknowledged = services.acknowledge_sos(alert_id=self.alert.id, actor=self.admin)
        self.assertEqual(acknowledged.status, SosStatus.ACKNOWLEDGED)
        self.assertEqual(acknowledged.acknowledged_by, self.admin.id)
        self.assertIsNotNone(acknowledged.acknowledged_at)

    def test_resolve_sets_fields(self):
        resolved = services.resolve_sos(alert_id=self.alert.id, resolution_note="False alarm", actor=self.admin)
        self.assertEqual(resolved.status, SosStatus.RESOLVED)
        self.assertEqual(resolved.resolved_by, self.admin.id)
        self.assertEqual(resolved.resolution_note, "False alarm")

    def test_unknown_alert_raises_not_found(self):
        with self.assertRaises(DomainError) as ctx:
            services.acknowledge_sos(alert_id="00000000-0000-0000-0000-000000000000", actor=self.admin)
        self.assertEqual(ctx.exception.code, "SOS_ALERT_NOT_FOUND")


class SosHttpTests(SosTestBase):
    def test_locked_driver_triggers_sos_via_http(self):
        self.driver.account_status = DriverAccountStatus.LOCKED_DL_EXPIRED
        self.driver.save(update_fields=["account_status"])

        client = APIClient()
        client.force_authenticate(user=self.driver)
        r = client.post("/api/v1/sos", {"latitude": "12.9", "longitude": "77.6"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["status"], "active")

    def test_out_of_range_latitude_is_rejected(self):
        client = APIClient()
        client.force_authenticate(user=self.driver)
        r = client.post("/api/v1/sos", {"latitude": "999", "longitude": "77.6"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_admin_cannot_trigger_sos(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.post("/api/v1/sos", {"latitude": "12.9", "longitude": "77.6"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_admin_can_list_and_driver_cannot_acknowledge(self):
        services.trigger_sos(driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"))

        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        r_list = admin_client.get("/api/v1/sos")
        self.assertEqual(r_list.status_code, 200)
        self.assertEqual(r_list.data["count"], 1)

        alert_id = r_list.data["results"][0]["id"]

        driver_client = APIClient()
        driver_client.force_authenticate(user=self.driver)
        r_ack = driver_client.post(f"/api/v1/sos/{alert_id}/acknowledge")
        self.assertEqual(r_ack.status_code, 403)

        r_ack_admin = admin_client.post(f"/api/v1/sos/{alert_id}/acknowledge")
        self.assertEqual(r_ack_admin.status_code, 200)
        self.assertEqual(r_ack_admin.data["status"], "acknowledged")


class SosWebsocketBroadcastTests(TransactionTestCase):
    """Channels' own docs recommend TransactionTestCase (not TestCase) for
    consumer tests that touch the DB: the WebsocketCommunicator runs the
    consumer in a separate async context, and TestCase's wrapping
    transaction isn't visible there.
    """

    def setUp(self):
        self.company = Company.objects.create(name="SOS WS Test Co")
        self.driver = Driver.objects.create(
            company=self.company, full_name="WS SOS Driver", phone_number="+919555000099",
            emergency_contact_name="EC", emergency_contact_phone="+919555000098",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        self.admin = AdminUser.objects.create_user(
            email="wssosadmin@test.invalid", company=self.company, password="pass12345"
        )
        self.token = str(AdminTokenObtainPairSerializer.get_token(self.admin).access_token)

    def test_sos_alert_reaches_connected_admin_client_immediately(self):
        asyncio.run(self._run())

    async def _run(self):
        router = URLRouter(websocket_urlpatterns)
        communicator = WebsocketCommunicator(router, f"/ws/sos/?token={self.token}")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        start = time.monotonic()

        @database_sync_to_async
        def trigger():
            return services.trigger_sos(driver=self.driver, latitude=Decimal("12.9"), longitude=Decimal("77.6"))

        alert = await trigger()

        received = await asyncio.wait_for(communicator.receive_json_from(), timeout=2)
        elapsed = time.monotonic() - start

        self.assertEqual(received["id"], str(alert.id))
        self.assertEqual(received["driver_id"], str(self.driver.id))
        # "near-instant" per the module spec — generous ceiling for CI jitter,
        # but this should in practice land in single-digit milliseconds.
        self.assertLess(elapsed, 1.0, f"SOS broadcast took {elapsed:.3f}s to reach the client")

        await communicator.disconnect()

    def test_wrong_token_is_rejected(self):
        asyncio.run(self._run_rejected())

    async def _run_rejected(self):
        router = URLRouter(websocket_urlpatterns)
        communicator = WebsocketCommunicator(router, "/ws/sos/?token=garbage")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
