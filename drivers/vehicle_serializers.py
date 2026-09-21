from rest_framework import serializers

from core.choices import VehicleDocumentType, VehicleTypeStatus
from core.constants import REGISTRATION_NUMBER_RE
from core.serializers import MediaUrlField

from .models import Vehicle, VehicleDocument, VehiclePhoto, VehicleType


class VehicleTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleType
        fields = [
            "id",
            "name",
            "category",
            "default_capacity_kg",
            "icon_image_url",
            "status",
            "base_fare",
            "per_km_rate",
            "per_min_rate",
            "min_fare",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_default_capacity_kg(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be a positive number.")
        return value

    def validate_base_fare(self, value):
        if value < 0:
            raise serializers.ValidationError("Cannot be negative.")
        return value

    def validate_per_km_rate(self, value):
        if value < 0:
            raise serializers.ValidationError("Cannot be negative.")
        return value

    def validate_per_min_rate(self, value):
        if value < 0:
            raise serializers.ValidationError("Cannot be negative.")
        return value

    def validate_min_fare(self, value):
        if value < 0:
            raise serializers.ValidationError("Cannot be negative.")
        return value

    def validate(self, attrs):
        # DRF's automatic unique-together validation skips UniqueConstraints
        # that have a `condition` (ours is soft-delete-aware), so the
        # per-company name uniqueness has to be checked explicitly here —
        # otherwise a duplicate falls through to a raw DB IntegrityError.
        request = self.context.get("request")
        name = attrs.get("name", getattr(self.instance, "name", None))
        if request is not None and name:
            qs = VehicleType.objects.filter(company_id=request.user.company_id, name=name)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"name": "A vehicle type with this name already exists."}
                )
        return attrs


class VehicleTypeSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleType
        fields = ["id", "name", "category", "icon_image_url"]


class VehicleDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleDocument
        fields = ["id", "document_type", "file_url", "expiry_date", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        document_type = attrs.get("document_type", getattr(self.instance, "document_type", None))
        expiry_date = attrs.get("expiry_date", getattr(self.instance, "expiry_date", None))
        if document_type in (VehicleDocumentType.INSURANCE, VehicleDocumentType.FITNESS) and not expiry_date:
            raise serializers.ValidationError(
                {"expiry_date": "Required for insurance and fitness documents."}
            )
        return attrs


class VehiclePhotoSerializer(serializers.ModelSerializer):
    url = MediaUrlField()

    class Meta:
        model = VehiclePhoto
        fields = ["id", "url"]
        read_only_fields = fields


class VehicleSerializer(serializers.ModelSerializer):
    owner_driver_id = serializers.UUIDField(read_only=True, allow_null=True)
    vehicle_type_id = serializers.PrimaryKeyRelatedField(
        source="vehicle_type", queryset=VehicleType.objects.none(), write_only=True
    )
    capacity_kg = serializers.DecimalField(max_digits=8, decimal_places=2, required=False)

    class Meta:
        model = Vehicle
        fields = [
            "id",
            "vehicle_type_id",
            "registration_number",
            "capacity_kg",
            "photo_url",
            "status",
            "current_driver_id",
            "owner_driver_id",
            "created_at",
            "updated_at",
        ]
        # current_driver_id is kept by driver duty start/end (drivers.services);
        # letting a client write it would corrupt "who has this vehicle".
        read_only_fields = ["id", "status", "current_driver_id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company_id = getattr(getattr(self.context.get("request"), "user", None), "company_id", None)
        if company_id is not None:
            self.fields["vehicle_type_id"].queryset = VehicleType.objects.filter(company_id=company_id)

    def validate_vehicle_type_id(self, value):
        if value.status != VehicleTypeStatus.ACTIVE:
            raise serializers.ValidationError("This vehicle type is not active.")
        return value

    def validate_registration_number(self, value):
        value = value.strip().upper()
        if not REGISTRATION_NUMBER_RE.match(value):
            raise serializers.ValidationError(
                "Must contain only uppercase letters, numbers, and hyphens."
            )
        return value

    def validate_capacity_kg(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be a positive number.")
        return value

    def validate(self, attrs):
        # Same reasoning as VehicleTypeSerializer.validate: the conditional
        # UniqueConstraint on (company_id, registration_number) isn't picked
        # up by DRF's automatic validators, so check it explicitly.
        request = self.context.get("request")
        registration_number = attrs.get(
            "registration_number", getattr(self.instance, "registration_number", None)
        )
        if request is not None and registration_number:
            qs = Vehicle.objects.filter(
                company_id=request.user.company_id, registration_number=registration_number
            )
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"registration_number": "A vehicle with this registration number already exists."}
                )
        return attrs

    def create(self, validated_data):
        if not validated_data.get("capacity_kg"):
            validated_data["capacity_kg"] = validated_data["vehicle_type"].default_capacity_kg
        return super().create(validated_data)


class VehicleListSerializer(serializers.ModelSerializer):
    vehicle_type = VehicleTypeSummarySerializer(read_only=True)
    # Driver-registered vehicles keep their pictures as stored paths; the company
    # gets absolute URLs that open anywhere.
    photo_url = MediaUrlField()
    owner_driver_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = Vehicle
        fields = [
            "id",
            "vehicle_type",
            "registration_number",
            "capacity_kg",
            "photo_url",
            "status",
            "current_driver_id",
            "owner_driver_id",
            "created_at",
            "updated_at",
        ]


class VehicleDetailSerializer(VehicleListSerializer):
    documents = VehicleDocumentSerializer(many=True, read_only=True)
    photos = VehiclePhotoSerializer(many=True, read_only=True)

    class Meta(VehicleListSerializer.Meta):
        fields = VehicleListSerializer.Meta.fields + ["documents", "photos"]
