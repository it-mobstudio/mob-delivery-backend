from rest_framework import serializers

from core.choices import PaymentMode
from core.constants import OTP_RE
from drivers.models import Driver, Vehicle, VehicleType
from drivers.vehicle_serializers import VehicleTypeSummarySerializer

from .models import Trip


class PointSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=255)
    lat = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)
    contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)


class TripEstimateRequestSerializer(serializers.Serializer):
    pickup = PointSerializer()
    drop = PointSerializer()


class TripCreateSerializer(serializers.Serializer):
    vehicle_type_id = serializers.PrimaryKeyRelatedField(source="vehicle_type", queryset=VehicleType.objects.none())
    pickup = PointSerializer()
    drop = PointSerializer()
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices)
    reference_id = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and getattr(request, "user", None) is not None:
            self.fields["vehicle_type_id"].queryset = VehicleType.objects.filter(company_id=request.user.company_id)

    def validate(self, attrs):
        # The delivery OTP that finalizes a COD trip (see
        # TripService.collect_cod_payment) is sent to this number — it has
        # to exist for a COD trip even though it's optional otherwise.
        if attrs["payment_mode"] == PaymentMode.COD and not attrs["drop"].get("contact_phone"):
            raise serializers.ValidationError(
                {"drop": {"contact_phone": "Required for a COD trip — the finalizing OTP is sent to this number."}}
            )
        return attrs


class TripDriverSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = ["id", "full_name", "phone_number"]


class TripVehicleSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ["id", "registration_number"]


class TripSerializer(serializers.ModelSerializer):
    vehicle_type = VehicleTypeSummarySerializer(read_only=True)
    driver = TripDriverSummarySerializer(read_only=True)
    vehicle = TripVehicleSummarySerializer(read_only=True)

    class Meta:
        model = Trip
        fields = [
            "id",
            "status",
            "reference_id",
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
            "currency",
            "payment_mode",
            "payment_status",
            "cod_collected_at",
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


class TripListSerializer(TripSerializer):
    class Meta(TripSerializer.Meta):
        fields = [
            "id",
            "status",
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


class TripCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class TripCompleteSerializer(serializers.Serializer):
    """otp is only required for a COD trip — see TripService.driver_complete,
    which is where that's actually enforced (this only validates shape)."""

    otp = serializers.CharField(required=False, allow_blank=True)

    def validate_otp(self, value):
        value = value.strip()
        if value and not OTP_RE.match(value):
            raise serializers.ValidationError("OTP must be exactly 6 digits.")
        return value
