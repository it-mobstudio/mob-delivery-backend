from django.db import models

from core.models import BaseModel
from drivers.models import Driver
from vehicles.models import Vehicle


class TripStatus(models.TextChoices):
    COLLECTING_PICKUPS = "collecting_pickups", "Collecting Pickups"
    PICKUPS_LOCKED = "pickups_locked", "Pickups Locked"
    IN_TRANSIT = "in_transit", "In Transit"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"


# Statuses under which a vehicle/driver is considered "on a trip" — shared by
# vehicles.services.has_active_trip and drivers.services.has_active_trip.
ACTIVE_TRIP_STATUSES = [
    TripStatus.COLLECTING_PICKUPS,
    TripStatus.PICKUPS_LOCKED,
    TripStatus.IN_TRANSIT,
]


class Trip(BaseModel):
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.PROTECT, related_name="trips")
    driver = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.PROTECT, related_name="trips")
    status = models.CharField(
        max_length=30, choices=TripStatus.choices, default=TripStatus.COLLECTING_PICKUPS, db_index=True
    )
    total_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Trip {self.id}"


class StopType(models.TextChoices):
    PICKUP = "pickup", "Pickup"
    DROP = "drop", "Drop"


class StopStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ARRIVED = "arrived", "Arrived"
    COMPLETED = "completed", "Completed"
    SKIPPED = "skipped", "Skipped"


class PaymentMode(models.TextChoices):
    PREPAID = "prepaid", "Prepaid"
    UPI_ON_DELIVERY = "upi_on_delivery", "UPI on Delivery"  # placeholder only, not implemented


class TripStop(BaseModel):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="stops")
    stop_type = models.CharField(max_length=10, choices=StopType.choices)
    sequence_no = models.IntegerField()
    order_ref = models.CharField(max_length=100)  # suborder-level reference from the client
    parent_order_ref = models.CharField(max_length=100, null=True, blank=True)
    address = models.CharField(max_length=500)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    status = models.CharField(max_length=20, choices=StopStatus.choices, default=StopStatus.PENDING)
    proof_photo_url = models.URLField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices, default=PaymentMode.PREPAID)
    payment_status = models.CharField(max_length=20, null=True, blank=True)  # placeholder only

    # Delivery geofence check (soft flag only — never blocks completion; see
    # trips.services.complete_stop). Set when the most recent location ping
    # at completion time is further than the tenant's geofence_meters
    # threshold from this stop's registered coordinates.
    location_mismatch = models.BooleanField(default=False)
    location_mismatch_meters = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["sequence_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "order_ref", "stop_type"],
                condition=models.Q(is_deleted=False),
                name="unique_tripstop_order_ref_type_per_company",
            )
        ]

    def __str__(self):
        return f"{self.get_stop_type_display()} — {self.order_ref}"


class TripVehicleHistory(BaseModel):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="vehicle_history")
    previous_vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    new_vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="+")
    previous_driver = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    new_driver = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reassignment on trip {self.trip_id}"


class AddressChangeLog(BaseModel):
    trip_stop = models.ForeignKey(TripStop, on_delete=models.CASCADE, related_name="address_changes")
    old_address = models.CharField(max_length=500)
    new_address = models.CharField(max_length=500)
    old_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    old_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    new_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    new_longitude = models.DecimalField(max_digits=9, decimal_places=6)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Address change on stop {self.trip_stop_id}"
