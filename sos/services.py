from django.utils import timezone

from core.exceptions import DomainError
from notifications.tasks import send_push_notification
from trips.models import Trip

from . import realtime
from .models import SosAlert, SosStatus


def trigger_sos(driver, latitude, longitude, trip_id=None):
    """Safety feature — deliberately carries NO business-rule validation
    beyond what the serializer already checked (lat/lng range). Do not add
    driver-eligibility/account-status/trip-state checks here: a locked or
    disabled driver must still be able to trigger this, unconditionally.
    """
    trip = None
    if trip_id:
        # Existence-only lookup, not a business-rule check — a stale or
        # wrong trip_id must never cause this write to fail (the FK would
        # otherwise reject an invalid id outright). No status/ownership
        # check here, deliberately.
        trip = Trip.objects.filter(pk=trip_id, company_id=driver.company_id).first()

    alert = SosAlert.objects.create(
        company_id=driver.company_id,
        driver=driver,
        trip=trip,
        latitude=latitude,
        longitude=longitude,
        triggered_at=timezone.now(),
    )

    realtime.broadcast_sos_alert(
        driver.company_id,
        {
            "id": str(alert.id),
            "driver_id": str(driver.id),
            "driver_name": driver.full_name,
            "trip_id": str(trip.id) if trip else None,
            "latitude": str(latitude),
            "longitude": str(longitude),
            "triggered_at": alert.triggered_at.isoformat(),
        },
    )

    return alert


def _get_alert(alert_id, company_id):
    try:
        return SosAlert.objects.get(pk=alert_id, company_id=company_id)
    except SosAlert.DoesNotExist:
        raise DomainError("SOS_ALERT_NOT_FOUND", "SOS alert not found.", status_code=404)


def acknowledge_sos(alert_id, actor):
    alert = _get_alert(alert_id, actor.company_id)
    alert.status = SosStatus.ACKNOWLEDGED
    alert.acknowledged_by = actor.id
    alert.acknowledged_at = timezone.now()
    alert.save(update_fields=["status", "acknowledged_by", "acknowledged_at"])
    send_push_notification.delay(
        driver_id=alert.driver_id,
        title="SOS Acknowledged",
        body="Your SOS alert has been acknowledged — help is on the way",
        data={"type": "sos_acknowledged", "sos_alert_id": str(alert.id)},
    )
    return alert


def resolve_sos(alert_id, resolution_note, actor):
    alert = _get_alert(alert_id, actor.company_id)
    alert.status = SosStatus.RESOLVED
    alert.resolved_by = actor.id
    alert.resolved_at = timezone.now()
    alert.resolution_note = resolution_note
    alert.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_note"])
    send_push_notification.delay(
        driver_id=alert.driver_id,
        title="SOS Resolved",
        body="Your SOS alert has been marked resolved",
        data={"type": "sos_resolved", "sos_alert_id": str(alert.id)},
    )
    return alert
