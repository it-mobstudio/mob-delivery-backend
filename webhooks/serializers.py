from rest_framework import serializers

from .models import WebhookDeliveryLog, WebhookEvent


class WebhookDeliveryLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDeliveryLog
        fields = ["id", "attempt_number", "response_status_code", "response_body", "error_message", "attempted_at"]
        read_only_fields = fields


class WebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEvent
        fields = [
            "id",
            "event_type",
            "order_ref",
            "payload",
            "status",
            "attempt_count",
            "next_attempt_at",
            "delivered_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WebhookEventDetailSerializer(WebhookEventSerializer):
    delivery_logs = WebhookDeliveryLogSerializer(many=True, read_only=True)

    class Meta(WebhookEventSerializer.Meta):
        fields = WebhookEventSerializer.Meta.fields + ["delivery_logs"]
        read_only_fields = fields
