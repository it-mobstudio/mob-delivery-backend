from rest_framework import serializers

from .choices import UploadPurpose


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    purpose = serializers.ChoiceField(choices=UploadPurpose.choices)
