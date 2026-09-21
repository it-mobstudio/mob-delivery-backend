"""What a driver sends and sees for the vehicles they register themselves."""

from decimal import Decimal

from rest_framework import serializers

from core.choices import VehicleTypeStatus
from core.constants import REGISTRATION_NUMBER_RE, VEHICLE_MAX_PHOTOS
from core.serializers import MediaUrlField

from .models import Vehicle, VehicleType
from .vehicle_serializers import VehiclePhotoSerializer, VehicleTypeSummarySerializer


class DriverVehicleTypeSerializer(serializers.ModelSerializer):
    """A kind of vehicle the company runs - what a driver picks when adding theirs."""

    icon_image_url = MediaUrlField()

    class Meta:
        model = VehicleType
        fields = ["id", "name", "category", "default_capacity_kg", "icon_image_url"]
        read_only_fields = fields


class DriverOwnVehicleSerializer(serializers.ModelSerializer):
    vehicle_type = VehicleTypeSummarySerializer(read_only=True)
    photo_url = MediaUrlField()
    photos = VehiclePhotoSerializer(many=True, read_only=True)
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields = [
            "id",
            "vehicle_type",
            "registration_number",
            "capacity_kg",
            "photo_url",
            "photos",
            "status",
            "is_current",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_current(self, vehicle) -> bool:
        request = self.context.get("request")
        current = getattr(getattr(request, "user", None), "current_vehicle_id", None)
        return current is not None and str(current) == str(vehicle.id)


class DriverVehicleWriteSerializer(serializers.Serializer):
    vehicle_type_id = serializers.PrimaryKeyRelatedField(source="vehicle_type", queryset=VehicleType.objects.none())
    registration_number = serializers.CharField(max_length=20)
    capacity_kg = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, min_value=Decimal("0.01"))
    photos = serializers.ListField(child=serializers.FileField(), required=False, max_length=VEHICLE_MAX_PHOTOS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company_id = getattr(getattr(self.context.get("request"), "user", None), "company_id", None)
        if company_id is not None:
            self.fields["vehicle_type_id"].queryset = VehicleType.objects.filter(
                company_id=company_id, status=VehicleTypeStatus.ACTIVE
            )

    def validate_registration_number(self, value):
        value = value.strip().upper()
        if not REGISTRATION_NUMBER_RE.match(value):
            raise serializers.ValidationError("Use only letters, numbers and hyphens - no spaces.")
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        number = attrs.get("registration_number", getattr(self.instance, "registration_number", None))
        if request is not None and number:
            taken = Vehicle.objects.filter(company_id=request.user.company_id, registration_number=number)
            if self.instance is not None:
                taken = taken.exclude(pk=self.instance.pk)
            if taken.exists():
                raise serializers.ValidationError({"registration_number": "A vehicle with this registration number already exists."})
        return attrs


class DriverVehicleUpdateSerializer(DriverVehicleWriteSerializer):
    """PATCH: the same fields, all optional; pictures have their own endpoints."""

    photos = None


class DriverVehiclePhotoUploadSerializer(serializers.Serializer):
    photo = serializers.FileField()


# -- Documentation shapes (the wrappers these endpoints answer with) -------------------


class DriverVehicleTypesSerializer(serializers.Serializer):
    vehicle_types = DriverVehicleTypeSerializer(many=True)


class DriverOwnVehiclesSerializer(serializers.Serializer):
    vehicles = DriverOwnVehicleSerializer(many=True)
