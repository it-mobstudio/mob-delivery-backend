from django.db import models

from core.models import BaseModel


class VehicleCategory(models.TextChoices):
    TWO_WHEELER = "two_wheeler", "2 Wheeler"
    THREE_WHEELER = "three_wheeler", "3 Wheeler"
    FOUR_WHEELER = "four_wheeler", "4 Wheeler"


class VehicleTypeStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class DimensionUnit(models.TextChoices):
    FEET = "feet", "Feet"
    CM = "cm", "cm"
    INCHES = "inches", "Inches"


class VehicleType(BaseModel):
    name = models.CharField(max_length=50)  # "Bike", "Auto", "Tempo", "Mini Van", "Truck"
    category = models.CharField(max_length=20, choices=VehicleCategory.choices)
    default_capacity_kg = models.DecimalField(max_digits=8, decimal_places=2)
    icon_image_url = models.URLField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=VehicleTypeStatus.choices, default=VehicleTypeStatus.ACTIVE)

    # Cargo storage space — some vehicles hit their volume limit long before
    # their weight limit. All optional/independent of default_capacity_kg;
    # a vehicle type can still be created with just weight if dimensions
    # aren't known/relevant yet. Unit varies per row (feet for larger
    # vehicles, cm for smaller), hence a field rather than a hardcoded unit.
    storage_length = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    storage_width = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    storage_height = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    storage_unit = models.CharField(max_length=10, choices=DimensionUnit.choices, null=True, blank=True)

    # Informational only for now — no enforcement against real orders yet
    # (that needs actual category data flowing from Django first). See
    # trips.services._estimate_minutes_until_free for where the loading/
    # unloading minutes are a natural future input, deliberately not wired
    # in this pass.
    suitable_product_categories = models.JSONField(default=list, blank=True)
    default_loading_minutes = models.IntegerField(null=True, blank=True)
    default_unloading_minutes = models.IntegerField(null=True, blank=True)

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

    @property
    def storage_display(self):
        if not (self.storage_length and self.storage_width and self.storage_height):
            return None
        return f"{self.storage_length} × {self.storage_width} × {self.storage_height} {self.storage_unit}"


class VehicleStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    MAINTENANCE = "maintenance", "Maintenance"
    # Short-term unavailability (e.g. parked at a hub without a mapped
    # driver, temporarily pulled from rotation) — distinct from MAINTENANCE
    # (implies a workshop/repair process) and DISABLED (implies permanent
    # discontinuation).
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable", "Temporarily Unavailable"
    DISABLED = "disabled", "Disabled"


class VehicleOwnership(models.TextChoices):
    OWNED = "owned", "Owned"
    LEASED = "leased", "Leased"
    CONTRACTED = "contracted", "Contracted"


class Vehicle(BaseModel):
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.PROTECT, related_name="vehicles")
    registration_number = models.CharField(max_length=20)
    capacity_kg = models.DecimalField(max_digits=8, decimal_places=2)
    photo_url = models.URLField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=VehicleStatus.choices, default=VehicleStatus.ACTIVE)
    current_driver_id = models.UUIDField(null=True, blank=True)

    make = models.CharField(max_length=50, blank=True, default="")
    model = models.CharField(max_length=50, blank=True, default="")
    ownership = models.CharField(max_length=20, choices=VehicleOwnership.choices, blank=True, default="")

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


class VehicleDocumentExpiryAlert(BaseModel):
    """Fix 5 — created by vehicles.tasks.flag_expiring_vehicle_documents
    (daily Celery Beat) for a VehicleDocument approaching its expiry_date.
    Mirrors the acknowledged-based dedup pattern already used by
    TripAnomalyAlert/SosAlert elsewhere in this codebase.
    """

    document = models.ForeignKey(VehicleDocument, on_delete=models.CASCADE, related_name="expiry_alerts")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="document_expiry_alerts")
    expiry_date = models.DateField()
    detected_at = models.DateTimeField()
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-detected_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["document"],
                condition=models.Q(acknowledged=False),
                name="unique_open_expiry_alert_per_document",
            )
        ]

    def __str__(self):
        return f"{self.document_id} expires {self.expiry_date}"
