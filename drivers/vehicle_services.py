from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from core.choices import UploadPurpose, VehicleStatus
from core.constants import DRIVER_MAX_VEHICLES, VEHICLE_MAX_PHOTOS
from core.exceptions import DomainError
from core.uploads import UploadService

from .models import Vehicle, VehiclePhoto


class VehicleService:
    @staticmethod
    def delete_vehicle_type(vehicle_type):
        """Soft-delete a VehicleType, refusing if any non-deleted Vehicle
        still references it — a clean API-level error mirroring the DB-level
        PROTECT constraint on Vehicle.vehicle_type, instead of letting a raw
        IntegrityError leak through (soft-deletes don't hit PROTECT at all,
        since no row is actually deleted at the DB level).
        """
        if Vehicle.objects.filter(vehicle_type=vehicle_type).exists():
            raise DomainError(
                "VEHICLE_TYPE_IN_USE",
                "This vehicle type is still assigned to one or more vehicles.",
            )
        vehicle_type.soft_delete()

    @staticmethod
    def has_active_trip(vehicle):
        # Local import: trips depends on drivers (Trip.vehicle -> Vehicle),
        # so importing it at module level here would be circular.
        from trips.models import ACTIVE_TRIP_STATUSES, Trip

        return Trip.objects.filter(vehicle=vehicle, status__in=ACTIVE_TRIP_STATUSES).exists()

    @classmethod
    def disable_vehicle(cls, vehicle):
        if cls.has_active_trip(vehicle):
            raise DomainError(
                "VEHICLE_HAS_ACTIVE_TRIP",
                "This vehicle has an active trip and cannot be disabled.",
            )
        vehicle.status = VehicleStatus.DISABLED
        vehicle.save(update_fields=["status"])
        vehicle.soft_delete()


class DriverVehicleService:
    """Vehicles a driver registers for themselves (their own bike, auto, ...):
    several per driver, each with pictures. Only the owner can take one on duty
    or manage it (see DriverService.available_vehicles); the company sees them in
    its fleet with `owner_driver_id` set and can disable them like any other."""

    @staticmethod
    def own_vehicles(driver):
        return (
            Vehicle.objects.filter(owner_driver=driver, company_id=driver.company_id)
            .select_related("vehicle_type")
            .prefetch_related("photos")
            .order_by("-created_at")
        )

    @classmethod
    def get(cls, driver, pk):
        """One of the driver's own vehicles; anyone else's is a plain 404."""
        return get_object_or_404(cls.own_vehicles(driver), pk=pk)

    @staticmethod
    def _check_images(files):
        # Everything is checked before anything is stored, so one bad picture
        # doesn't leave the others behind as orphans.
        for file in files:
            try:
                UploadService.validate(file, UploadPurpose.VEHICLE_PHOTO)
            except DjangoValidationError as exc:
                raise DomainError("INVALID_UPLOAD", "; ".join(exc.messages), status_code=400)

    @staticmethod
    def _store(vehicle, file):
        try:
            url = UploadService.store(file, UploadPurpose.VEHICLE_PHOTO, vehicle.company_id)
        except DjangoValidationError as exc:
            raise DomainError("INVALID_UPLOAD", "; ".join(exc.messages), status_code=400)
        return VehiclePhoto.objects.create(company=vehicle.company, vehicle=vehicle, url=url)

    @staticmethod
    def _sync_primary(vehicle):
        first = vehicle.photos.order_by("created_at", "id").first()
        vehicle.photo_url = first.url if first else None
        vehicle.save(update_fields=["photo_url", "updated_at"])

    @classmethod
    @transaction.atomic
    def create(cls, driver, vehicle_type, registration_number, capacity_kg=None, photos=()):
        if cls.own_vehicles(driver).count() >= DRIVER_MAX_VEHICLES:
            raise DomainError(
                "VEHICLE_LIMIT_REACHED",
                f"You can register up to {DRIVER_MAX_VEHICLES} vehicles. Remove one to add another.",
                status_code=409,
            )
        cls._check_images(photos)
        vehicle = Vehicle.objects.create(
            company=driver.company,
            owner_driver=driver,
            vehicle_type=vehicle_type,
            registration_number=registration_number,
            capacity_kg=capacity_kg or vehicle_type.default_capacity_kg,
        )
        for file in photos:
            cls._store(vehicle, file)
        cls._sync_primary(vehicle)
        return vehicle

    @classmethod
    def update(cls, vehicle, **fields):
        for name, value in fields.items():
            setattr(vehicle, name, value)
        vehicle.save(update_fields=[*fields, "updated_at"])
        return vehicle

    @classmethod
    @transaction.atomic
    def add_photo(cls, vehicle, file):
        if vehicle.photos.count() >= VEHICLE_MAX_PHOTOS:
            raise DomainError(
                "PHOTO_LIMIT_REACHED",
                f"A vehicle can have up to {VEHICLE_MAX_PHOTOS} photos. Remove one to add another.",
                status_code=409,
            )
        cls._check_images([file])
        cls._store(vehicle, file)
        cls._sync_primary(vehicle)
        return vehicle

    @classmethod
    @transaction.atomic
    def remove_photo(cls, vehicle, photo_id):
        photo = get_object_or_404(vehicle.photos.all(), pk=photo_id)
        photo.delete()
        cls._sync_primary(vehicle)
        return vehicle

    @classmethod
    def remove(cls, driver, vehicle):
        """Retires the vehicle (it drops out of the driver's list and the company's
        fleet; past trips keep their record). Not while they're on duty with it or
        while a trip is using it."""
        if driver.is_online and driver.current_vehicle_id == vehicle.id:
            raise DomainError(
                "VEHICLE_ON_DUTY", "You're on duty with this vehicle. Go off duty before removing it.", status_code=409
            )
        VehicleService.disable_vehicle(vehicle)
        if driver.current_vehicle_id == vehicle.id:
            driver.current_vehicle_id = None
            driver.save(update_fields=["current_vehicle_id", "updated_at"])
