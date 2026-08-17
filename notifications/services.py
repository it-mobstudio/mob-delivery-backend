import logging

from firebase_admin import messaging

from .models import DriverDevice

logger = logging.getLogger(__name__)


def register_device(driver, fcm_token, platform):
    """Upsert: an existing (driver, fcm_token) pair just gets its
    last_active_at bumped (via auto_now); a new pair creates a row. Called
    on every login and FCM token refresh, not just at install time.
    """
    device, _created = DriverDevice.objects.update_or_create(
        driver=driver,
        fcm_token=fcm_token,
        defaults={"company_id": driver.company_id, "platform": platform},
    )
    return device


def deregister_device(driver, fcm_token):
    DriverDevice.objects.filter(driver=driver, fcm_token=fcm_token).delete()


def send_push_to_driver(driver_id, title, body, data=None):
    """Fire-and-forget. Sends to every registered device for this driver.
    Prunes tokens FCM reports as invalid/unregistered. Never raises — a
    notification failure must not break the calling operation.
    """
    string_data = {str(k): str(v) for k, v in (data or {}).items()}

    for device in DriverDevice.objects.filter(driver_id=driver_id):
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=string_data,
            token=device.fcm_token,
        )
        try:
            messaging.send(message)
        except messaging.UnregisteredError:
            device.delete()  # prune dead token
        except Exception as exc:
            logger.warning("Push notification failed for driver %s: %s", driver_id, exc)
