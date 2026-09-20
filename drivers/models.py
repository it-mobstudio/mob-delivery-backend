from datetime import date

from django.db import models

from core.choices import (
    DriverAccountStatus,
    VehicleCategory,
    VehicleDocumentType,
    VehicleStatus,
    VehicleTypeStatus,
    VerificationStatus,
)
from core.models import BaseModel, TimeStampedUUIDModel


class Driver(BaseModel):
    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20)  # login identifier — unique per company

    emergency_contact_name = models.CharField(max_length=150)
    emergency_contact_phone = models.CharField(max_length=20)

    account_status = models.CharField(
        max_length=30, choices=DriverAccountStatus.choices, default=DriverAccountStatus.ACTIVE
    )
    current_vehicle_id = models.UUIDField(null=True, blank=True)

    # Duty/location — set via driver/duty and driver/location; read by
    # trips.matching to find the nearest available driver for a new trip.
    is_online = models.BooleanField(default=False)
    last_known_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_known_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_location_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company_id", "phone_number"],
                condition=models.Q(is_deleted=False),
                name="unique_driver_phone_per_company",
            )
        ]

    def __str__(self):
        return self.full_name

    @property
    def is_eligible_for_assignment(self):
        kyc = self.kyc
        return (
            kyc.aadhar_status == VerificationStatus.VERIFIED
            and kyc.dl_status == VerificationStatus.VERIFIED
            and kyc.police_status == VerificationStatus.VERIFIED
            and self.account_status == DriverAccountStatus.ACTIVE
            and (kyc.dl_expiry_date is None or kyc.dl_expiry_date >= date.today())
        )


class DriverKyc(TimeStampedUUIDModel):
    """Verification state for a Driver — split out of Driver itself so that
    model stays a thin profile/operational-state record. One row per
    driver, created alongside it (see DriverService.create); driver.kyc is
    assumed to always exist rather than being optional.
    """

    driver = models.OneToOneField(Driver, on_delete=models.CASCADE, related_name="kyc")

    # Aadhar
    aadhar_doc_url = models.URLField(null=True, blank=True)
    aadhar_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    aadhar_verified_by = models.UUIDField(null=True, blank=True)
    aadhar_verified_at = models.DateTimeField(null=True, blank=True)
    aadhar_rejection_note = models.TextField(null=True, blank=True)

    # Driving Licence
    dl_doc_url = models.URLField(null=True, blank=True)
    dl_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    dl_expiry_date = models.DateField(null=True, blank=True)
    dl_allowed_categories = models.JSONField(default=list)  # matches VehicleType.category values
    dl_verified_by = models.UUIDField(null=True, blank=True)
    dl_verified_at = models.DateTimeField(null=True, blank=True)
    dl_rejection_note = models.TextField(null=True, blank=True)

    # Police Verification
    police_doc_url = models.URLField(null=True, blank=True)
    police_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    police_verified_by = models.UUIDField(null=True, blank=True)
    police_verified_at = models.DateTimeField(null=True, blank=True)
    police_rejection_note = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"KYC — {self.driver.full_name}"


class VehicleType(BaseModel):
    name = models.CharField(max_length=50)  # "Bike", "Auto", "Tempo", "Mini Van", "Truck"
    category = models.CharField(max_length=20, choices=VehicleCategory.choices)
    default_capacity_kg = models.DecimalField(max_digits=8, decimal_places=2)
    icon_image_url = models.URLField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=VehicleTypeStatus.choices, default=VehicleTypeStatus.ACTIVE)

    # Fare card — read by trips.pricing.PricingService.calculate_fare.
    # min_fare of 0 means "not configured yet"; trip creation refuses to
    # book against it rather than silently charging ₹0.
    base_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_km_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_min_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    min_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0)

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


class Vehicle(BaseModel):
    vehicle_type = models.ForeignKey(VehicleType, on_delete=models.PROTECT, related_name="vehicles")
    registration_number = models.CharField(max_length=20)
    capacity_kg = models.DecimalField(max_digits=8, decimal_places=2)
    photo_url = models.URLField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=VehicleStatus.choices, default=VehicleStatus.ACTIVE)

    # Loose UUID reference, not a FK, even though Driver and Vehicle now
    # live in the same app — deliberately decoupled so "who's driving this
    # vehicle right now" isn't a hard relational dependency either model's
    # queries need to join through (mirrors Driver.current_vehicle_id).
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


class VehicleDocument(BaseModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=20, choices=VehicleDocumentType.choices)
    file_url = models.URLField()
    expiry_date = models.DateField(null=True, blank=True)  # null for purchase/other, required for insurance/fitness

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.vehicle.registration_number}"
