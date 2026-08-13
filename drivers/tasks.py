import logging

from celery import shared_task
from django.utils import timezone

from .models import Driver, DriverAccountStatus

logger = logging.getLogger(__name__)


@shared_task
def lock_expired_driver_licenses():
    """Scheduled daily (see CELERY_BEAT_SCHEDULE) — locks any active driver
    whose DL has expired. The unlock half lives in drivers.services.verify_dl,
    triggered by successful DL re-verification.
    """
    locked = Driver.objects.filter(
        dl_expiry_date__lt=timezone.localdate(), account_status=DriverAccountStatus.ACTIVE
    ).update(account_status=DriverAccountStatus.LOCKED_DL_EXPIRED)

    logger.info("lock_expired_driver_licenses: locked %d driver(s) with an expired DL", locked)
    return locked
