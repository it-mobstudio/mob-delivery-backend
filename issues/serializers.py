from rest_framework import serializers

from .models import IssueSeverity, IssueType, TripIssue


class CreateIssueSerializer(serializers.Serializer):
    issue_type = serializers.ChoiceField(choices=IssueType.choices)
    trip_stop_id = serializers.UUIDField(required=False, allow_null=True)
    severity = serializers.ChoiceField(choices=IssueSeverity.choices, required=False)
    note = serializers.CharField(min_length=5)
    photo_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)
    # Only used (and required) when issue_type == traffic_penalty — see
    # services.create_issue.
    penalty_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, min_value=0)
    penalty_challan_number = serializers.CharField(required=False, allow_blank=True, max_length=50)

    def validate(self, attrs):
        if attrs["issue_type"] == IssueType.TRAFFIC_PENALTY and attrs.get("penalty_amount") is None:
            raise serializers.ValidationError(
                {"penalty_amount": "Required when issue_type is 'traffic_penalty'."}
            )
        return attrs


class IssueListSerializer(serializers.ModelSerializer):
    driver_name = serializers.SerializerMethodField()

    class Meta:
        model = TripIssue
        fields = [
            "id",
            "trip",
            "driver_name",
            "issue_type",
            "severity",
            "status",
            "note",
            "photo_url",
            "resolution_note",
            "resolved_by",
            "resolved_at",
            "penalty_amount",
            "penalty_challan_number",
            "penalty_paid",
            "penalty_paid_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_driver_name(self, obj):
        return obj.trip.driver.full_name if obj.trip.driver_id else None


class IssueDetailSerializer(serializers.ModelSerializer):
    trip = serializers.SerializerMethodField()
    trip_stop = serializers.SerializerMethodField()

    class Meta:
        model = TripIssue
        fields = [
            "id",
            "trip",
            "trip_stop",
            "issue_type",
            "severity",
            "status",
            "note",
            "photo_url",
            "resolution_note",
            "resolved_by",
            "resolved_at",
            "penalty_amount",
            "penalty_challan_number",
            "penalty_paid",
            "penalty_paid_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_trip(self, obj):
        trip = obj.trip
        return {
            "id": trip.id,
            "status": trip.status,
            "vehicle": (
                {"id": trip.vehicle_id, "registration_number": trip.vehicle.registration_number}
                if trip.vehicle_id
                else None
            ),
            "driver": {"id": trip.driver_id, "full_name": trip.driver.full_name} if trip.driver_id else None,
        }

    def get_trip_stop(self, obj):
        if obj.trip_stop_id is None:
            return None
        return {"id": obj.trip_stop_id, "address": obj.trip_stop.address}


class ResolveIssueSerializer(serializers.Serializer):
    resolution_note = serializers.CharField(min_length=5)
