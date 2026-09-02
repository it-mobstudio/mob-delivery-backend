import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from tenant_settings.services import get_tenant_setting
from trips.models import Trip, TripStatus

from . import anomaly_detection, realtime
from .models import AnomalyType, DriverShift, ShiftStatus, TripAnomalyAlert, TripLocationPing

logger = logging.getLogger(__name__)

# Fallback defaults if a tenant has no TenantSetting row yet (e.g. a company
# created before Fix 3 shipped) — same values as tenant_settings.services.
# DEFAULT_SETTINGS seeds at bootstrap.
DEFAULT_STATIONARY_RADIUS_METERS = 50
DEFAULT_STATIONARY_DURATION_MINUTES = 10
DEFAULT_WRONG_DIRECTION_DEGREES = 90
DEFAULT_GPS_OFFLINE_MINUTES = 5


def _stationary_settings_for(company_id):
    return {
        "radius_meters": get_tenant_setting(company_id, "stationary_radius_meters", DEFAULT_STATIONARY_RADIUS_METERS),
        "duration_minutes": get_tenant_setting(
            company_id, "stationary_duration_minutes", DEFAULT_STATIONARY_DURATION_MINUTES
        ),
    }


def _has_unacknowledged_alert(trip, alert_type):
    return TripAnomalyAlert.objects.filter(trip=trip, alert_type=alert_type, acknowledged=False).exists()


def _create_alert(trip, alert_type, details):
    alert = TripAnomalyAlert.objects.create(
        company_id=trip.company_id,
        trip=trip,
        vehicle_id=trip.vehicle_id,
        alert_type=alert_type,
        details=details,
        detected_at=timezone.now(),
    )
    realtime.broadcast_anomaly_alert(
        trip.id,
        {
            "id": str(alert.id),
            "trip_id": str(trip.id),
            "alert_type": alert_type,
            "details": details,
            "detected_at": alert.detected_at.isoformat(),
        },
    )
    return alert


@shared_task
def detect_stationary_vehicles():
    now = timezone.now()
    created = 0
    settings_cache = {}

    for trip in Trip.objects.filter(status=TripStatus.IN_TRANSIT, vehicle_id__isnull=False):
        if _has_unacknowledged_alert(trip, AnomalyType.STATIONARY_TOO_LONG):
            continue

        if trip.company_id not in settings_cache:
            settings_cache[trip.company_id] = _stationary_settings_for(trip.company_id)
        cfg = settings_cache[trip.company_id]
        window_start = now - timedelta(minutes=cfg["duration_minutes"])

        pause_covers_window = trip.pauses.filter(started_at__lte=now).filter(
            Q(ended_at__isnull=True) | Q(ended_at__gte=window_start)
        ).exists()
        if pause_covers_window:
            continue

        pings = list(
            TripLocationPing.objects.filter(vehicle_id=trip.vehicle_id, recorded_at__gte=window_start).order_by(
                "recorded_at"
            )
        )
        if not pings:
            continue

        if anomaly_detection.is_stationary(pings, radius_m=cfg["radius_meters"]):
            _create_alert(
                trip,
                AnomalyType.STATIONARY_TOO_LONG,
                f"{len(pings)} ping(s) within {cfg['radius_meters']}m over the last "
                f"{cfg['duration_minutes']} minute(s).",
            )
            created += 1

    logger.info("detect_stationary_vehicles: created %d alert(s)", created)
    return created


@shared_task
def detect_wrong_direction():
    # wrong_direction_min_consecutive_pings stays a global setting — it's
    # not one of the values Fix 3 asked to make tenant-configurable.
    min_consecutive = settings.ANOMALY_DETECTION_SETTINGS["wrong_direction_min_consecutive_pings"]
    created = 0
    degrees_cache = {}

    for trip in Trip.objects.filter(status=TripStatus.IN_TRANSIT, vehicle_id__isnull=False):
        if _has_unacknowledged_alert(trip, AnomalyType.WRONG_DIRECTION):
            continue

        target_stop = anomaly_detection.target_stop_for(trip)
        if target_stop is None:
            continue

        if trip.company_id not in degrees_cache:
            degrees_cache[trip.company_id] = get_tenant_setting(
                trip.company_id, "wrong_direction_degrees", DEFAULT_WRONG_DIRECTION_DEGREES
            )
        threshold_degrees = degrees_cache[trip.company_id]

        lookback = min_consecutive + 1
        pings = list(
            TripLocationPing.objects.filter(vehicle_id=trip.vehicle_id).order_by("-recorded_at")[:lookback]
        )
        pings.reverse()  # oldest -> newest, required by is_wrong_direction

        if anomaly_detection.is_wrong_direction(
            pings,
            target_stop.latitude,
            target_stop.longitude,
            threshold_degrees=threshold_degrees,
            min_consecutive=min_consecutive,
        ):
            _create_alert(
                trip,
                AnomalyType.WRONG_DIRECTION,
                f"Sustained bearing deviation > {threshold_degrees}° from stop {target_stop.id}.",
            )
            created += 1

    logger.info("detect_wrong_direction: created %d alert(s)", created)
    return created


def _offline_minutes_for(company_id):
    return get_tenant_setting(company_id, "gps_offline_minutes", DEFAULT_GPS_OFFLINE_MINUTES)


@shared_task
def detect_offline_vehicles():
    """Distinct from detect_stationary_vehicles — that's pings arriving but
    the vehicle not moving; this is no pings arriving at all (phone died,
    app killed, GPS/permissions disabled). Runs every ~2 minutes (see
    CELERY_BEAT_SCHEDULE). Checks both an in-transit trip AND an active
    shift with no active trip yet, so a driver going offline before their
    first ping of the day is also caught.
    """
    now = timezone.now()
    created = 0
    minutes_cache = {}

    def _offline_minutes(company_id):
        if company_id not in minutes_cache:
            minutes_cache[company_id] = _offline_minutes_for(company_id)
        return minutes_cache[company_id]

    vehicles_covered_by_trip = set()

    for trip in Trip.objects.filter(status=TripStatus.IN_TRANSIT, vehicle_id__isnull=False):
        vehicles_covered_by_trip.add(trip.vehicle_id)

        if _has_unacknowledged_alert(trip, AnomalyType.GPS_OFFLINE):
            continue

        window_start = now - timedelta(minutes=_offline_minutes(trip.company_id))
        has_recent_ping = TripLocationPing.objects.filter(
            vehicle_id=trip.vehicle_id, recorded_at__gte=window_start
        ).exists()
        if not has_recent_ping:
            _create_alert(
                trip,
                AnomalyType.GPS_OFFLINE,
                f"No location pings in the last {_offline_minutes(trip.company_id)} minute(s).",
            )
            created += 1

    # Shift-level — a driver marked active on shift but with no active trip
    # (and so not already covered by the loop above) and no pings at all.
    for shift in DriverShift.objects.filter(status=ShiftStatus.ACTIVE):
        if shift.vehicle_id in vehicles_covered_by_trip:
            continue
        if TripAnomalyAlert.objects.filter(
            shift=shift, alert_type=AnomalyType.GPS_OFFLINE, acknowledged=False
        ).exists():
            continue

        window_start = now - timedelta(minutes=_offline_minutes(shift.company_id))
        has_recent_ping = TripLocationPing.objects.filter(
            vehicle_id=shift.vehicle_id, recorded_at__gte=window_start
        ).exists()
        if not has_recent_ping:
            TripAnomalyAlert.objects.create(
                company_id=shift.company_id,
                shift=shift,
                vehicle_id=shift.vehicle_id,
                alert_type=AnomalyType.GPS_OFFLINE,
                details=(
                    f"Driver on active shift with no location pings in the last "
                    f"{_offline_minutes(shift.company_id)} minute(s)."
                ),
                detected_at=now,
            )
            created += 1

    logger.info("detect_offline_vehicles: created %d alert(s)", created)
    return created


@shared_task
def purge_old_location_pings():
    """Scheduled weekly (see CELERY_BEAT_SCHEDULE) — deletes TripLocationPing
    rows older than the retention window (settings.LOCATION_PING_RETENTION_DAYS,
    default 90). Only the raw ping table; never touches TripAnomalyAlert or
    any other derived/summary data.

    Global setting, not per-tenant — unlike the anomaly-detection thresholds
    above, the spec doesn't ask for this to be tenant-configurable. Read
    from settings at call time (not cached at import time) so tests can
    override it.
    """
    retention_days = settings.LOCATION_PING_RETENTION_DAYS
    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted, _ = TripLocationPing.objects.filter(recorded_at__lt=cutoff).delete()
    logger.info("purge_old_location_pings: deleted %d ping(s) older than %d days", deleted, retention_days)
    return deleted
