import logging

from celery import shared_task
from django.utils import timezone

from .models import IdempotencyRecord
from .services import IDEMPOTENCY_WINDOW

logger = logging.getLogger(__name__)


@shared_task
def cleanup_expired_idempotency_records():
    """Scheduled hourly (see CELERY_BEAT_SCHEDULE) — deletes replay records
    past the same 24h window used to decide whether to replay them, so
    nothing lingers once it can no longer be matched anyway.
    """
    cutoff = timezone.now() - IDEMPOTENCY_WINDOW
    deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
    logger.info("cleanup_expired_idempotency_records: deleted %d record(s)", deleted)
    return deleted
