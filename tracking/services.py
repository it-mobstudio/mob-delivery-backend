from django.core.cache import cache
from django.utils import timezone

from core.exceptions import DomainError
from drivers.models import Driver
from trips import services as trip_services
from trips.models import ACTIVE_TRIP_STATUSES, Trip, TripStatus
from vehicles.models import Vehicle

from . import realtime
from .models import DriverShift, ShiftStatus, TripAnomalyAlert, TripLocationPing, TripPause, VehicleStartPoint

LOCATION_CACHE_KEY = "vehicle_location:{vehicle_id}"


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def get_trip(trip_id, company_id):
    try:
        return Trip.objects.select_related("vehicle", "driver").get(pk=trip_id, company_id=company_id)
    except Trip.DoesNotExist:
        raise DomainError("TRIP_NOT_FOUND", "Trip not found.", status_code=404)


def _get_driver(driver_id, company_id):
    try:
        return Driver.objects.get(pk=driver_id, company_id=company_id)
    except Driver.DoesNotExist:
        raise DomainError("DRIVER_NOT_FOUND", "Driver not found.", status_code=404)


def _get_vehicle(vehicle_id, company_id):
    try:
        return Vehicle.objects.get(pk=vehicle_id, company_id=company_id)
    except Vehicle.DoesNotExist:
        raise DomainError("VEHICLE_NOT_FOUND", "Vehicle not found.", status_code=404)


def _get_shift(shift_id, company_id):
    try:
        return DriverShift.objects.select_related("driver", "vehicle").get(pk=shift_id, company_id=company_id)
    except DriverShift.DoesNotExist:
        raise DomainError("SHIFT_NOT_FOUND", "Shift not found.", status_code=404)


# ---------------------------------------------------------------------------
# Location + pause/resume
# ---------------------------------------------------------------------------


def record_location_ping(trip_id, latitude, longitude, speed_kmph, actor):
    trip = get_trip(trip_id, actor.company_id)
    if trip.vehicle_id is None:
        raise DomainError("TRIP_HAS_NO_VEHICLE", "This trip has no vehicle assigned yet.", status_code=409)

    now = timezone.now()

    # The driver app starts pinging once it's actually moving — the first
    # ping on a locked trip is the natural signal that it has gone in
    # transit, since neither this module nor Trips defines any other
    # explicit trigger for that status/started_at transition.
    if trip.status == TripStatus.PICKUPS_LOCKED:
        trip.status = TripStatus.IN_TRANSIT
        trip.started_at = trip.started_at or now
        trip.save(update_fields=["status", "started_at"])

    ping = TripLocationPing.objects.create(
        company_id=trip.company_id,
        trip=trip,
        vehicle_id=trip.vehicle_id,
        latitude=latitude,
        longitude=longitude,
        speed_kmph=speed_kmph,
        recorded_at=now,
    )

    payload = {
        "vehicle_id": str(trip.vehicle_id),
        "trip_id": str(trip.id),
        "lat": str(latitude),
        "lng": str(longitude),
        "speed": str(speed_kmph) if speed_kmph is not None else None,
        "recorded_at": now.isoformat(),
    }
    cache.set(LOCATION_CACHE_KEY.format(vehicle_id=trip.vehicle_id), payload, timeout=None)
    realtime.broadcast_location(trip.id, trip.vehicle_id, payload)

    return ping


def pause_trip(trip_id, reason, actor):
    trip = get_trip(trip_id, actor.company_id)
    if trip.status != TripStatus.IN_TRANSIT:
        raise DomainError("TRIP_NOT_IN_TRANSIT", "Only a trip that is in transit can be paused.", status_code=409)
    if trip.pauses.filter(ended_at__isnull=True).exists():
        raise DomainError("TRIP_ALREADY_PAUSED", "This trip already has an open pause.", status_code=409)
    return TripPause.objects.create(company_id=trip.company_id, trip=trip, reason=reason, started_at=timezone.now())


def resume_trip(trip_id, actor):
    trip = get_trip(trip_id, actor.company_id)
    pause = trip.pauses.filter(ended_at__isnull=True).order_by("-started_at").first()
    if pause is None:
        raise DomainError("NO_OPEN_PAUSE", "This trip has no open pause to resume from.", status_code=409)
    pause.ended_at = timezone.now()
    pause.save(update_fields=["ended_at"])
    return pause


def get_trip_time_summary(trip):
    """Reusable by both the driver app's timer and the admin trip detail
    view — takes a Trip instance directly rather than an id so callers that
    already have one (e.g. a detail serializer) don't pay for a second
    lookup.
    """
    start = trip.started_at or trip.created_at
    end = trip.completed_at or timezone.now()
    total_seconds = max((end - start).total_seconds(), 0)

    paused_seconds = 0
    for pause in trip.pauses.all():
        pause_start = max(pause.started_at, start)
        pause_end = min(pause.ended_at or timezone.now(), end)
        if pause_end > pause_start:
            paused_seconds += (pause_end - pause_start).total_seconds()

    moving_seconds = max(total_seconds - paused_seconds, 0)
    return {
        "moving_minutes": int(moving_seconds // 60),
        "paused_minutes": int(paused_seconds // 60),
    }


# ---------------------------------------------------------------------------
# Driver shifts
# ---------------------------------------------------------------------------


def start_shift(driver_id, vehicle_id, start_odometer, actor, start_point_id=None):
    driver = _get_driver(driver_id, actor.company_id)
    vehicle = _get_vehicle(vehicle_id, actor.company_id)

    if DriverShift.objects.filter(driver=driver, status=ShiftStatus.ACTIVE).exists():
        raise DomainError("SHIFT_ALREADY_ACTIVE", "This driver already has an active shift.", status_code=409)

    # Point 6 — entirely optional. Omitted -> all three fixed_start_* fields
    # stay null and the shift works exactly as it did before this fix.
    fixed_start_fields = {}
    if start_point_id:
        try:
            start_point = VehicleStartPoint.objects.get(pk=start_point_id, company_id=actor.company_id)
        except VehicleStartPoint.DoesNotExist:
            raise DomainError("START_POINT_NOT_FOUND", "Start point not found.", status_code=404)
        fixed_start_fields = {
            "fixed_start_latitude": start_point.latitude,
            "fixed_start_longitude": start_point.longitude,
            "fixed_start_label": start_point.label,
        }

    return DriverShift.objects.create(
        company_id=actor.company_id,
        driver=driver,
        vehicle=vehicle,
        shift_date=timezone.localdate(),
        start_odometer=start_odometer,
        started_at=timezone.now(),
        **fixed_start_fields,
    )


def get_active_shift(driver_id, actor):
    return DriverShift.objects.filter(
        driver_id=driver_id, company_id=actor.company_id, status=ShiftStatus.ACTIVE
    ).first()


def end_shift(shift_id, end_odometer, actor, cleanliness_photo_url=None, charging_plugged_photo_url=None):
    shift = _get_shift(shift_id, actor.company_id)

    if shift.status != ShiftStatus.ACTIVE:
        raise DomainError("SHIFT_NOT_ACTIVE", "This shift is not active.", status_code=409)

    if not cleanliness_photo_url:
        raise DomainError(
            "CLEANLINESS_PHOTO_REQUIRED", "A cleanliness photo is required to end the shift.", status_code=422
        )
    if not charging_plugged_photo_url:
        raise DomainError(
            "CHARGING_PHOTO_REQUIRED", "A charging-plugged photo is required to end the shift.", status_code=422
        )

    in_progress_trip = Trip.objects.filter(
        company_id=actor.company_id,
        driver=shift.driver,
        vehicle=shift.vehicle,
        status__in=ACTIVE_TRIP_STATUSES,
    ).first()
    if in_progress_trip is not None:
        raise DomainError(
            "TRIP_IN_PROGRESS",
            f"Trip {in_progress_trip.id} is still in progress; end it before closing the shift.",
            status_code=409,
        )

    if end_odometer < shift.start_odometer:
        raise DomainError("INVALID_END_ODOMETER", "end_odometer must be >= start_odometer.", status_code=400)

    shift.end_odometer = end_odometer
    shift.cleanliness_photo_url = cleanliness_photo_url
    shift.charging_plugged_photo_url = charging_plugged_photo_url
    shift.ended_at = timezone.now()
    shift.status = ShiftStatus.ENDED
    shift.total_km = end_odometer - shift.start_odometer
    shift.total_working_minutes = int((shift.ended_at - shift.started_at).total_seconds() // 60)
    shift.save(
        update_fields=[
            "end_odometer",
            "cleanliness_photo_url",
            "charging_plugged_photo_url",
            "ended_at",
            "status",
            "total_km",
            "total_working_minutes",
        ]
    )

    trip_services.release_assignment(shift.vehicle, shift.driver)

    trips_completed_today = Trip.objects.filter(
        company_id=actor.company_id,
        driver=shift.driver,
        status=TripStatus.DELIVERED,
        completed_at__date=shift.shift_date,
    ).count()

    return {
        "total_km": shift.total_km,
        "total_working_minutes": shift.total_working_minutes,
        "trips_completed_today": trips_completed_today,
    }


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def acknowledge_alert(alert_id, actor):
    try:
        alert = TripAnomalyAlert.objects.get(pk=alert_id, company_id=actor.company_id)
    except TripAnomalyAlert.DoesNotExist:
        raise DomainError("ALERT_NOT_FOUND", "Alert not found.", status_code=404)
    alert.acknowledged = True
    alert.save(update_fields=["acknowledged"])
    return alert
