from rest_framework import serializers

from .models import DriverShift, PauseReason, TripAnomalyAlert, TripPause, VehicleStartPoint


class LocationPingSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    speed_kmph = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)

    def validate_latitude(self, value):
        if not (-90 <= value <= 90):
            raise serializers.ValidationError("Must be between -90 and 90.")
        return value

    def validate_longitude(self, value):
        if not (-180 <= value <= 180):
            raise serializers.ValidationError("Must be between -180 and 180.")
        return value


class PauseSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=PauseReason.choices)


class TripPauseSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripPause
        fields = ["id", "trip", "reason", "started_at", "ended_at", "created_at"]
        read_only_fields = fields


class TimeSummarySerializer(serializers.Serializer):
    moving_minutes = serializers.IntegerField()
    paused_minutes = serializers.IntegerField()


class StartShiftSerializer(serializers.Serializer):
    driver_id = serializers.UUIDField()
    vehicle_id = serializers.UUIDField()
    start_odometer = serializers.DecimalField(max_digits=10, decimal_places=2)
    start_point_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_start_odometer(self, value):
        if value < 0:
            raise serializers.ValidationError("Must be >= 0.")
        return value


class DriverShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = DriverShift
        fields = [
            "id",
            "driver",
            "vehicle",
            "shift_date",
            "start_odometer",
            "end_odometer",
            "started_at",
            "ended_at",
            "status",
            "total_km",
            "total_working_minutes",
            "cleanliness_photo_url",
            "charging_plugged_photo_url",
            "fixed_start_latitude",
            "fixed_start_longitude",
            "fixed_start_label",
        ]
        read_only_fields = fields


class VehicleStartPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleStartPoint
        fields = ["id", "label", "latitude", "longitude", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_latitude(self, value):
        if not (-90 <= value <= 90):
            raise serializers.ValidationError("Must be between -90 and 90.")
        return value

    def validate_longitude(self, value):
        if not (-180 <= value <= 180):
            raise serializers.ValidationError("Must be between -180 and 180.")
        return value


class EndShiftSerializer(serializers.Serializer):
    end_odometer = serializers.DecimalField(max_digits=10, decimal_places=2)
    # Deliberately not required=True: a missing photo must surface as the
    # specific CLEANLINESS_PHOTO_REQUIRED / CHARGING_PHOTO_REQUIRED
    # DomainError from the service layer (point 24), not a generic DRF
    # "this field is required" 400.
    cleanliness_photo_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)
    charging_plugged_photo_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)

    def validate_end_odometer(self, value):
        if value < 0:
            raise serializers.ValidationError("Must be >= 0.")
        return value


class EndShiftResultSerializer(serializers.Serializer):
    total_km = serializers.DecimalField(max_digits=8, decimal_places=2)
    total_working_minutes = serializers.IntegerField()
    trips_completed_today = serializers.IntegerField()


class TripAnomalyAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripAnomalyAlert
        fields = [
            "id",
            "trip",
            "vehicle_id",
            "alert_type",
            "details",
            "detected_at",
            "acknowledged",
            "created_at",
        ]
        read_only_fields = fields
