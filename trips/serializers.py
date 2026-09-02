from rest_framework import serializers

from drivers.serializers import DriverListSerializer
from vehicles.serializers import VehicleListSerializer

from .models import AddressChangeLog, Trip, TripPhoto, TripPhotoType, TripStop, TripVehicleHistory


class LatLngAddressSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=500)
    # Not required — a pincode-only payload is resolved server-side (see
    # trips.services._resolve_stop_coordinates) rather than rejected here.
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    pincode = serializers.CharField(max_length=10, required=False, allow_null=True, allow_blank=True)

    def validate_latitude(self, value):
        if value is not None and not (-90 <= value <= 90):
            raise serializers.ValidationError("Must be between -90 and 90.")
        return value

    def validate_longitude(self, value):
        if value is not None and not (-180 <= value <= 180):
            raise serializers.ValidationError("Must be between -180 and 180.")
        return value

    def validate(self, attrs):
        has_coords = attrs.get("latitude") is not None and attrs.get("longitude") is not None
        if not has_coords and not attrs.get("pincode"):
            raise serializers.ValidationError("Provide either latitude/longitude or a pincode.")
        return attrs


class IntakeOrderSerializer(serializers.Serializer):
    order_ref = serializers.CharField(max_length=100)
    parent_order_ref = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    pickup = LatLngAddressSerializer()
    delivery = LatLngAddressSerializer()
    weight_kg = serializers.DecimalField(max_digits=8, decimal_places=2)

    def validate_weight_kg(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be a positive number.")
        return value


class TripPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripPhoto
        fields = ["id", "trip_stop", "photo_type", "photo_url", "latitude", "longitude", "created_at"]
        read_only_fields = fields


class AddTripStopPhotoSerializer(serializers.Serializer):
    photo_type = serializers.ChoiceField(choices=TripPhotoType.choices)
    photo_url = serializers.URLField()
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)

    def validate_latitude(self, value):
        if value is not None and not (-90 <= value <= 90):
            raise serializers.ValidationError("Must be between -90 and 90.")
        return value

    def validate_longitude(self, value):
        if value is not None and not (-180 <= value <= 180):
            raise serializers.ValidationError("Must be between -180 and 180.")
        return value


class TripStopSerializer(serializers.ModelSerializer):
    photos = TripPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = TripStop
        fields = [
            "id",
            "stop_type",
            "sequence_no",
            "order_ref",
            "parent_order_ref",
            "address",
            "latitude",
            "longitude",
            "status",
            "proof_photo_url",
            "photos",
            "arrived_at",
            "completed_at",
            "weight_kg",
            "payment_mode",
            "payment_status",
            "location_mismatch",
            "location_mismatch_meters",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TripVehicleHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TripVehicleHistory
        fields = [
            "id",
            "previous_vehicle",
            "new_vehicle",
            "previous_driver",
            "new_driver",
            "reason",
            "created_at",
        ]
        read_only_fields = fields


class TripVehicleSummarySerializer(serializers.ModelSerializer):
    registration_number = serializers.CharField(read_only=True)

    class Meta:
        from vehicles.models import Vehicle

        model = Vehicle
        fields = ["id", "registration_number"]


class TripDriverSummarySerializer(serializers.ModelSerializer):
    class Meta:
        from drivers.models import Driver

        model = Driver
        fields = ["id", "full_name", "phone_number"]


class TripListSerializer(serializers.ModelSerializer):
    vehicle = TripVehicleSummarySerializer(read_only=True)
    driver = TripDriverSummarySerializer(read_only=True)

    class Meta:
        model = Trip
        fields = [
            "id",
            "vehicle",
            "driver",
            "status",
            "total_weight_kg",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TripDetailSerializer(TripListSerializer):
    stops = TripStopSerializer(many=True, read_only=True)
    vehicle_history = TripVehicleHistorySerializer(many=True, read_only=True)
    route = serializers.SerializerMethodField()

    class Meta(TripListSerializer.Meta):
        fields = TripListSerializer.Meta.fields + ["stops", "vehicle_history", "route"]
        read_only_fields = fields

    def get_route(self, trip):
        """The actual driving route (Google Maps), not a straight line
        between endpoints — earliest stop to latest by sequence_no, so the
        Admin Panel can draw it on the trip map. None if the trip has fewer
        than two stops or Google Maps isn't configured.
        """
        stops = list(trip.stops.all())  # already sequence_no-ordered (TripStop.Meta.ordering)
        if len(stops) < 2:
            return None

        from maps.services import fetch_route_polyline

        origin, destination = stops[0], stops[-1]
        return fetch_route_polyline(origin.latitude, origin.longitude, destination.latitude, destination.longitude)


class AssignmentCandidatesRequestSerializer(serializers.Serializer):
    trip_id = serializers.UUIDField(required=False)
    order_refs = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    # No default here — omitted means "use this tenant's configured
    # assignment_window_minutes" (see services.get_assignment_candidates),
    # not a hardcoded 30 baked into the request schema.
    within_minutes = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        if not attrs.get("trip_id") and not attrs.get("order_refs"):
            raise serializers.ValidationError("Provide either trip_id or order_refs.")
        return attrs


class AssignmentCandidateSerializer(serializers.Serializer):
    vehicle = VehicleListSerializer()
    drivers = DriverListSerializer(many=True)
    available_in_minutes = serializers.IntegerField()


class ExcludedAssignmentVehicleSerializer(serializers.Serializer):
    vehicle = VehicleListSerializer()
    reason = serializers.CharField()


class AssignVehicleSerializer(serializers.Serializer):
    vehicle_id = serializers.UUIDField()
    driver_id = serializers.UUIDField()


class ReassignVehicleSerializer(serializers.Serializer):
    new_vehicle_id = serializers.UUIDField()
    new_driver_id = serializers.UUIDField(required=False)
    reason = serializers.CharField(min_length=5)


class CompleteStopSerializer(serializers.Serializer):
    proof_photo_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)


class UpdateDeliveryAddressSerializer(serializers.Serializer):
    new_address = serializers.CharField(max_length=500)
    new_lat = serializers.DecimalField(max_digits=9, decimal_places=6)
    new_lng = serializers.DecimalField(max_digits=9, decimal_places=6)

    def validate_new_lat(self, value):
        if not (-90 <= value <= 90):
            raise serializers.ValidationError("Must be between -90 and 90.")
        return value

    def validate_new_lng(self, value):
        if not (-180 <= value <= 180):
            raise serializers.ValidationError("Must be between -180 and 180.")
        return value


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5)
