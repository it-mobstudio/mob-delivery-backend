from rest_framework import serializers

from .models import TenantSetting


class TenantSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantSetting
        fields = ["id", "key", "value", "created_at", "updated_at"]
        read_only_fields = fields


class UpdateTenantSettingSerializer(serializers.Serializer):
    value = serializers.CharField()
