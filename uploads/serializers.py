from rest_framework import serializers

from .constants import UploadPurpose


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    purpose = serializers.ChoiceField(choices=UploadPurpose.choices)
