from rest_framework import serializers

from .models import SosAlert


class TriggerSosSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    trip_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_latitude(self, value):
        if not (-90 <= value <= 90):
            raise serializers.ValidationError("Must be between -90 and 90.")
        return value

    def validate_longitude(self, value):
        if not (-180 <= value <= 180):
            raise serializers.ValidationError("Must be between -180 and 180.")
        return value


class SosAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = SosAlert
        fields = [
            "id",
            "driver",
            "trip",
            "latitude",
            "longitude",
            "triggered_at",
            "status",
            "acknowledged_by",
            "acknowledged_at",
            "resolved_by",
            "resolved_at",
            "resolution_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResolveSosSerializer(serializers.Serializer):
    resolution_note = serializers.CharField()
