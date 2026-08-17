from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AdminUser, ApiClient, Company
from core.exceptions import DomainError
from drivers.models import Driver, VerificationStatus
from trips import services as trip_services
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import services
from .models import WebhookDeliveryLog, WebhookEvent, WebhookEventStatus
from .tasks import WEBHOOK_RETRY_INTERVALS_MINUTES, _sign, dispatch_pending_webhooks


def _addr(address="Address", lat="12.900000", lng="77.600000"):
    return {"address": address, "latitude": Decimal(lat), "longitude": Decimal(lng)}


class WebhookTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Webhook Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)
        self.api_client_principal = ApiClient(
            company=self.company, name="Partner Integration",
            webhook_url="https://partner.example.test/hooks", webhook_signing_secret="topsecret",
        )
        self.api_client_principal.set_secret("dummy-secret")
        self.api_client_principal.save()


class PublishWebhookEventTests(WebhookTestBase):
    def test_publish_creates_pending_event(self):
        event = services.publish_webhook_event(
            company_id=self.company.id, order_ref="ORD-1", event_type="order.cancelled", data={"reason": "n/a"}
        )
        self.assertEqual(event.status, WebhookEventStatus.PENDING)
        self.assertEqual(event.attempt_count, 0)
        self.assertEqual(event.payload, {"reason": "n/a"})


class DispatchSuccessTests(WebhookTestBase):
    def setUp(self):
        super().setUp()
        self.event = services.publish_webhook_event(
            company_id=self.company.id, order_ref="ORD-2", event_type="order.status_changed",
            data={"status": "picked_up"},
        )

    @patch("webhooks.tasks.requests.post")
    def test_successful_delivery_marks_event_delivered_and_signs_correctly(self, mock_post):
        mock_post.return_value = Mock(status_code=200, text="ok")

        result = dispatch_pending_webhooks()

        self.assertEqual(result["delivered"], 1)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, WebhookEventStatus.DELIVERED)
        self.assertEqual(self.event.attempt_count, 1)
        self.assertIsNotNone(self.event.delivered_at)

        log = WebhookDeliveryLog.objects.get(webhook_event=self.event)
        self.assertEqual(log.attempt_number, 1)
        self.assertEqual(log.response_status_code, 200)

        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(mock_post.call_args.args[0], "https://partner.example.test/hooks")
        sent_body = call_kwargs["data"]
        expected_signature = _sign("topsecret", sent_body)
        self.assertEqual(call_kwargs["headers"]["X-Webhook-Signature"], expected_signature)
        self.assertEqual(call_kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(call_kwargs["timeout"], 10)

    @patch("webhooks.tasks.requests.post")
    def test_no_webhook_url_configured_fails_immediately_without_retry(self, mock_post):
        self.api_client_principal.webhook_url = None
        self.api_client_principal.save(update_fields=["webhook_url"])

        dispatch_pending_webhooks()

        mock_post.assert_not_called()
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, WebhookEventStatus.FAILED)
        log = WebhookDeliveryLog.objects.get(webhook_event=self.event)
        self.assertIn("No webhook URL configured", log.error_message)


class DispatchRetryBackoffTests(WebhookTestBase):
    def setUp(self):
        super().setUp()
        self.event = services.publish_webhook_event(
            company_id=self.company.id, order_ref="ORD-3", event_type="order.cancelled", data={"reason": "n/a"}
        )

    @patch("webhooks.tasks.requests.post")
    def test_backoff_advances_through_all_intervals_then_fails(self, mock_post):
        mock_post.return_value = Mock(status_code=500, text="server error")

        for attempt_number in range(1, 6):
            before = timezone.now()
            dispatch_pending_webhooks()
            self.event.refresh_from_db()
            self.assertEqual(self.event.attempt_count, attempt_number)

            if attempt_number < 5:
                self.assertEqual(self.event.status, WebhookEventStatus.PENDING)
                expected_minutes = WEBHOOK_RETRY_INTERVALS_MINUTES[attempt_number - 1]
                expected_next = before + timedelta(minutes=expected_minutes)
                self.assertAlmostEqual(
                    self.event.next_attempt_at.timestamp(), expected_next.timestamp(), delta=5
                )
                # Force the next dispatch run to pick it up immediately
                # rather than waiting out the real backoff window.
                self.event.next_attempt_at = timezone.now()
                self.event.save(update_fields=["next_attempt_at"])
            else:
                self.assertEqual(self.event.status, WebhookEventStatus.FAILED)
                self.assertIsNone(self.event.next_attempt_at)

        self.assertEqual(mock_post.call_count, 5)
        self.assertEqual(WebhookDeliveryLog.objects.filter(webhook_event=self.event).count(), 5)

    @patch("webhooks.tasks.requests.post")
    def test_future_next_attempt_at_is_not_picked_up_early(self, mock_post):
        self.event.next_attempt_at = timezone.now() + timedelta(hours=1)
        self.event.save(update_fields=["next_attempt_at"])

        dispatch_pending_webhooks()

        mock_post.assert_not_called()
        self.event.refresh_from_db()
        self.assertEqual(self.event.attempt_count, 0)


class RetryWebhookEventTests(WebhookTestBase):
    def test_retry_resets_failed_event(self):
        event = services.publish_webhook_event(
            company_id=self.company.id, order_ref="ORD-4", event_type="order.cancelled", data={}
        )
        event.status = WebhookEventStatus.FAILED
        event.attempt_count = 5
        event.save(update_fields=["status", "attempt_count"])

        retried = services.retry_webhook_event(event_id=event.id, actor=self.actor)
        self.assertEqual(retried.status, WebhookEventStatus.PENDING)
        self.assertEqual(retried.attempt_count, 0)
        self.assertIsNotNone(retried.next_attempt_at)

    def test_retry_rejects_non_failed_event(self):
        event = services.publish_webhook_event(
            company_id=self.company.id, order_ref="ORD-5", event_type="order.cancelled", data={}
        )
        with self.assertRaises(DomainError) as ctx:
            services.retry_webhook_event(event_id=event.id, actor=self.actor)
        self.assertEqual(ctx.exception.code, "WEBHOOK_EVENT_NOT_FAILED")


class FullLoopIntegrationTests(WebhookTestBase):
    """Spec's own 'after building' check: complete a stop -> WebhookEvent
    row created -> Celery task delivers it."""

    def test_completing_a_stop_publishes_and_delivers_a_webhook_event(self):
        vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=vehicle_type, registration_number="KA13HH0001",
            capacity_kg=Decimal("500"),
        )
        driver = Driver.objects.create(
            company=self.company, full_name="Webhook Driver", phone_number="+919990000099",
            emergency_contact_name="EC", emergency_contact_phone="+919990000098",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        result = trip_services.intake_order(
            company_id=self.company.id, order_ref="ORD-LOOP-1", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("4.0"), actor=self.actor,
        )
        trip_services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=vehicle.id, driver_id=driver.id, actor=self.actor
        )

        trip_services.complete_stop(
            stop_id=result["pickup_stop_id"], proof_photo_url="https://x.test/p.jpg", actor=self.actor
        )

        event = WebhookEvent.objects.get(order_ref="ORD-LOOP-1", event_type="order.status_changed")
        self.assertEqual(event.status, WebhookEventStatus.PENDING)
        self.assertEqual(event.payload["status"], "picked_up")

        with patch("webhooks.tasks.requests.post") as mock_post:
            mock_post.return_value = Mock(status_code=200, text="ok")
            dispatch_pending_webhooks()

        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEventStatus.DELIVERED)


class WebhookHttpTests(WebhookTestBase):
    def setUp(self):
        super().setUp()
        self.admin = AdminUser.objects.create_user(
            email="webhookadmin@test.invalid", company=self.company, password="pass12345"
        )
        self.event = services.publish_webhook_event(
            company_id=self.company.id, order_ref="ORD-HTTP-1", event_type="order.cancelled", data={"reason": "n/a"}
        )

    def test_list_filters_by_order_ref(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.get("/api/v1/webhooks/deliveries?order_ref=ORD-HTTP-1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)

    def test_detail_includes_delivery_logs(self):
        WebhookDeliveryLog.objects.create(
            webhook_event=self.event, attempt_number=1, response_status_code=500, error_message=None
        )
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.get(f"/api/v1/webhooks/deliveries/{self.event.id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["delivery_logs"]), 1)

    def test_retry_endpoint_requires_failed_status(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.post(f"/api/v1/webhooks/retry/{self.event.id}")
        self.assertEqual(r.status_code, 409)

    def test_retry_endpoint_succeeds_for_failed_event(self):
        self.event.status = WebhookEventStatus.FAILED
        self.event.save(update_fields=["status"])
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.post(f"/api/v1/webhooks/retry/{self.event.id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "pending")
