from decimal import ROUND_HALF_UP, Decimal

from core.constants import FARE_ROUNDING_CENTS
from core.exceptions import DomainError

CENTS = Decimal(FARE_ROUNDING_CENTS)


class PricingService:
    """Fare calculation against a VehicleType's fare card (base_fare,
    per_km_rate, per_min_rate, min_fare — see drivers.models.VehicleType).
    """

    @staticmethod
    def get_surge_multiplier(vehicle_type, pickup_lat, pickup_lng):
        """No live demand/supply signal (open trip count vs. online driver
        count per zone) exists yet — always 1.0x. Single place to wire up
        real surge pricing once that data exists; calculate_fare already
        threads pickup coordinates through to here for that purpose.
        """
        return Decimal("1.0")

    @classmethod
    def calculate_fare(cls, vehicle_type, distance_meters, duration_seconds, pickup_lat, pickup_lng):
        if vehicle_type.min_fare <= 0:
            raise DomainError(
                "FARE_NOT_CONFIGURED",
                f"No fare card is configured for vehicle type '{vehicle_type.name}'.",
                status_code=422,
            )

        distance_km = Decimal(distance_meters) / Decimal(1000)
        duration_min = Decimal(duration_seconds) / Decimal(60)

        base_fare = vehicle_type.base_fare
        distance_fare = (distance_km * vehicle_type.per_km_rate).quantize(CENTS, rounding=ROUND_HALF_UP)
        time_fare = (duration_min * vehicle_type.per_min_rate).quantize(CENTS, rounding=ROUND_HALF_UP)
        surge_multiplier = cls.get_surge_multiplier(vehicle_type, pickup_lat, pickup_lng)

        subtotal = base_fare + distance_fare + time_fare
        total_fare = (subtotal * surge_multiplier).quantize(CENTS, rounding=ROUND_HALF_UP)
        total_fare = max(total_fare, vehicle_type.min_fare)

        return {
            "base_fare": base_fare,
            "distance_fare": distance_fare,
            "time_fare": time_fare,
            "surge_multiplier": surge_multiplier,
            "total_fare": total_fare,
            "currency": "INR",
        }
