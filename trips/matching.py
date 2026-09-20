from django.conf import settings
from django.utils import timezone

from core.choices import TripStatus, VehicleStatus
from core.geo import haversine_distance_km
from drivers.models import Driver, Vehicle

from .models import ACTIVE_TRIP_STATUSES, Trip


class MatchingService:
    """Nearest-available-driver matching for a new trip. Single-city
    (Bengaluru) candidate pools are small enough that a plain Python
    haversine scan over them is simpler and fast enough — no need for
    PostGIS just for this.
    """

    @staticmethod
    def find_nearest_available_driver(trip):
        """Returns the (driver, vehicle) pair nearest to the trip's pickup
        point, or (None, None) if nobody qualifies. A driver qualifies if:
        - online, KYC-verified, active, in the same company as the trip
        - currently on a vehicle of the trip's vehicle_type, and that
          vehicle is itself active
        - not already on another active trip
        - has reported a location within DRIVER_MATCH_RADIUS_KM of pickup
        """
        candidate_vehicle_ids = Vehicle.objects.filter(
            company=trip.company, vehicle_type=trip.vehicle_type, status=VehicleStatus.ACTIVE
        ).values_list("id", flat=True)

        busy_driver_ids = (
            Trip.objects.filter(company=trip.company, status__in=ACTIVE_TRIP_STATUSES, driver_id__isnull=False)
            .exclude(pk=trip.pk)
            .values_list("driver_id", flat=True)
        )

        candidates = (
            Driver.objects.select_related("kyc")
            .filter(
                company=trip.company,
                is_online=True,
                current_vehicle_id__in=candidate_vehicle_ids,
                last_known_lat__isnull=False,
                last_known_lng__isnull=False,
            )
            .exclude(id__in=busy_driver_ids)
        )

        nearest_driver = None
        nearest_distance_km = None
        for driver in candidates:
            if not driver.is_eligible_for_assignment:
                continue
            distance_km = haversine_distance_km(
                trip.pickup_lat, trip.pickup_lng, driver.last_known_lat, driver.last_known_lng
            )
            if distance_km > settings.DRIVER_MATCH_RADIUS_KM:
                continue
            if nearest_distance_km is None or distance_km < nearest_distance_km:
                nearest_driver, nearest_distance_km = driver, distance_km

        if nearest_driver is None:
            return None, None

        vehicle = Vehicle.objects.get(id=nearest_driver.current_vehicle_id)
        return nearest_driver, vehicle

    @classmethod
    def try_assign_driver(cls, trip):
        """Attempts to assign the nearest available driver to `trip`.
        Mutates and saves `trip` either way — ASSIGNED with driver/vehicle
        set, or NO_DRIVER_AVAILABLE so the caller can retry later (see
        TripService.retry_assignment).
        """
        driver, vehicle = cls.find_nearest_available_driver(trip)

        if driver is None:
            trip.status = TripStatus.NO_DRIVER_AVAILABLE
            trip.save(update_fields=["status", "updated_at"])
            return trip

        trip.driver = driver
        trip.vehicle = vehicle
        trip.status = TripStatus.ASSIGNED
        trip.assigned_at = timezone.now()
        trip.save(update_fields=["driver", "vehicle", "status", "assigned_at", "updated_at"])
        return trip
