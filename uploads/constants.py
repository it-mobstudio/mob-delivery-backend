from django.db import models


class UploadPurpose(models.TextChoices):
    VEHICLE_TYPE_ICON = "vehicle_type_icon", "Vehicle Type Icon"
    VEHICLE_PHOTO = "vehicle_photo", "Vehicle Photo"
    VEHICLE_DOCUMENT = "vehicle_document", "Vehicle Document"
