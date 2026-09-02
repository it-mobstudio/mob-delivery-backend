from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.exceptions import DomainError
from core.geo import haversine_distance_m
from drivers.models import Driver, DriverAccountStatus, VerificationStatus
from notifications.tasks import send_push_notification
from tenant_settings.services import get_tenant_setting
from vehicles.models import Vehicle, VehicleStatus
from webhooks.services import publish_webhook_event

from .models import (
    ACTIVE_TRIP_STATUSES,
    DELIVERY_GATE_PHOTO_TYPES,
    PICKUP_GATE_PHOTO_TYPES,
    AddressChangeLog,
    StopStatus,
    StopType,
    Trip,
    TripPhoto,
    TripPhotoType,
    TripStatus,
    TripStop,
    TripVehicleHistory,
)

DEFAULT_ASSIGNMENT_WINDOW_MINUTES = 30
DEFAULT_GEOFENCE_METERS = 100
RECENT_PING_WINDOW = timedelta(minutes=2)

# Point 8 — ETA-window vehicle assignment. Deliberately straight-line +
# assumed-speed rather than a routed Maps estimate: this runs inline in a
# list endpoint over every busy vehicle, so a per-vehicle external HTTP call
# isn't viable, and the approximation is transparent/testable without
# mocking a third-party API.
ETA_PING_FRESHNESS = timedelta(minutes=15)
DEFAULT_ASSIGNMENT_ETA_SPEED_KMPH = 25
DEFAULT_ASSIGNMENT_ETA_DWELL_MINUTES = 5


# ---------------------------------------------------------------------------
# Lookups (company-scoped — every mutating service takes an `actor`, the
# authenticated AdminUser/ApiClient/Driver, and scopes lookups to
# `actor.company_id` so each function is safe to call on its own).
# ---------------------------------------------------------------------------


def get_trip(trip_id, company_id):
    try:
        return Trip.objects.select_related("vehicle", "driver").get(pk=trip_id, company_id=company_id)
    except Trip.DoesNotExist:
        raise DomainError("TRIP_NOT_FOUND", "Trip not found.", status_code=404)


def _get_vehicle(vehicle_id, company_id):
    try:
        return Vehicle.objects.select_related("vehicle_type").get(pk=vehicle_id, company_id=company_id)
    except Vehicle.DoesNotExist:
        raise DomainError("VEHICLE_NOT_FOUND", "Vehicle not found.", status_code=404)


def _get_driver(driver_id, company_id):
    try:
        return Driver.objects.get(pk=driver_id, company_id=company_id)
    except Driver.DoesNotExist:
        raise DomainError("DRIVER_NOT_FOUND", "Driver not found.", status_code=404)


def _get_stop(stop_id, company_id):
    try:
        return TripStop.objects.select_related("trip").get(pk=stop_id, company_id=company_id)
    except TripStop.DoesNotExist:
        raise DomainError("TRIP_STOP_NOT_FOUND", "Trip stop not found.", status_code=404)


def _drop_stop_for(trip, parent_order_ref):
    qs = trip.stops.filter(stop_type=StopType.DROP)
    if parent_order_ref:
        qs = qs.filter(parent_order_ref=parent_order_ref)
    return qs.first()


def _next_sequence_no(trip):
    last = trip.stops.order_by("-sequence_no").values_list("sequence_no", flat=True).first()
    return (last or 0) + 1


def _recompute_total_weight(trip):
    total = trip.stops.filter(stop_type=StopType.PICKUP).aggregate(total=Sum("weight_kg"))["total"] or 0
    trip.total_weight_kg = total
    trip.save(update_fields=["total_weight_kg"])


def release_assignment(vehicle, driver):
    """Clears the current-assignment pairing on whichever side(s) are given.

    Shared by complete_stop and reassign_vehicle so the Tracking/End-Day
    module can reuse the same release logic instead of duplicating it.
    """
    if vehicle is not None:
        vehicle.current_driver_id = None
        vehicle.save(update_fields=["current_driver_id"])
    if driver is not None:
        driver.current_vehicle_id = None
        driver.save(update_fields=["current_vehicle_id"])


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


def _find_existing_intake(company_id, order_ref):
    pickup = (
        TripStop.objects.select_related("trip")
        .filter(company_id=company_id, order_ref=order_ref, stop_type=StopType.PICKUP)
        .first()
    )
    if pickup is None:
        return None
    drop = _drop_stop_for(pickup.trip, pickup.parent_order_ref)
    return {
        "trip_id": pickup.trip_id,
        "pickup_stop_id": pickup.id,
        "drop_stop_id": drop.id if drop else None,
    }


def _resolve_stop_coordinates(location_data):
    """Pincode-to-coordinate fallback — if lat/lng are missing but a pincode
    is present, resolve via Google's Geocoding API rather than rejecting the
    order outright. Only raises once that also fails.
    """
    latitude, longitude = location_data.get("latitude"), location_data.get("longitude")
    if latitude is not None and longitude is not None:
        return latitude, longitude

    # Local import: maps has no dependency on trips, but keeping the import
    # here mirrors how every other cross-app pull-in in this file is scoped
    # to where it's actually used.
    from maps.services import resolve_pincode_coordinates

    latitude, longitude = resolve_pincode_coordinates(location_data.get("pincode"))
    if latitude is None or longitude is None:
        raise DomainError(
            "COORDINATES_UNRESOLVED",
            "latitude/longitude were not provided and could not be resolved from the given pincode.",
            status_code=422,
        )
    return latitude, longitude


def intake_order(company_id, order_ref, parent_order_ref, pickup, delivery, weight_kg, actor):
    existing = _find_existing_intake(company_id, order_ref)
    if existing is not None:
        return existing

    pickup_latitude, pickup_longitude = _resolve_stop_coordinates(pickup)

    try:
        with transaction.atomic():
            trip = None
            if parent_order_ref:
                # select_for_update() can't be combined with the DISTINCT that
                # a join through `stops` would require, so resolve the id
                # first, then lock that single row by primary key.
                trip_id = (
                    Trip.objects.filter(
                        company_id=company_id,
                        status=TripStatus.COLLECTING_PICKUPS,
                        stops__parent_order_ref=parent_order_ref,
                    )
                    .values_list("id", flat=True)
                    .first()
                )
                if trip_id is not None:
                    trip = Trip.objects.select_for_update().get(pk=trip_id)

            if trip is None:
                trip = Trip.objects.create(company_id=company_id)

            pickup_stop = TripStop.objects.create(
                company_id=company_id,
                trip=trip,
                stop_type=StopType.PICKUP,
                sequence_no=_next_sequence_no(trip),
                order_ref=order_ref,
                parent_order_ref=parent_order_ref,
                address=pickup["address"],
                latitude=pickup_latitude,
                longitude=pickup_longitude,
                weight_kg=weight_kg,
            )

            drop_stop = _drop_stop_for(trip, parent_order_ref)
            if drop_stop is None:
                # Only resolved when actually needed — a suborder joining an
                # existing staggered group reuses that group's shared drop
                # stop instead, with no geocode lookup for `delivery` at all.
                delivery_latitude, delivery_longitude = _resolve_stop_coordinates(delivery)
                drop_stop = TripStop.objects.create(
                    company_id=company_id,
                    trip=trip,
                    stop_type=StopType.DROP,
                    sequence_no=_next_sequence_no(trip),
                    order_ref=order_ref,
                    parent_order_ref=parent_order_ref,
                    address=delivery["address"],
                    latitude=delivery_latitude,
                    longitude=delivery_longitude,
                )

            _recompute_total_weight(trip)
    except IntegrityError:
        # Lost a race against a concurrent identical intake call — replay as
        # the idempotent result rather than surfacing the constraint error.
        existing = _find_existing_intake(company_id, order_ref)
        if existing is not None:
            return existing
        raise

    return {"trip_id": trip.id, "pickup_stop_id": pickup_stop.id, "drop_stop_id": drop_stop.id}


def lock_pickups(trip_id, actor):
    trip = get_trip(trip_id, actor.company_id)
    if trip.status != TripStatus.COLLECTING_PICKUPS:
        raise DomainError(
            "INVALID_TRIP_STATUS",
            f"Trip must be in '{TripStatus.COLLECTING_PICKUPS.label}' status to lock pickups.",
            status_code=409,
        )
    if not trip.stops.filter(stop_type=StopType.PICKUP, status=StopStatus.COMPLETED).exists():
        raise DomainError(
            "NO_COMPLETED_PICKUPS",
            "At least one pickup stop must be completed before locking pickups.",
            status_code=409,
        )
    trip.status = TripStatus.PICKUPS_LOCKED
    trip.save(update_fields=["status"])
    return trip


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def _estimate_minutes_until_free(vehicle, company_id):
    """Point 8 — how soon `vehicle` is expected to finish its current trip.

    Returns 0 if the vehicle isn't on an active trip at all (immediately
    available). Returns None if it IS on one but there isn't enough data to
    estimate honestly: the trip hasn't started moving yet (still collecting
    or has locked pickups — no location trail to project from), or its last
    location ping is stale/missing. A None here means "treat as busy,
    unknown ETA" — the caller excludes it, same as before this feature
    existed, rather than guessing.

    When there IS a fresh ping, the estimate is straight-line distance from
    the vehicle's last known position, through each remaining stop in
    sequence, at an assumed average speed — plus a fixed per-stop dwell time
    for loading/unloading/photo capture at each remaining stop. Both are
    tenant-configurable (assignment_eta_speed_kmph /
    assignment_eta_dwell_minutes) since real-world speed varies by city.

    VehicleType.default_loading_minutes/default_unloading_minutes are a
    natural future input to the dwell-time estimate below (per-vehicle-type
    instead of one tenant-wide assignment_eta_dwell_minutes) — deliberately
    not wired in yet, since that changes existing assignment-candidate
    behavior and deserves its own deliberate testing pass.
    """
    from tracking.models import TripLocationPing

    trip = (
        Trip.objects.filter(company_id=company_id, vehicle=vehicle, status__in=ACTIVE_TRIP_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if trip is None:
        return 0
    if trip.status != TripStatus.IN_TRANSIT:
        return None

    recent_ping = (
        TripLocationPing.objects.filter(
            vehicle_id=vehicle.id, recorded_at__gte=timezone.now() - ETA_PING_FRESHNESS
        )
        .order_by("-recorded_at")
        .first()
    )
    if recent_ping is None:
        return None

    remaining_stops = list(
        trip.stops.exclude(status__in=[StopStatus.COMPLETED, StopStatus.SKIPPED]).order_by("sequence_no")
    )
    if not remaining_stops:
        return None

    speed_kmph = get_tenant_setting(company_id, "assignment_eta_speed_kmph", DEFAULT_ASSIGNMENT_ETA_SPEED_KMPH)
    dwell_minutes = get_tenant_setting(
        company_id, "assignment_eta_dwell_minutes", DEFAULT_ASSIGNMENT_ETA_DWELL_MINUTES
    )

    total_distance_m = 0.0
    prev_lat, prev_lng = recent_ping.latitude, recent_ping.longitude
    for stop in remaining_stops:
        total_distance_m += haversine_distance_m(prev_lat, prev_lng, stop.latitude, stop.longitude)
        prev_lat, prev_lng = stop.latitude, stop.longitude

    drive_minutes = (total_distance_m / 1000) / speed_kmph * 60
    return round(drive_minutes + dwell_minutes * len(remaining_stops))


def get_assignment_candidates(company_id, trip_id=None, order_refs=None, within_minutes=None):
    """Returns {"matches": [{"vehicle": Vehicle, "drivers": [Driver, ...],
    "available_in_minutes": int}, ...],
    "excluded_vehicles": [{"vehicle": Vehicle, "reason": str}, ...]}.

    A vehicle lands in `excluded_vehicles` (rather than being silently
    dropped) only for the vehicle-type/DL-category mismatch case — it's
    otherwise assignable (active, enough capacity, free now or soon) but no
    currently-eligible driver is licensed for its category. Capacity-too-low
    and busy-with-no-honest-ETA vehicles are still just absent from both
    lists — those aren't a "this driver isn't licensed for this vehicle"
    situation, so surfacing them as disabled-with-reason in the Admin Panel
    vehicle picker isn't the point of this distinction.

    `within_minutes` sets how soon a busy vehicle must be expected to free
    up to still be offered as a match — see _estimate_minutes_until_free().
    `available_in_minutes` on each match is 0 for a vehicle that's free
    right now, or the estimated number of minutes until it is.

    Omitted (None) -> falls back to this tenant's configured
    assignment_window_minutes (TenantSetting, default 30).
    """
    if within_minutes is None:
        within_minutes = get_tenant_setting(company_id, "assignment_window_minutes", DEFAULT_ASSIGNMENT_WINDOW_MINUTES)

    if not trip_id and not order_refs:
        raise DomainError(
            "INVALID_CANDIDATE_REQUEST", "Provide either trip_id or order_refs.", status_code=400
        )

    if trip_id:
        trip = get_trip(trip_id, company_id)
        cumulative_weight = trip.total_weight_kg
    else:
        cumulative_weight = (
            TripStop.objects.filter(
                company_id=company_id, order_ref__in=order_refs, stop_type=StopType.PICKUP
            ).aggregate(total=Sum("weight_kg"))["total"]
            or 0
        )

    eligible_drivers = list(Driver.objects.filter(company_id=company_id))

    matches = []
    excluded_vehicles = []
    vehicles = Vehicle.objects.select_related("vehicle_type").filter(
        company_id=company_id, status=VehicleStatus.ACTIVE, capacity_kg__gte=cumulative_weight
    )
    for vehicle in vehicles:
        available_in_minutes = _estimate_minutes_until_free(vehicle, company_id)
        if available_in_minutes is None or available_in_minutes > within_minutes:
            continue
        matched_drivers = [
            driver
            for driver in eligible_drivers
            if driver.is_eligible_for_assignment and vehicle.vehicle_type.category in (driver.dl_allowed_categories or [])
        ]
        if not matched_drivers:
            category_label = vehicle.vehicle_type.get_category_display()
            excluded_vehicles.append(
                {
                    "vehicle": vehicle,
                    "reason": f"No currently-eligible driver is licensed for a {category_label} vehicle.",
                }
            )
            continue
        matches.append({"vehicle": vehicle, "drivers": matched_drivers, "available_in_minutes": available_in_minutes})

    return {"matches": matches, "excluded_vehicles": excluded_vehicles}


def _driver_ineligibility_reasons(driver):
    reasons = []
    if driver.aadhar_status != VerificationStatus.VERIFIED:
        reasons.append("Aadhar is not verified")
    if driver.police_status != VerificationStatus.VERIFIED:
        reasons.append("Police verification is not complete")
    if driver.dl_status != VerificationStatus.VERIFIED:
        reasons.append("Driving licence is not verified")
    elif driver.dl_expiry_date and driver.dl_expiry_date < date.today():
        reasons.append("Driving licence has expired")
    if driver.account_status != DriverAccountStatus.ACTIVE:
        reasons.append(f"Driver account is {driver.get_account_status_display()}")
    return reasons


def _validate_driver_eligible(driver):
    if driver.is_eligible_for_assignment:
        return
    reasons = _driver_ineligibility_reasons(driver) or ["Driver is not eligible for assignment."]
    raise DomainError("DRIVER_NOT_ELIGIBLE", "; ".join(reasons), status_code=409)


def _validate_vehicle_type_match(vehicle, driver):
    category = vehicle.vehicle_type.category
    if category in (driver.dl_allowed_categories or []):
        return
    allowed = ", ".join(driver.dl_allowed_categories or []) or "no categories"
    raise DomainError(
        "VEHICLE_TYPE_NOT_PERMITTED",
        f"Driver's DL only permits: {allowed}. This vehicle requires: {category}.",
        status_code=409,
    )


def _validate_capacity(vehicle, required_weight_kg):
    if vehicle.capacity_kg < required_weight_kg:
        raise DomainError(
            "VEHICLE_CAPACITY_EXCEEDED",
            f"Vehicle capacity ({vehicle.capacity_kg}kg) is less than the trip's weight ({required_weight_kg}kg).",
            status_code=409,
        )


def assign_vehicle(trip_id, vehicle_id, driver_id, actor):
    trip = get_trip(trip_id, actor.company_id)
    vehicle = _get_vehicle(vehicle_id, actor.company_id)
    driver = _get_driver(driver_id, actor.company_id)

    _validate_driver_eligible(driver)
    _validate_vehicle_type_match(vehicle, driver)
    _validate_capacity(vehicle, trip.total_weight_kg)

    trip.vehicle = vehicle
    trip.driver = driver
    trip.save(update_fields=["vehicle", "driver"])

    vehicle.current_driver_id = driver.id
    vehicle.save(update_fields=["current_driver_id"])
    driver.current_vehicle_id = vehicle.id
    driver.save(update_fields=["current_vehicle_id"])

    stop_count = trip.stops.count()
    transaction.on_commit(
        lambda: send_push_notification.delay(
            driver_id=driver.id,
            title="New Trip Assigned",
            body=f"New trip assigned — {stop_count} stops",
            data={"type": "trip_assigned", "trip_id": str(trip.id)},
        )
    )

    return trip


def reassign_vehicle(trip_id, new_vehicle_id, reason, actor, new_driver_id=None):
    trip = get_trip(trip_id, actor.company_id)
    new_vehicle = _get_vehicle(new_vehicle_id, actor.company_id)
    new_driver = _get_driver(new_driver_id, actor.company_id) if new_driver_id else trip.driver

    if new_driver is None:
        raise DomainError(
            "TRIP_HAS_NO_DRIVER", "Trip has no current driver; new_driver_id is required.", status_code=409
        )

    _validate_driver_eligible(new_driver)
    _validate_vehicle_type_match(new_vehicle, new_driver)
    _validate_capacity(new_vehicle, trip.total_weight_kg)

    previous_vehicle = trip.vehicle
    previous_driver = trip.driver

    with transaction.atomic():
        TripVehicleHistory.objects.create(
            company_id=trip.company_id,
            trip=trip,
            previous_vehicle=previous_vehicle,
            new_vehicle=new_vehicle,
            previous_driver=previous_driver,
            new_driver=new_driver,
            reason=reason,
        )

        if previous_vehicle is not None and previous_vehicle.id != new_vehicle.id:
            release_assignment(previous_vehicle, None)
        if previous_driver is not None and previous_driver.id != new_driver.id:
            release_assignment(None, previous_driver)

        trip.vehicle = new_vehicle
        trip.driver = new_driver
        trip.save(update_fields=["vehicle", "driver"])

        new_vehicle.current_driver_id = new_driver.id
        new_vehicle.save(update_fields=["current_driver_id"])
        new_driver.current_vehicle_id = new_vehicle.id
        new_driver.save(update_fields=["current_vehicle_id"])

        order_refs = trip.stops.filter(stop_type=StopType.PICKUP).values_list("order_ref", flat=True).distinct()
        for order_ref in order_refs:
            publish_webhook_event(
                company_id=trip.company_id,
                order_ref=order_ref,
                event_type="order.driver_reassigned",
                data={"newDriverName": new_driver.full_name, "reason": reason},
            )

    if previous_driver is not None and previous_driver.id != new_driver.id:
        transaction.on_commit(
            lambda: send_push_notification.delay(
                driver_id=previous_driver.id,
                title="Reassigned Off Trip",
                body=f"You've been reassigned off trip {trip.id}",
                data={"type": "trip_reassigned_away", "trip_id": str(trip.id)},
            )
        )
        transaction.on_commit(
            lambda: send_push_notification.delay(
                driver_id=new_driver.id,
                title="New Trip Assigned",
                body=f"New trip assigned — {trip.id}",
                data={"type": "trip_reassigned_to", "trip_id": str(trip.id)},
            )
        )

    return trip


# ---------------------------------------------------------------------------
# Stops
# ---------------------------------------------------------------------------


def _check_delivery_geofence(stop, trip):
    """Soft flag only — never blocks completion (see complete_stop). If the
    vehicle's most recent location ping (last ~2 min) is further than this
    tenant's geofence_meters from the stop's registered coordinates, flags
    it for admin visibility rather than rejecting the completion — GPS
    drift and indoor/basement deliveries can legitimately put a genuinely
    present driver 100-150m off.
    """
    # Local import: tracking already depends on trips.models (Trip), so a
    # module-level import here would be circular.
    from tracking.models import TripLocationPing

    if trip.vehicle_id is None:
        return

    recent_ping = (
        TripLocationPing.objects.filter(
            vehicle_id=trip.vehicle_id, recorded_at__gte=timezone.now() - RECENT_PING_WINDOW
        )
        .order_by("-recorded_at")
        .first()
    )
    if recent_ping is None:
        return

    distance_m = haversine_distance_m(recent_ping.latitude, recent_ping.longitude, stop.latitude, stop.longitude)
    threshold_m = get_tenant_setting(stop.company_id, "geofence_meters", DEFAULT_GEOFENCE_METERS)

    if distance_m > threshold_m:
        stop.location_mismatch = True
        stop.location_mismatch_meters = Decimal(str(round(distance_m, 2)))


def _gate_photo_types_for(stop):
    return PICKUP_GATE_PHOTO_TYPES if stop.stop_type == StopType.PICKUP else DELIVERY_GATE_PHOTO_TYPES


def _has_gate_photo(stop):
    return stop.photos.filter(photo_type__in=_gate_photo_types_for(stop)).exists()


def _add_stop_photo(stop, photo_type, photo_url, latitude=None, longitude=None):
    photo = TripPhoto.objects.create(
        company_id=stop.company_id,
        trip_stop=stop,
        photo_type=photo_type,
        photo_url=photo_url,
        latitude=latitude,
        longitude=longitude,
    )
    # Keep the legacy single field in sync for backward compatibility — set
    # to this stop's first/primary photo so nothing that still reads
    # proof_photo_url breaks.
    if not stop.proof_photo_url:
        stop.proof_photo_url = photo_url
        stop.save(update_fields=["proof_photo_url"])
    return photo


def add_trip_stop_photo(stop_id, photo_type, photo_url, actor, latitude=None, longitude=None):
    stop = _get_stop(stop_id, actor.company_id)
    return _add_stop_photo(stop, photo_type, photo_url, latitude, longitude)


def complete_stop(stop_id, proof_photo_url, actor):
    stop = _get_stop(stop_id, actor.company_id)
    trip = stop.trip

    if proof_photo_url:
        # Backward-compat shape (proofPhotoUrl directly on the complete
        # call) — auto-mapped to whichever gate-satisfying TripPhoto type
        # this stop is for, same as the dedicated photos endpoint would
        # produce.
        default_type = TripPhotoType.PICKUP if stop.stop_type == StopType.PICKUP else TripPhotoType.DELIVERY
        _add_stop_photo(stop, default_type, proof_photo_url)

    is_first_stop_overall = not trip.stops.filter(status=StopStatus.COMPLETED).exists()
    if is_first_stop_overall and not _has_gate_photo(stop):
        raise DomainError(
            "PROOF_PHOTO_REQUIRED", "A proof photo is required to complete the trip's first stop.", status_code=422
        )

    other_incomplete_remaining = trip.stops.exclude(pk=stop.pk).filter(
        status__in=[StopStatus.PENDING, StopStatus.ARRIVED]
    ).exists()
    if not other_incomplete_remaining and not _has_gate_photo(stop):
        raise DomainError(
            "PROOF_PHOTO_REQUIRED", "A proof photo is required to complete the trip's final stop.", status_code=422
        )

    with transaction.atomic():
        stop.status = StopStatus.COMPLETED
        stop.completed_at = timezone.now()
        _check_delivery_geofence(stop, trip)
        stop.save(update_fields=["status", "completed_at", "location_mismatch", "location_mismatch_meters"])

        if not other_incomplete_remaining:
            trip.status = TripStatus.DELIVERED
            trip.completed_at = timezone.now()
            trip.save(update_fields=["status", "completed_at"])
            release_assignment(trip.vehicle, trip.driver)

        publish_webhook_event(
            company_id=stop.company_id,
            order_ref=stop.order_ref,
            event_type="order.status_changed",
            data={
                "status": "picked_up" if stop.stop_type == StopType.PICKUP else "delivered",
                "location": {"lat": float(stop.latitude), "lng": float(stop.longitude)},
                "proof_photo_url": stop.proof_photo_url,
            },
        )

    return stop


def update_delivery_address(stop_id, new_address, new_lat, new_lng, actor):
    stop = _get_stop(stop_id, actor.company_id)
    old_address = stop.address

    with transaction.atomic():
        AddressChangeLog.objects.create(
            company_id=stop.company_id,
            trip_stop=stop,
            old_address=stop.address,
            new_address=new_address,
            old_latitude=stop.latitude,
            old_longitude=stop.longitude,
            new_latitude=new_lat,
            new_longitude=new_lng,
        )

        stop.address = new_address
        stop.latitude = new_lat
        stop.longitude = new_lng
        stop.save(update_fields=["address", "latitude", "longitude"])

        publish_webhook_event(
            company_id=stop.company_id,
            order_ref=stop.order_ref,
            event_type="order.delivery_address_updated",
            data={
                "old_address": old_address,
                "new_address": new_address,
                "new_location": {"lat": float(new_lat), "lng": float(new_lng)},
            },
        )

        if stop.trip.driver_id:
            order_ref = stop.order_ref
            driver_id = stop.trip.driver_id
            trip_id = stop.trip_id
            transaction.on_commit(
                lambda: send_push_notification.delay(
                    driver_id=driver_id,
                    title="Delivery Address Updated",
                    body=f"Delivery address updated for {order_ref}",
                    data={"type": "delivery_address_updated", "trip_id": str(trip_id), "order_ref": order_ref},
                )
            )

    return stop


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def cancel_order_stop(order_ref, reason, actor):
    """Scenario A — cancel a single suborder before it's picked up."""
    try:
        stop = (
            TripStop.objects.select_related("trip")
            .get(company_id=actor.company_id, order_ref=order_ref, stop_type=StopType.PICKUP)
        )
    except TripStop.DoesNotExist:
        raise DomainError("ORDER_NOT_FOUND", "No order found with this reference.", status_code=404)

    if stop.status != StopStatus.PENDING:
        raise DomainError(
            "CANNOT_CANCEL_AFTER_PICKUP",
            "This order has already been picked up; use the Issues module instead.",
            status_code=409,
        )

    trip = stop.trip

    with transaction.atomic():
        stop.soft_delete()

        if trip.stops.filter(stop_type=StopType.PICKUP).exists():
            _recompute_total_weight(trip)
        else:
            # No pickups left at all — an empty trip shouldn't sit in
            # collecting_pickups forever.
            trip.status = TripStatus.CANCELLED
            trip.save(update_fields=["status"])

        publish_webhook_event(
            company_id=trip.company_id,
            order_ref=order_ref,
            event_type="order.cancelled",
            data={"reason": reason, "trip_id": str(trip.id)},
        )

    return {"trip_id": trip.id, "trip_status": trip.status, "cancelled_order_ref": order_ref}


def cancel_trip(trip_id, reason, actor):
    """Scenario B — cancel an entire trip. AdminUser only (enforced by the
    view's permission class, not here — see trips.views.TripViewSet).
    """
    # Local import: issues depends on trips.services (via get_trip), so a
    # module-level import here would be circular.
    from issues.models import IssueSeverity, IssueType
    from issues.services import create_issue

    trip = get_trip(trip_id, actor.company_id)

    if trip.status not in ACTIVE_TRIP_STATUSES:
        raise DomainError(
            "INVALID_TRIP_STATUS",
            "Only a trip that is collecting pickups, pickups locked, or in transit can be cancelled.",
            status_code=409,
        )

    with transaction.atomic():
        # Critical rule: goods already picked up but not yet delivered can't
        # just vanish when the trip is cancelled — flag each one as an Issue
        # so it's visible in the Admin Panel's queue instead of silently lost.
        completed_pickups = trip.stops.filter(stop_type=StopType.PICKUP, status=StopStatus.COMPLETED)
        for pickup_stop in completed_pickups:
            drop_stop = _drop_stop_for(trip, pickup_stop.parent_order_ref)
            if drop_stop is not None and drop_stop.status != StopStatus.COMPLETED:
                create_issue(
                    trip_id=trip.id,
                    issue_type=IssueType.TRANSIT,
                    note="Trip cancelled with picked-up, undelivered goods — requires manual reassignment",
                    actor=actor,
                    trip_stop_id=pickup_stop.id,
                    severity=IssueSeverity.HIGH,
                )

        trip.status = TripStatus.CANCELLED
        trip.save(update_fields=["status"])

        release_assignment(trip.vehicle, trip.driver)

        order_refs = trip.stops.filter(stop_type=StopType.PICKUP).values_list("order_ref", flat=True).distinct()
        for order_ref in order_refs:
            publish_webhook_event(
                company_id=trip.company_id,
                order_ref=order_ref,
                event_type="order.trip_cancelled",
                data={"reason": reason, "trip_id": str(trip.id)},
            )

    return trip
