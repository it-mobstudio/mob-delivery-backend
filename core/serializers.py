from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .choices import UploadPurpose
from .uploads import absolute_media_url


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField(
        help_text="The file to store. Images (`jpg`, `jpeg`, `png`, `webp`) for every purpose; PDF is also accepted for `vehicle_document`, `driver_document` and `trip_invoice`. Maximum size is `MAX_UPLOAD_SIZE_MB` (10 MB by default). The content is checked — a renamed text file is rejected."
    )
    purpose = serializers.ChoiceField(
        choices=UploadPurpose.choices,
        help_text="What the file is for. It decides where the file is stored and which types are accepted.",
    )


class UploadResultSerializer(serializers.Serializer):
    url = serializers.URLField(
        help_text="Absolute URL of the stored file. Pass it, unchanged, to whichever request needs a file URL (`invoice_url`, an item's `image_url`, a vehicle's `photo_url`, ...)."
    )


class MessageSerializer(serializers.Serializer):
    message = serializers.CharField(help_text="A short human-readable confirmation.")


@extend_schema_field({"type": "string", "format": "uri", "nullable": True})
class MediaUrlField(serializers.ReadOnlyField):
    """A stored file URL, made absolute for the requesting client — see
    core.uploads.absolute_media_url. Blank/None come out as None so clients
    don't have to tell "" from "no file"."""

    def to_representation(self, value):
        return absolute_media_url(value, self.context.get("request")) or None
