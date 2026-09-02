from django.db import models


class UploadPurpose(models.TextChoices):
    VEHICLE_TYPE_ICON = "vehicle_type_icon", "Vehicle Type Icon"
    VEHICLE_PHOTO = "vehicle_photo", "Vehicle Photo"
    VEHICLE_DOCUMENT = "vehicle_document", "Vehicle Document"
    DRIVER_DOCUMENT = "driver_document", "Driver Document"
    TRIP_PHOTO = "trip_photo", "Trip Photo"
    SHIFT_PHOTO = "shift_photo", "Shift Photo"
    DAMAGE_PHOTO = "damage_photo", "Damage Photo"
