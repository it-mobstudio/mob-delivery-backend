from rest_framework import serializers

from .models import DevicePlatform, DriverDevice


class RegisterDeviceSerializer(serializers.Serializer):
    fcm_token = serializers.CharField(max_length=255)
    platform = serializers.ChoiceField(choices=DevicePlatform.choices)


class DriverDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DriverDevice
        fields = ["id", "driver", "fcm_token", "platform", "last_active_at", "created_at", "updated_at"]
        read_only_fields = fields
