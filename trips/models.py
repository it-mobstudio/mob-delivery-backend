from django.db import models

from core.choices import CancelledBy, PaymentMode, PaymentStatus, TripStatus
from core.models import BaseModel
from drivers.models import Driver, Vehicle, VehicleType

# Statuses that count as "this driver/vehicle is busy" — read by
# drivers.services.DriverService.has_active_trip,
# drivers.vehicle_services.VehicleService.has_active_trip, and
# trips.matching when ranking candidates for a new trip.
ACTIVE_TRIP_STATUSES = [
    TripStatus.REQUESTED,
    TripStatus.ASSIGNED,
    TripStatus.ARRIVED_AT_PICKUP,
    TripStatus.IN_PROGRESS,
]


class Trip(BaseModel):
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.PROTECT, related_name="trips")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="trips")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="trips")
    status = models.CharField(max_length=30, choices=TripStatus.choices, default=TripStatus.REQUESTED)

    # An id in the calling company's own system (order/shipment id) —
    # optional, lets them reconcile webhooks/Kafka events without storing
    # our uuid on their side first.
    reference_id = models.CharField(max_length=100, blank=True)

    pickup_address = models.CharField(max_length=255)
    pickup_lat = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_lng = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_contact_name = models.CharField(max_length=150, blank=True)
    pickup_contact_phone = models.CharField(max_length=20, blank=True)

    drop_address = models.CharField(max_length=255)
    drop_lat = models.DecimalField(max_digits=9, decimal_places=6)
    drop_lng = models.DecimalField(max_digits=9, decimal_places=6)
    drop_contact_name = models.CharField(max_length=150, blank=True)
    drop_contact_phone = models.CharField(max_length=20, blank=True)

    # Route — from trips.routing.get_route (Valhalla) at creation time.
    distance_meters = models.PositiveIntegerField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    route_polyline = models.TextField(blank=True)
    polyline_precision = models.PositiveSmallIntegerField(default=6)

    # Fare — from trips.pricing.calculate_fare at creation time.
    base_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    distance_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    time_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    surge_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1)
    total_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")

    # Set once at creation and never changed after. COD trips start
    # payment_status=pending; TripService.collect_cod_payment flips it to
    # paid once the driver confirms the customer scanned and paid.
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices, default=PaymentMode.PREPAID)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    cod_collected_at = models.DateTimeField(null=True, blank=True)

    cancellation_reason = models.CharField(max_length=255, blank=True)
    cancelled_by = models.CharField(max_length=20, choices=CancelledBy.choices, blank=True)

    assigned_at = models.DateTimeField(null=True, blank=True)
    arrived_at_pickup_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["driver", "status"]),
        ]

    def __str__(self):
        return f"Trip {self.id} ({self.status})"
