from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Company
from drivers.models import Driver, VerificationStatus

from . import services
from .models import IdempotencyRecord
from .tasks import cleanup_expired_idempotency_records


class IdempotencyTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Idempotency Test Co")
        self.driver = Driver.objects.create(
            company=self.company, full_name="Idempotent Driver", phone_number="+919333000001",
            emergency_contact_name="EC", emergency_contact_phone="+919333000002",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
        )


def _fake_request(user, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return SimpleNamespace(user=user, headers=headers)


class WithIdempotencyServiceTests(IdempotencyTestBase):
    def test_first_call_invokes_handler_and_records(self):
        handler = Mock(return_value=(200, {"ok": True}))
        request = _fake_request(self.driver, "key-1")

        status_code, body = services.with_idempotency(request, handler)

        self.assertEqual((status_code, body), (200, {"ok": True}))
        handler.assert_called_once()
        self.assertTrue(IdempotencyRecord.objects.filter(driver_id=self.driver.id, key="key-1").exists())

    def test_second_call_with_same_key_replays_without_reinvoking_handler(self):
        first_handler = Mock(return_value=(200, {"attempt": 1}))
        request = _fake_request(self.driver, "key-2")
        services.with_idempotency(request, first_handler)

        second_handler = Mock(return_value=(200, {"attempt": 2}))
        status_code, body = services.with_idempotency(request, second_handler)

        second_handler.assert_not_called()
        self.assertEqual(body, {"attempt": 1}, "must replay the ORIGINAL response, not reprocess")

    def test_different_key_reprocesses(self):
        request1 = _fake_request(self.driver, "key-a")
        services.with_idempotency(request1, Mock(return_value=(200, {"attempt": 1})))

        request2 = _fake_request(self.driver, "key-b")
        handler2 = Mock(return_value=(200, {"attempt": 2}))
        services.with_idempotency(request2, handler2)

        handler2.assert_called_once()

    def test_no_key_never_caches(self):
        request = _fake_request(self.driver, key=None)
        handler = Mock(return_value=(200, {"x": 1}))

        services.with_idempotency(request, handler)
        services.with_idempotency(request, handler)

        self.assertEqual(handler.call_count, 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)

    def test_non_driver_actor_never_caches(self):
        admin_like = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", company_id=self.company.id)
        request = _fake_request(admin_like, "key-admin")
        handler = Mock(return_value=(200, {"x": 1}))

        services.with_idempotency(request, handler)
        services.with_idempotency(request, handler)

        self.assertEqual(handler.call_count, 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)

    def test_expired_record_is_not_replayed(self):
        request = _fake_request(self.driver, "key-old")
        services.with_idempotency(request, Mock(return_value=(200, {"attempt": 1})))

        record = IdempotencyRecord.objects.get(driver_id=self.driver.id, key="key-old")
        record.created_at = timezone.now() - timedelta(hours=25)
        record.save(update_fields=["created_at"])

        handler2 = Mock(return_value=(200, {"attempt": 2}))
        services.with_idempotency(request, handler2)
        handler2.assert_called_once()


class CleanupTaskTests(IdempotencyTestBase):
    def test_cleanup_deletes_only_expired_records(self):
        fresh = IdempotencyRecord.objects.create(
            key="fresh", driver_id=self.driver.id, response_body={}, status_code=200
        )
        stale = IdempotencyRecord.objects.create(
            key="stale", driver_id=self.driver.id, response_body={}, status_code=200
        )
        IdempotencyRecord.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(hours=25))

        deleted = cleanup_expired_idempotency_records()

        self.assertEqual(deleted, 1)
        self.assertTrue(IdempotencyRecord.objects.filter(pk=fresh.pk).exists())
        self.assertFalse(IdempotencyRecord.objects.filter(pk=stale.pk).exists())


class SosIdempotencyHttpTests(IdempotencyTestBase):
    """End-to-end proof on a real endpoint: retried request with the same
    Idempotency-Key must not double-process."""

    def test_retried_sos_trigger_creates_only_one_alert(self):
        from sos.models import SosAlert

        client = APIClient()
        client.force_authenticate(user=self.driver)
        headers = {"HTTP_IDEMPOTENCY_KEY": "retry-key-1"}

        r1 = client.post("/api/v1/sos", {"latitude": "12.9", "longitude": "77.6"}, format="json", **headers)
        r2 = client.post("/api/v1/sos", {"latitude": "12.9", "longitude": "77.6"}, format="json", **headers)

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(SosAlert.objects.filter(driver=self.driver).count(), 1)
        self.assertEqual(r1.data["id"], r2.data["id"])

    def test_different_key_creates_a_second_alert(self):
        from sos.models import SosAlert

        client = APIClient()
        client.force_authenticate(user=self.driver)

        client.post(
            "/api/v1/sos", {"latitude": "12.9", "longitude": "77.6"}, format="json",
            HTTP_IDEMPOTENCY_KEY="key-x",
        )
        client.post(
            "/api/v1/sos", {"latitude": "12.9", "longitude": "77.6"}, format="json",
            HTTP_IDEMPOTENCY_KEY="key-y",
        )

        self.assertEqual(SosAlert.objects.filter(driver=self.driver).count(), 2)
