from django.core.validators import URLValidator
from rest_framework import serializers

from core.choices import ItemVerificationStatus, PaymentMode, PickupPhotoMode
from core.constants import MAX_TRIP_ITEMS, OTP_LENGTH, OTP_RE
from core.serializers import MediaUrlField
from drivers.models import Driver, Vehicle, VehicleType
from drivers.vehicle_serializers import VehicleTypeSummarySerializer

from .models import Trip, TripItem


class HttpUrlField(serializers.URLField):
    """A URL the driver's phone will be asked to open or download, so only
    http(s) — no ftp:, file: or the like."""

    default_validators = [URLValidator(schemes=["http", "https"])]


class PointSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=255)
    lat = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)
    contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)


class TripEstimateRequestSerializer(serializers.Serializer):
    pickup = PointSerializer()
    drop = PointSerializer()


class TripItemInputSerializer(serializers.Serializer):
    """One line of the goods, as the company sends it when booking."""

    name = serializers.CharField(max_length=200)
    quantity = serializers.IntegerField(min_value=1, max_value=1_000_000, default=1)
    unit = serializers.CharField(max_length=30, required=False, allow_blank=True)  # "pcs", "kg", "box"
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=255, required=False, allow_blank=True)
    image_url = HttpUrlField(max_length=1000, required=False, allow_blank=True)
    unit_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False, allow_null=True
    )


class TripCreateSerializer(serializers.Serializer):
    vehicle_type_id = serializers.PrimaryKeyRelatedField(source="vehicle_type", queryset=VehicleType.objects.none())
    pickup = PointSerializer()
    drop = PointSerializer()
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices)
    reference_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)

    # The goods. `invoice_url` is a link to the invoice document (host it
    # yourself, or upload it via POST /uploads with purpose=trip_invoice and
    # pass the URL that comes back); the driver can download it and send it to
    # the customer on WhatsApp. With `verify_items` on, the driver must confirm
    # each item at the drop (optionally with a photo) before the trip can be
    # paid for or completed, and that confirmation is kept as delivery history.
    invoice_url = HttpUrlField(max_length=1000, required=False, allow_blank=True)
    invoice_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    verify_items = serializers.BooleanField(required=False, default=False)
    # Photos the driver must take (with the camera) at the pickup before the
    # delivery can start: none, one of the whole order, or one per item.
    pickup_photo = serializers.ChoiceField(
        choices=PickupPhotoMode.choices, required=False, default=PickupPhotoMode.NONE
    )
    # Prepaid trips: text the customer a delivery OTP the driver must enter to
    # complete (COD trips always need one).
    delivery_otp = serializers.BooleanField(required=False, default=False)
    # The same at the drop, before the delivery can be paid for / completed.
    delivery_photo = serializers.ChoiceField(
        choices=PickupPhotoMode.choices, required=False, default=PickupPhotoMode.NONE
    )
    items = TripItemInputSerializer(many=True, required=False, max_length=MAX_TRIP_ITEMS)

    # An extra flat amount for this trip, paid to the driver on top of the
    # fare card — e.g. for unloading heavy goods. Never charged to the
    # customer. Optional; omit or send 0 for a trip with none.
    bonus_fare = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False, allow_null=True
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company_id = getattr(getattr(self.context.get("request"), "user", None), "company_id", None)
        if company_id is not None:
            self.fields["vehicle_type_id"].queryset = VehicleType.objects.filter(company_id=company_id)

    def validate(self, attrs):
        # The delivery OTP that finalizes a COD trip (see
        # TripService.collect_cod_payment) is sent to this number — it has
        # to exist for a COD trip even though it's optional otherwise.
        needs_otp = attrs["payment_mode"] == PaymentMode.COD or attrs.get("delivery_otp")
        if needs_otp and not attrs["drop"].get("contact_phone"):
            raise serializers.ValidationError(
                {"drop": {"contact_phone": "Required when a delivery OTP is used — the OTP is sent to this number."}}
            )
        if attrs.get("verify_items") and not attrs.get("items"):
            raise serializers.ValidationError({"items": "Add at least one item to have the driver verify it."})
        per_item = PickupPhotoMode.PER_ITEM in (attrs.get("pickup_photo"), attrs.get("delivery_photo"))
        if per_item and not attrs.get("items"):
            raise serializers.ValidationError({"items": "Add at least one item to have the driver photograph it."})
        return attrs


class TripDriverSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = ["id", "full_name", "phone_number"]


class TripVehicleSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ["id", "registration_number"]


class TripItemSerializer(serializers.ModelSerializer):
    image_url = MediaUrlField()
    proof_image_url = MediaUrlField()
    pickup_photo_url = MediaUrlField()
    delivery_photo_url = MediaUrlField()

    class Meta:
        model = TripItem
        fields = [
            "id",
            "position",
            "name",
            "quantity",
            "unit",
            "sku",
            "notes",
            "image_url",
            "unit_price",
            "status",
            "verified_at",
            "proof_image_url",
            "pickup_photo_url",
            "delivery_photo_url",
            "driver_note",
        ]
        read_only_fields = fields


class TripSerializer(serializers.ModelSerializer):
    vehicle_type = VehicleTypeSummarySerializer(read_only=True)
    driver = TripDriverSummarySerializer(read_only=True)
    vehicle = TripVehicleSummarySerializer(read_only=True)
    invoice_url = MediaUrlField()
    pickup_photo_url = MediaUrlField()
    delivery_photo_url = MediaUrlField()
    items = TripItemSerializer(many=True, read_only=True)

    class Meta:
        model = Trip
        fields = [
            "id",
            "status",
            "order_number",
            "reference_id",
            "notes",
            "vehicle_type",
            "driver",
            "vehicle",
            "pickup_address",
            "pickup_lat",
            "pickup_lng",
            "pickup_contact_name",
            "pickup_contact_phone",
            "drop_address",
            "drop_lat",
            "drop_lng",
            "drop_contact_name",
            "drop_contact_phone",
            "distance_meters",
            "duration_seconds",
            "route_polyline",
            "polyline_precision",
            "base_fare",
            "distance_fare",
            "time_fare",
            "surge_multiplier",
            "total_fare",
            "bonus_fare",
            "currency",
            "payment_mode",
            "payment_status",
            "cod_collected_at",
            "payment_reference",
            "invoice_url",
            "invoice_number",
            "verify_items",
            "pickup_photo",
            "pickup_photo_url",
            "delivery_photo",
            "delivery_photo_url",
            "delivery_otp",
            "items",
            "cancellation_reason",
            "cancelled_by",
            "assigned_at",
            "arrived_at_pickup_at",
            "started_at",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DriverTripSerializer(TripSerializer):
    """The full trip as the assigned driver sees it — adds what the trip pays
    them, which the company-facing TripSerializer deliberately leaves out."""

    class Meta(TripSerializer.Meta):
        fields = TripSerializer.Meta.fields + ["driver_earning"]
        read_only_fields = fields


class TripListSerializer(TripSerializer):
    class Meta(TripSerializer.Meta):
        fields = [
            "id",
            "status",
            "order_number",
            "reference_id",
            "vehicle_type",
            "driver",
            "vehicle",
            "pickup_address",
            "drop_address",
            "total_fare",
            "currency",
            "payment_mode",
            "payment_status",
            "created_at",
        ]
        read_only_fields = fields


class DriverTripListSerializer(TripListSerializer):
    """A driver's trip history row — TripListSerializer plus the timing,
    distance, earning and cancellation fields the history/detail cards show,
    without dragging in the whole route polyline for every row."""

    class Meta(TripSerializer.Meta):
        fields = TripListSerializer.Meta.fields + [
            "driver_earning",
            "distance_meters",
            "duration_seconds",
            "assigned_at",
            "completed_at",
            "cancelled_at",
            "cancellation_reason",
            "cancelled_by",
        ]
        read_only_fields = fields


class DriverNavigationQuerySerializer(serializers.Serializer):
    lat = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)


class TripCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class TripCompleteSerializer(serializers.Serializer):
    """otp is only required for a COD trip — see TripService.driver_complete,
    which is where that's actually enforced (this only validates shape)."""

    otp = serializers.CharField(required=False, allow_blank=True)

    def validate_otp(self, value):
        value = value.strip()
        if value and not OTP_RE.match(value):
            raise serializers.ValidationError(f"OTP must be exactly {OTP_LENGTH} digits.")
        return value


class TripItemVerifySerializer(serializers.Serializer):
    """POST /driver/trips/{id}/items/{item_id}/verify — multipart: `status`,
    optional `note` (required when the item was not delivered) and an optional
    `photo`."""

    status = serializers.ChoiceField(
        choices=[ItemVerificationStatus.DELIVERED, ItemVerificationStatus.NOT_DELIVERED]
    )
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    photo = serializers.FileField(required=False)

    def validate(self, attrs):
        attrs["note"] = attrs["note"].strip()
        if attrs["status"] == ItemVerificationStatus.NOT_DELIVERED and not attrs["note"]:
            raise serializers.ValidationError({"note": "Say what happened to this item."})
        return attrs


class TripPickupPhotoSerializer(serializers.Serializer):
    """POST /driver/trips/{id}/pickup-photo and /delivery-photo — multipart:
    the `photo`, and the `item_id` it shows when the trip wants one photo per
    item."""

    photo = serializers.FileField()
    item_id = serializers.UUIDField(required=False, allow_null=True)


# -- Response shapes -------------------------------------------------------------
# Hand-built responses (dicts). They describe the wire format for the API schema
# (core.openapi) and document what each field means. Fares in these particular
# responses are JSON *numbers*, not decimal strings — see the Conventions guide.


class TripEstimateSerializer(serializers.Serializer):
    vehicle_type_id = serializers.UUIDField(help_text="Pass this as `vehicle_type_id` to `POST /trips` to book this option.")
    vehicle_type_name = serializers.CharField(help_text="Display name, e.g. `Bike`.")
    category = serializers.CharField(help_text="`two_wheeler`, `three_wheeler` or `four_wheeler`.")
    icon_image_url = serializers.URLField(allow_null=True, help_text="The vehicle type's icon, if the company set one.")
    distance_meters = serializers.IntegerField(help_text="Route distance in metres.")
    duration_seconds = serializers.IntegerField(help_text="Estimated driving time in seconds.")
    route_polyline = serializers.CharField(help_text="The route as an encoded polyline (precision given by `polyline_precision`).")
    polyline_precision = serializers.IntegerField(help_text="Decimal places the polyline is encoded at (6 — not Google's 5).")
    base_fare = serializers.FloatField(help_text="Flat starting fare.")
    distance_fare = serializers.FloatField(help_text="Per-km charge for this route.")
    time_fare = serializers.FloatField(help_text="Per-minute charge for this route.")
    surge_multiplier = serializers.FloatField(help_text="Demand multiplier applied to the sum of the three (currently always 1.0).")
    total_fare = serializers.FloatField(help_text="What the trip would cost: (base + distance + time) x surge, never less than the type's minimum fare.")
    currency = serializers.CharField(help_text="ISO currency code (`INR`).")


class TripEstimatesSerializer(serializers.Serializer):
    estimates = TripEstimateSerializer(many=True, help_text="One entry per active vehicle type in the company's fleet.")


class ActiveTripSerializer(serializers.Serializer):
    trip = DriverTripSerializer(allow_null=True, help_text="The trip the driver is on (assigned, at pickup or in progress), or `null` when there isn't one.")


class NavigationRouteSerializer(serializers.Serializer):
    target = serializers.ChoiceField(choices=["pickup", "drop"], help_text="The stop this route leads to: the pickup until the driver has picked up, the drop after.")
    target_lat = serializers.FloatField(help_text="Latitude of that stop.")
    target_lng = serializers.FloatField(help_text="Longitude of that stop.")
    polyline = serializers.CharField(help_text="Route from the driver's position to the stop, encoded at `polyline_precision`.")
    polyline_precision = serializers.IntegerField(help_text="Decimal places the polyline is encoded at (6).")
    distance_meters = serializers.IntegerField(help_text="Remaining distance in metres.")
    duration_seconds = serializers.IntegerField(help_text="Estimated remaining driving time in seconds.")


class PaymentQrSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(
        choices=["razorpay", "upi_static"],
        help_text="Who made the code. `razorpay` in production; `upi_static` is the local-development stand-in.",
    )
    reference = serializers.CharField(allow_null=True, help_text="The provider's id for this code (a Razorpay `qr_...`). Its webhook refers to the code by this id.")
    image_url = serializers.URLField(allow_null=True, help_text="A ready-made QR image hosted by Razorpay — display it as an image. `null` for `upi_static`.")
    qr_payload = serializers.CharField(allow_null=True, help_text="Only for `upi_static`: a UPI deep link (`upi://pay?...`) to draw as a QR yourself. `null` for `razorpay`.")
    amount = serializers.FloatField(help_text="The amount to collect: the trip's `total_fare`. The code is fixed to exactly this amount.")
    currency = serializers.CharField(help_text="ISO currency code (`INR`).")
    expires_at = serializers.DateTimeField(allow_null=True, help_text="When the code stops working. Asking again after this issues a fresh one. `null` for `upi_static`.")


class PaymentCollectedSerializer(serializers.Serializer):
    message = serializers.CharField(help_text="Confirms the payment was recorded and to which number the delivery OTP went.")
    otp = serializers.CharField(
        required=False,
        help_text="The delivery OTP. **Only present on non-production servers** (`DRIVER_OTP_DEBUG_RESPONSE=True`); in production only the customer ever sees it.",
    )
