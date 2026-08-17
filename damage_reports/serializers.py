from rest_framework import serializers

from .models import DamageSeverity, VehicleDamageReport


class CreateDamageReportSerializer(serializers.Serializer):
    description = serializers.CharField(min_length=5)
    photo_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)
    severity = serializers.ChoiceField(choices=DamageSeverity.choices, required=False)


class DamageReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleDamageReport
        fields = [
            "id",
            "vehicle",
            "reported_by_id",
            "reporter_type",
            "description",
            "photo_url",
            "severity",
            "status",
            "resolution_note",
            "resolved_by",
            "resolved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DamageReportListSerializer(serializers.ModelSerializer):
    vehicle_registration_number = serializers.CharField(source="vehicle.registration_number", read_only=True)

    class Meta:
        model = VehicleDamageReport
        fields = [
            "id",
            "vehicle",
            "vehicle_registration_number",
            "reported_by_id",
            "reporter_type",
            "description",
            "photo_url",
            "severity",
            "status",
            "resolution_note",
            "resolved_by",
            "resolved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResolveDamageReportSerializer(serializers.Serializer):
    resolution_note = serializers.CharField(min_length=5)
