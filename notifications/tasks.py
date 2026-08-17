from celery import shared_task

from .services import send_push_to_driver


@shared_task
def send_push_notification(driver_id, title, body, data=None):
    """Fire-and-forget wrapper so callers never block on FCM. Deliberately
    NOT a persisted outbox with retry/backoff like the Webhook module — FCM
    already retries delivery internally, and a missed push just means the
    driver sees the up-to-date state next time they open the app. Losing
    one silently is an acceptable failure mode here, unlike a webhook.
    """
    send_push_to_driver(driver_id, title, body, data)
