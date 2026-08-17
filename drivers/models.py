from datetime import date

from django.db import models

from core.models import BaseModel


class VerificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class DriverAccountStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    LOCKED_DL_EXPIRED = "locked_dl_expired", "Locked — DL Expired"
    DISABLED = "disabled", "Disabled"


class Driver(BaseModel):
    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20)  # login identifier — unique per company

    emergency_contact_name = models.CharField(max_length=150)
    emergency_contact_phone = models.CharField(max_length=20)

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

    account_status = models.CharField(
        max_length=30, choices=DriverAccountStatus.choices, default=DriverAccountStatus.ACTIVE, db_index=True
    )
    current_vehicle_id = models.UUIDField(null=True, blank=True)

    # Duck-typed to satisfy DRF's IsAuthenticated (and simplejwt), same as
    # ApiClient — Driver isn't a Django auth user model, so without this any
    # endpoint using the stock IsAuthenticated permission (rather than the
    # custom IsDriverUser) raises AttributeError for a Driver principal.
    is_authenticated = True
    is_anonymous = False

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
        return (
            self.aadhar_status == VerificationStatus.VERIFIED
            and self.dl_status == VerificationStatus.VERIFIED
            and self.police_status == VerificationStatus.VERIFIED
            and self.account_status == DriverAccountStatus.ACTIVE
            and (self.dl_expiry_date is None or self.dl_expiry_date >= date.today())
        )
