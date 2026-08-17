"""Webhook delivery dispatch.

Signature scheme (for the receiving client team to verify):
  1. The delivered request body is the exact JSON bytes of the envelope
     below, UTF-8 encoded — no re-serialization on your end needed for
     verification, just hash the raw bytes you received.
  2. `X-Webhook-Signature` header value is `sha256=<hex>`, where <hex> is
     HMAC-SHA256(key=webhook_signing_secret, message=<raw request body bytes>)
     rendered as a lowercase hex digest.
  3. Compare using a constant-time comparison (e.g. Python's
     hmac.compare_digest) against your own computed signature — do not use
     `==` on the hex strings.

Envelope shape:
    {
      "event_id": "uuid", "event_type": "order.status_changed",
      "order_ref": "suborder-A", "company_id": "uuid",
      "timestamp": "2026-08-06T10:15:00Z", "data": {...}
    }
"""

import hashlib
import hmac
import json
import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from accounts.models import ApiClient

from .models import WebhookDeliveryLog, WebhookEvent, WebhookEventStatus

logger = logging.getLogger(__name__)

# One retry delay per attempt number (1-indexed) — attempt 1 failing schedules
# a retry after WEBHOOK_RETRY_INTERVALS_MINUTES[0], etc. The 5th entry exists
# to document the spec's stated attempt-5 backoff, but is never actually
# consulted: reaching attempt_count == 5 always terminates as `failed` rather
# than scheduling a 6th try (see _reschedule_or_fail).
WEBHOOK_RETRY_INTERVALS_MINUTES = [1, 5, 30, 120, 360]

WEBHOOK_DISPATCH_BATCH_SIZE = 50
WEBHOOK_REQUEST_TIMEOUT_SECONDS = 10
WEBHOOK_RESPONSE_BODY_TRUNCATE_CHARS = 2000


def _truncate(text):
    if text is None:
        return None
    return text[:WEBHOOK_RESPONSE_BODY_TRUNCATE_CHARS]


def _sign(secret, body_bytes):
    return "sha256=" + hmac.new((secret or "").encode(), body_bytes, hashlib.sha256).hexdigest()


def _build_envelope(event):
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "order_ref": event.order_ref,
        "company_id": str(event.company_id),
        "timestamp": event.created_at.isoformat(),
        "data": event.payload,
    }


def _reschedule_or_fail(event):
    if event.attempt_count >= len(WEBHOOK_RETRY_INTERVALS_MINUTES):
        event.status = WebhookEventStatus.FAILED
        event.next_attempt_at = None
    else:
        delay_minutes = WEBHOOK_RETRY_INTERVALS_MINUTES[event.attempt_count - 1]
        event.next_attempt_at = timezone.now() + timedelta(minutes=delay_minutes)
    event.save(update_fields=["status", "attempt_count", "next_attempt_at"])


@shared_task
def dispatch_pending_webhooks():
    now = timezone.now()
    events = list(
        WebhookEvent.objects.filter(status=WebhookEventStatus.PENDING)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by("created_at")[:WEBHOOK_DISPATCH_BATCH_SIZE]
    )

    delivered = 0
    outcome_count = 0

    for event in events:
        # `.first()`, not `.get()`: a company could have zero or (in theory)
        # more than one ApiClient. The first configured one is used — matches
        # "every client gets all event types at one fixed webhook_url for now."
        client = ApiClient.objects.filter(company_id=event.company_id).first()

        if client is None or not client.webhook_url:
            event.status = WebhookEventStatus.FAILED
            event.save(update_fields=["status"])
            WebhookDeliveryLog.objects.create(
                webhook_event=event,
                attempt_number=event.attempt_count + 1,
                error_message="No webhook URL configured for this company's API client.",
            )
            outcome_count += 1
            continue

        envelope = _build_envelope(event)
        body_bytes = json.dumps(envelope, default=str).encode("utf-8")
        attempt_number = event.attempt_count + 1

        try:
            response = requests.post(
                client.webhook_url,
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": _sign(client.webhook_signing_secret, body_bytes),
                },
                timeout=WEBHOOK_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            WebhookDeliveryLog.objects.create(
                webhook_event=event, attempt_number=attempt_number, error_message=str(exc)
            )
            event.attempt_count = attempt_number
            _reschedule_or_fail(event)
            outcome_count += 1
            continue

        WebhookDeliveryLog.objects.create(
            webhook_event=event,
            attempt_number=attempt_number,
            response_status_code=response.status_code,
            response_body=_truncate(response.text),
        )

        if 200 <= response.status_code < 300:
            event.status = WebhookEventStatus.DELIVERED
            event.attempt_count = attempt_number
            event.delivered_at = timezone.now()
            event.save(update_fields=["status", "attempt_count", "delivered_at"])
            delivered += 1
        else:
            event.attempt_count = attempt_number
            _reschedule_or_fail(event)
            outcome_count += 1

    logger.info("dispatch_pending_webhooks: delivered=%d other=%d", delivered, outcome_count)
    return {"delivered": delivered, "failed_or_retried": outcome_count}
