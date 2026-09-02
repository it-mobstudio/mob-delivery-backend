from collections import defaultdict
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db.models import Avg, DurationField, ExpressionWrapper, F, Sum
from django.utils import timezone

from core.exceptions import DomainError
from damage_reports.models import DamageReportStatus, VehicleDamageReport
from drivers.models import Driver, DriverAccountStatus
from issues.models import IssueStatus, IssueType, TripIssue
from tracking.models import AnomalyType, DriverShift, ShiftStatus, TripAnomalyAlert, TripLocationPing, TripPause
from tracking.services import LOCATION_CACHE_KEY
from trips.models import ACTIVE_TRIP_STATUSES, Trip, TripStatus
from vehicles.models import Vehicle, VehicleDocumentExpiryAlert, VehicleStatus

# Safety score penalties — see compute_safety_score.
SAFETY_SCORE_ALERT_PENALTY = 3
SAFETY_SCORE_UNRESOLVED_ISSUE_PENALTY = 5
SAFETY_SCORE_TRAFFIC_PENALTY_PENALTY = 10

# A vehicle counts as "moving" only if its last location ping is within this
# window — separate from (and tighter than) the Tracking module's own
# stationary-alert window, which looks for sustained lack of movement over a
# much longer horizon.
RECENT_PING_WINDOW = timedelta(minutes=2)


def _parse_recorded_at(value):
    # Written by tracking.services.record_location_ping as
    # timezone.now().isoformat() — always aware, always parseable directly.
    return datetime.fromisoformat(value)


def get_fleet_status(company_id):
    now = timezone.now()
    today = timezone.localdate()

    vehicles = list(
        Vehicle.objects.filter(company_id=company_id, status=VehicleStatus.ACTIVE)
        .select_related("vehicle_type")
        .order_by("registration_number")
    )
    if not vehicles:
        return []
    vehicle_ids = [v.id for v in vehicles]

    shifts_by_vehicle = {
        s.vehicle_id: s
        for s in DriverShift.objects.filter(
            company_id=company_id, vehicle_id__in=vehicle_ids, status=ShiftStatus.ACTIVE, shift_date=today
        ).select_related("driver")
    }

    trips_by_vehicle = {
        t.vehicle_id: t
        for t in Trip.objects.filter(
            company_id=company_id, vehicle_id__in=vehicle_ids, status__in=ACTIVE_TRIP_STATUSES
        )
    }
    trip_ids = list(trips_by_vehicle.values())
    trip_ids = [t.id for t in trip_ids]

    open_pause_by_trip = {
        p.trip_id: p for p in TripPause.objects.filter(trip_id__in=trip_ids, ended_at__isnull=True)
    }

    alerted_trip_ids = set(
        TripAnomalyAlert.objects.filter(
            trip_id__in=trip_ids, alert_type=AnomalyType.STATIONARY_TOO_LONG, acknowledged=False
        ).values_list("trip_id", flat=True)
    )

    pauses_by_trip = defaultdict(list)
    for pause in TripPause.objects.filter(trip_id__in=trip_ids).only("trip_id", "started_at", "ended_at"):
        pauses_by_trip[pause.trip_id].append(pause)

    cache_keys = [LOCATION_CACHE_KEY.format(vehicle_id=vid) for vid in vehicle_ids]
    locations_by_key = cache.get_many(cache_keys)

    # GPS-derived running total for today — an in-progress approximation,
    # not the authoritative figure. DriverShift.total_km (odometer-based) is
    # more accurate but only exists once a shift has ended; this fills the
    # gap while a vehicle is still out. Speed-implausible GPS jumps are
    # already zeroed out at write time (see
    # tracking.services.record_location_ping), so they don't inflate this.
    today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    km_by_vehicle = {
        row["vehicle_id"]: row["total"]
        for row in TripLocationPing.objects.filter(
            company_id=company_id, vehicle_id__in=vehicle_ids, recorded_at__gte=today_start
        )
        .values("vehicle_id")
        .annotate(total=Sum("distance_from_previous_km"))
    }

    results = []
    for vehicle in vehicles:
        shift = shifts_by_vehicle.get(vehicle.id)
        trip = trips_by_vehicle.get(vehicle.id)
        driver = shift.driver if shift else None
        location = locations_by_key.get(LOCATION_CACHE_KEY.format(vehicle_id=vehicle.id))

        pause_reason = None
        today_working_minutes = None

        if not shift or not trip:
            status = "offline"
        else:
            open_pause = open_pause_by_trip.get(trip.id)
            if open_pause is not None:
                status = "paused"
                pause_reason = open_pause.reason
            elif location is not None and (now - _parse_recorded_at(location["recorded_at"])) <= RECENT_PING_WINDOW:
                status = "moving"
            else:
                # Either no recent ping, or an unacknowledged stationary
                # alert (or both) — either way it needs attention.
                status = "idle_alert"

        if shift:
            elapsed_seconds = (now - shift.started_at).total_seconds()
            paused_seconds = 0.0
            if trip:
                for pause in pauses_by_trip.get(trip.id, []):
                    pause_end = pause.ended_at or now
                    paused_seconds += (pause_end - pause.started_at).total_seconds()
            today_working_minutes = int(max(elapsed_seconds - paused_seconds, 0) // 60)

        results.append(
            {
                "vehicle_id": vehicle.id,
                "registration_number": vehicle.registration_number,
                "vehicle_type": {
                    "name": vehicle.vehicle_type.name,
                    "category": vehicle.vehicle_type.category,
                    "icon_image_url": vehicle.vehicle_type.icon_image_url,
                },
                "driver_id": driver.id if driver else None,
                "driver_name": driver.full_name if driver else None,
                "status": status,
                "current_trip_id": trip.id if trip else None,
                "current_trip_status": trip.status if trip else None,
                "pause_reason": pause_reason,
                "last_location": (
                    {
                        "lat": float(location["lat"]),
                        "lng": float(location["lng"]),
                        "recorded_at": location["recorded_at"],
                    }
                    if location
                    else None
                ),
                "today_working_minutes": today_working_minutes,
                "today_km": float(km_by_vehicle.get(vehicle.id) or 0),
            }
        )

    return results


def get_kpis(company_id):
    today = timezone.localdate()

    active_vehicles = (
        DriverShift.objects.filter(company_id=company_id, status=ShiftStatus.ACTIVE, shift_date=today)
        .values("vehicle_id")
        .distinct()
        .count()
    )

    on_trip = Trip.objects.filter(company_id=company_id, status=TripStatus.IN_TRANSIT).count()

    idle_alerts = TripAnomalyAlert.objects.filter(company_id=company_id, acknowledged=False).count()

    avg_duration = (
        Trip.objects.filter(
            company_id=company_id,
            status=TripStatus.DELIVERED,
            completed_at__date=today,
            started_at__isnull=False,
        )
        .annotate(duration=ExpressionWrapper(F("completed_at") - F("started_at"), output_field=DurationField()))
        .aggregate(avg=Avg("duration"))["avg"]
    )
    avg_delivery_minutes = int(avg_duration.total_seconds() // 60) if avg_duration is not None else None

    open_damage_reports = VehicleDamageReport.objects.filter(
        company_id=company_id, status=DamageReportStatus.OPEN
    ).count()

    drivers_locked_dl_expired = Driver.objects.filter(
        company_id=company_id, account_status=DriverAccountStatus.LOCKED_DL_EXPIRED
    ).count()

    documents_expiring_soon = VehicleDocumentExpiryAlert.objects.filter(
        company_id=company_id, acknowledged=False
    ).count()

    return {
        "active_vehicles": active_vehicles,
        "on_trip": on_trip,
        "idle_alerts": idle_alerts,
        "avg_delivery_minutes": avg_delivery_minutes,
        "open_damage_reports": open_damage_reports,
        "drivers_locked_dl_expired": drivers_locked_dl_expired,
        "documents_expiring_soon": documents_expiring_soon,
    }


def get_recent_issues(company_id, limit):
    return list(
        TripIssue.objects.filter(company_id=company_id)
        .select_related("trip", "trip__driver")
        .order_by("-created_at")[:limit]
    )


def get_recent_damage_reports(company_id, limit):
    return list(
        VehicleDamageReport.objects.filter(company_id=company_id)
        .select_related("vehicle")
        .order_by("-created_at")[:limit]
    )


def _start_of_current_week():
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    return timezone.make_aware(datetime.combine(monday, datetime.min.time()))


def compute_safety_score(driver_id, company_id, since):
    alerts = TripAnomalyAlert.objects.filter(
        trip__driver_id=driver_id, company_id=company_id, created_at__gte=since
    ).count()
    unresolved_issues = TripIssue.objects.filter(
        trip__driver_id=driver_id, company_id=company_id, created_at__gte=since, status=IssueStatus.OPEN
    ).count()
    traffic_penalties = TripIssue.objects.filter(
        trip__driver_id=driver_id,
        company_id=company_id,
        created_at__gte=since,
        issue_type=IssueType.TRAFFIC_PENALTY,
    ).count()

    score = max(
        100
        - alerts * SAFETY_SCORE_ALERT_PENALTY
        - unresolved_issues * SAFETY_SCORE_UNRESOLVED_ISSUE_PENALTY
        - traffic_penalties * SAFETY_SCORE_TRAFFIC_PENALTY_PENALTY,
        0,
    )
    return {
        "score": score,
        "out_of": 100,
        "factors": {
            "tracking_alerts": alerts,
            "unresolved_issues": unresolved_issues,
            "traffic_penalties": traffic_penalties,
        },
    }


def get_driver_safety_score(driver_id, company_id, since=None):
    if not Driver.objects.filter(pk=driver_id, company_id=company_id).exists():
        raise DomainError("DRIVER_NOT_FOUND", "Driver not found.", status_code=404)
    return compute_safety_score(driver_id, company_id, since or _start_of_current_week())
