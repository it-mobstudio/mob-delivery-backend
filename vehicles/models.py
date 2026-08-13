from django.db import models

from core.models import BaseModel


class VehicleCategory(models.TextChoices):
    TWO_WHEELER = "two_wheeler", "2 Wheeler"
    THREE_WHEELER = "three_wheeler", "3 Wheeler"
    FOUR_WHEELER = "four_wheeler", "4 Wheeler"


class VehicleTypeStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class VehicleType(BaseModel):
    name = models.CharField(max_length=50)  # "Bike", "Auto", "Tempo", "Mini Van", "Truck"
    category = models.CharField(max_length=20, choices=VehicleCategory.choices)
    default_capacity_kg = models.DecimalField(max_digits=8, decimal_places=2)
    icon_image_url = models.URLField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=VehicleTypeStatus.choices, default=VehicleTypeStatus.ACTIVE)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "name"],
                condition=models.Q(is_deleted=False),
                name="unique_vehicle_type_name_per_company",
            )
        ]

    def __str__(self):
        return self.name


class VehicleStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    MAINTENANCE = "maintenance", "Maintenance"
    DISABLED = "disabled", "Disabled"


class Vehicle(BaseModel):
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.PROTECT, related_name="vehicles")
    registration_number = models.CharField(max_length=20)
    capacity_kg = models.DecimalField(max_digits=8, decimal_places=2)
    photo_url = models.URLField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=VehicleStatus.choices, default=VehicleStatus.ACTIVE)
    current_driver_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "registration_number"],
                condition=models.Q(is_deleted=False),
                name="unique_registration_per_company",
            )
        ]

    def __str__(self):
        return self.registration_number


class VehicleDocumentType(models.TextChoices):
    INSURANCE = "insurance", "Insurance"
    FITNESS = "fitness", "Fitness"
    RC = "rc", "RC"
    PURCHASE = "purchase", "Purchase"
    OTHER = "other", "Other"


class VehicleDocument(BaseModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=20, choices=VehicleDocumentType.choices)
    file_url = models.URLField()
    expiry_date = models.DateField(null=True, blank=True)  # null for purchase/other, required for insurance/fitness

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.vehicle.registration_number}"
