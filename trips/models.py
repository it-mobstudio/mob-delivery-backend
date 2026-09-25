from django.db import models

from core.choices import CancelledBy, ItemVerificationStatus, PaymentMode, PaymentStatus, PickupPhotoMode, TripStatus
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

    # Our own order number, shown to drivers and customers:
    # OD{YYYY}{MM}{DD}000{n}, n from TripNumber (see TripService.create_trip).
    # A trip with several drops would add _01, _02… per delivery — every trip
    # has exactly one drop today, so there's no suffix yet.
    order_number = models.CharField(max_length=40, unique=True, null=True, blank=True, editable=False)
    # A note for the driver about the whole order ("Call before arriving, use
    # gate 2") — shown on the order details screen.
    notes = models.CharField(max_length=500, blank=True, default="")

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

    # An extra flat amount for this trip specifically — e.g. compensation for
    # unloading heavy goods — on top of the fare card. Set once at booking
    # (TripCreateSerializer), paid to the driver in full (not split by
    # DRIVER_EARNING_PERCENT like total_fare is — see
    # drivers.wallet.WalletService.credit_trip_earning). Never charged to the
    # customer: it does not affect total_fare.
    bonus_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")

    # Set once at creation and never changed after. COD trips start
    # payment_status=pending; TripService.collect_cod_payment flips it to
    # paid once the driver confirms the customer scanned and paid.
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices, default=PaymentMode.PREPAID)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    cod_collected_at = models.DateTimeField(null=True, blank=True)

    # The scan-to-pay code issued for a COD trip and the payment that settled it
    # (see trips.payments). `payment_qr_id` is the provider's id for the code —
    # what its webhook refers to; `payment_reference` is the provider's id for
    # the payment itself (a Razorpay `pay_...`), for reconciliation.
    payment_provider = models.CharField(max_length=20, blank=True, default="")
    payment_qr_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    payment_qr_image_url = models.URLField(max_length=500, blank=True, default="")
    payment_qr_expires_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=64, blank=True, default="")

    # The goods' invoice, supplied by the company at booking (a URL to a PDF or
    # image it hosts, or one uploaded via POST /uploads with purpose
    # trip_invoice). The driver app offers it for download / WhatsApp sharing.
    invoice_url = models.URLField(max_length=1000, blank=True, default="")
    invoice_number = models.CharField(max_length=100, blank=True, default="")

    # When true, the driver has to tick off every TripItem (optionally with a
    # photo) at the drop before the trip can be paid for / completed — see
    # TripService.verify_item. Set once at booking.
    verify_items = models.BooleanField(default=False)

    # Proof of what left the pickup: `order` = one photo of the whole package
    # (pickup_photo_url), `per_item` = one per TripItem (its pickup_photo_url).
    # Set once at booking; the delivery can't start until they are taken — see
    # TripService.add_photo / driver_start.
    pickup_photo = models.CharField(max_length=20, choices=PickupPhotoMode.choices, default=PickupPhotoMode.NONE)
    pickup_photo_url = models.URLField(max_length=500, blank=True, default="")
    # The same at the drop: the delivery can't be paid for / completed until
    # these are taken.
    delivery_photo = models.CharField(max_length=20, choices=PickupPhotoMode.choices, default=PickupPhotoMode.NONE)
    delivery_photo_url = models.URLField(max_length=500, blank=True, default="")

    # A prepaid trip can still ask for the customer's delivery OTP (COD trips
    # always do): the driver has it texted at the drop, and completion needs it.
    delivery_otp = models.BooleanField(default=False)

    # What this trip paid the driver (a share of total_fare, fixed at
    # completion — see drivers.wallet.WalletService.credit_trip_earning).
    driver_earning = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

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


class TripItem(BaseModel):
    """One line of the goods on a trip: what the company says is being
    delivered (name, quantity, picture...) plus, when Trip.verify_items is on,
    what the driver says happened to it at the drop — which is the delivery
    history the company keeps.
    """

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField(default=0)  # keeps the company's order

    name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=30, blank=True, default="")  # "pcs", "kg", "box"...
    sku = models.CharField(max_length=100, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    image_url = models.URLField(max_length=1000, blank=True, default="")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Filled in by the driver.
    status = models.CharField(
        max_length=20, choices=ItemVerificationStatus.choices, default=ItemVerificationStatus.PENDING
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    proof_image_url = models.URLField(max_length=500, blank=True, default="")
    # Taken at the pickup / drop when Trip.pickup_photo / delivery_photo is
    # `per_item`.
    pickup_photo_url = models.URLField(max_length=500, blank=True, default="")
    delivery_photo_url = models.URLField(max_length=500, blank=True, default="")
    driver_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return f"{self.quantity} x {self.name}"


class TripNumber(models.Model):
    """Hands out the running number in Trip.order_number — one row per trip,
    so numbers are unique and increasing without any locking."""

    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def order_number_for(day, number):
        return f"OD{day:%Y%m%d}000{number}"
