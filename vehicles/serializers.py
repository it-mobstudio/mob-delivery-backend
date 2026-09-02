import re

from rest_framework import serializers

from .models import (
    DimensionUnit,
    Vehicle,
    VehicleDocument,
    VehicleDocumentType,
    VehicleType,
    VehicleTypeStatus,
)

REGISTRATION_NUMBER_RE = re.compile(r"^[A-Z0-9-]+$")
STORAGE_DIMENSION_FIELDS = ["storage_length", "storage_width", "storage_height"]


class VehicleTypeSerializer(serializers.ModelSerializer):
    storage_display = serializers.CharField(read_only=True)

    class Meta:
        model = VehicleType
        fields = [
            "id",
            "name",
            "category",
            "default_capacity_kg",
            "icon_image_url",
            "status",
            "storage_length",
            "storage_width",
            "storage_height",
            "storage_unit",
            "storage_display",
            "suitable_product_categories",
            "default_loading_minutes",
            "default_unloading_minutes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_default_capacity_kg(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be a positive number.")
        return value

    def _validate_positive(self, field, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError({field: "Must be a positive number."})

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

        # Storage dimensions — a partial set isn't useful, so length/width/
        # height must resolve to all-or-nothing. Checked against the
        # *effective* value (this request, falling back to what's already
        # on the instance) rather than only what's in this exact payload —
        # so a PATCH correcting just one dimension of an already-complete
        # set doesn't have to resend the other two.
        effective = {
            field: attrs[field] if field in attrs else getattr(self.instance, field, None) if self.instance else None
            for field in STORAGE_DIMENSION_FIELDS
        }
        if any(field in attrs for field in STORAGE_DIMENSION_FIELDS):
            missing = [field for field in STORAGE_DIMENSION_FIELDS if not effective[field]]
            if missing:
                raise serializers.ValidationError(
                    {
                        field: "storage_length, storage_width, and storage_height must be provided together."
                        for field in missing
                    }
                )
            for field in STORAGE_DIMENSION_FIELDS:
                self._validate_positive(field, effective[field])
            if not attrs.get("storage_unit") and not (self.instance and self.instance.storage_unit):
                attrs["storage_unit"] = DimensionUnit.CM
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


class VehicleSerializer(serializers.ModelSerializer):
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
            "make",
            "model",
            "ownership",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and getattr(request, "user", None) is not None:
            self.fields["vehicle_type_id"].queryset = VehicleType.objects.filter(
                company_id=request.user.company_id
            )

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
            "make",
            "model",
            "ownership",
            "created_at",
            "updated_at",
        ]


class VehicleDetailSerializer(VehicleListSerializer):
    documents = VehicleDocumentSerializer(many=True, read_only=True)

    class Meta(VehicleListSerializer.Meta):
        fields = VehicleListSerializer.Meta.fields + ["documents"]


class DriverVehicleTypeSerializer(serializers.ModelSerializer):
    """Nested vehicle-type detail for DriverVehicleSerializer — trimmed to
    what a driver needs to know about the *kind* of vehicle they're driving
    (name/category, capacity, cargo dimensions, icon), not the admin-only
    fields (status, suitable_product_categories, loading/unloading minutes).
    """

    storage_display = serializers.CharField(read_only=True)

    class Meta:
        model = VehicleType
        fields = [
            "name",
            "category",
            "default_capacity_kg",
            "storage_length",
            "storage_width",
            "storage_height",
            "storage_unit",
            "storage_display",
            "icon_image_url",
        ]


class DriverVehicleSerializer(serializers.ModelSerializer):
    """GET /driver/vehicle — the authenticated driver's own currently-
    assigned vehicle, joined with its vehicle type. Read-only, driver-facing
    subset of VehicleDetailSerializer (no documents/current_driver_id).
    """

    vehicle_type = DriverVehicleTypeSerializer(read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            "id",
            "registration_number",
            "photo_url",
            "status",
            "vehicle_type",
        ]
