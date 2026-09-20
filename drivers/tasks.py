import logging

from celery import shared_task

from .services import DriverService

logger = logging.getLogger(__name__)


@shared_task
def lock_expired_driver_licenses():
    """Scheduled daily (see CELERY_BEAT_SCHEDULE) — locks any active driver
    whose DL has expired. See DriverService.lock_expired_licenses.
    """
    locked = DriverService.lock_expired_licenses()
    logger.info("lock_expired_driver_licenses: locked %d driver(s) with an expired DL", locked)
    return locked
