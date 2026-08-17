import logging

from celery import shared_task
from django.utils import timezone

from notifications.tasks import send_push_notification

from .models import Driver, DriverAccountStatus

logger = logging.getLogger(__name__)


@shared_task
def lock_expired_driver_licenses():
    """Scheduled daily (see CELERY_BEAT_SCHEDULE) — locks any active driver
    whose DL has expired. The unlock half lives in drivers.services.verify_dl,
    triggered by successful DL re-verification.
    """
    driver_ids = list(
        Driver.objects.filter(
            dl_expiry_date__lt=timezone.localdate(), account_status=DriverAccountStatus.ACTIVE
        ).values_list("id", flat=True)
    )

    locked = Driver.objects.filter(id__in=driver_ids).update(account_status=DriverAccountStatus.LOCKED_DL_EXPIRED)

    for driver_id in driver_ids:
        send_push_notification.delay(
            driver_id=driver_id,
            title="Account Locked",
            body="Your account has been locked — DL expired. Contact your admin.",
            data={"type": "account_locked"},
        )

    logger.info("lock_expired_driver_licenses: locked %d driver(s) with an expired DL", locked)
    return locked
