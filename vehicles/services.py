from core.exceptions import DomainError

from .models import Vehicle, VehicleStatus


def delete_vehicle_type(vehicle_type):
    """Soft-delete a VehicleType, refusing if any non-deleted Vehicle still
    references it — a clean API-level error mirroring the DB-level PROTECT
    constraint on Vehicle.vehicle_type, instead of letting a raw
    IntegrityError leak through (soft-deletes don't hit PROTECT at all,
    since no row is actually deleted at the DB level).
    """
    if Vehicle.objects.filter(vehicle_type=vehicle_type).exists():
        raise DomainError(
            "VEHICLE_TYPE_IN_USE",
            "This vehicle type is still assigned to one or more vehicles.",
        )
    vehicle_type.soft_delete()


def has_active_trip(vehicle):
    from trips.models import ACTIVE_TRIP_STATUSES, Trip

    return Trip.objects.filter(vehicle=vehicle, status__in=ACTIVE_TRIP_STATUSES).exists()


def disable_vehicle(vehicle):
    if has_active_trip(vehicle):
        raise DomainError(
            "VEHICLE_HAS_ACTIVE_TRIP",
            "This vehicle has an active trip and cannot be disabled.",
        )
    vehicle.status = VehicleStatus.DISABLED
    vehicle.save(update_fields=["status"])
    vehicle.soft_delete()
