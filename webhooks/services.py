from django.utils import timezone

from core.exceptions import DomainError

from .models import WebhookEvent, WebhookEventStatus


def publish_webhook_event(company_id, order_ref, event_type, data):
    """Creates a WebhookEvent row. Call this INSIDE the same transaction as
    the state change that triggered it — don't call save() separately from
    the triggering operation's own transaction, or a crash between the two
    could lose the event entirely (defeating the point of the outbox).
    """
    return WebhookEvent.objects.create(
        company_id=company_id, order_ref=order_ref, event_type=event_type, payload=data,
        status=WebhookEventStatus.PENDING,
    )


def retry_webhook_event(event_id, actor):
    try:
        event = WebhookEvent.objects.get(pk=event_id, company_id=actor.company_id)
    except WebhookEvent.DoesNotExist:
        raise DomainError("WEBHOOK_EVENT_NOT_FOUND", "Webhook event not found.", status_code=404)

    if event.status != WebhookEventStatus.FAILED:
        raise DomainError(
            "WEBHOOK_EVENT_NOT_FAILED", "Only a failed webhook event can be retried.", status_code=409
        )

    event.status = WebhookEventStatus.PENDING
    event.attempt_count = 0
    event.next_attempt_at = timezone.now()
    event.save(update_fields=["status", "attempt_count", "next_attempt_at"])

    return event
