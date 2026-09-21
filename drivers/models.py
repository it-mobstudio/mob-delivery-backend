from datetime import date

from django.db import models

from core.choices import (
    DriverAccountStatus,
    OnboardingStatus,
    VehicleCategory,
    VehicleDocumentType,
    VehicleStatus,
    VehicleTypeStatus,
    VerificationStatus,
    WalletTransactionKind,
)
from core.models import BaseModel, TimeStampedUUIDModel


class Driver(BaseModel):
    # Blank for a driver who signed themselves up and hasn't filled in their
    # details yet (see DriverService.register); the company fills it in for
    # the drivers it creates.
    full_name = models.CharField(max_length=150, blank=True, default="")
    phone_number = models.CharField(max_length=20)  # login identifier — unique per company

    emergency_contact_name = models.CharField(max_length=150)
    emergency_contact_phone = models.CharField(max_length=20)

    # Onboarding details the driver enters themselves.
    email = models.EmailField(blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)
    address_line = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    pincode = models.CharField(max_length=10, blank=True, default="")
    profile_photo_url = models.URLField(max_length=500, blank=True, default="")

    # Where the company sends the driver's payouts (see drivers.wallet). Either
    # a UPI id or a bank account; both optional until the first payout.
    payout_upi_id = models.CharField(max_length=100, blank=True, default="")
    bank_account_holder = models.CharField(max_length=150, blank=True, default="")
    bank_account_number = models.CharField(max_length=20, blank=True, default="")
    bank_ifsc = models.CharField(max_length=11, blank=True, default="")

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

    @property
    def is_profile_complete(self):
        return bool(
            self.full_name.strip()
            and self.date_of_birth
            and self.emergency_contact_name.strip()
            and self.emergency_contact_phone.strip()
        )

    @property
    def onboarding_status(self):
        """Derived, not stored. The app forces the sign-up flow only for
        PROFILE_INCOMPLETE and DOCUMENTS_REQUIRED; everything after that is
        the driver waiting on (or fixing something for) the company.

        A driver the company already verified is APPROVED whatever their
        profile looks like — drivers created before self-service onboarding
        existed have no date of birth and must not be sent back through it.
        """
        if self.is_eligible_for_assignment:
            return OnboardingStatus.APPROVED
        if not self.is_profile_complete:
            return OnboardingStatus.PROFILE_INCOMPLETE

        kyc = self.kyc
        if VerificationStatus.REJECTED in (kyc.aadhar_status, kyc.dl_status, kyc.police_status):
            return OnboardingStatus.ACTION_REQUIRED

        # Aadhaar and licence are what the driver has to provide. The police
        # certificate is optional to upload (the company may verify it another
        # way), so it only matters once it has been rejected — handled above.
        # "Verified" needs no upload: the company may have verified them
        # without the driver ever attaching a scan.
        aadhar_needed = kyc.aadhar_status != VerificationStatus.VERIFIED and not kyc.aadhar_doc_url
        dl_needed = kyc.dl_status != VerificationStatus.VERIFIED and not kyc.dl_doc_url
        if aadhar_needed or dl_needed:
            return OnboardingStatus.DOCUMENTS_REQUIRED
        return OnboardingStatus.UNDER_REVIEW


class DriverKyc(TimeStampedUUIDModel):
    """Verification state for a Driver — split out of Driver itself so that
    model stays a thin profile/operational-state record. One row per
    driver, created alongside it (see DriverService.create); driver.kyc is
    assumed to always exist rather than being optional.
    """

    driver = models.OneToOneField(Driver, on_delete=models.CASCADE, related_name="kyc")

    # Aadhar. Only the last four digits of the number are kept: the scan is
    # what the company verifies against, and a full Aadhaar number is not
    # something to hold in a plain column.
    aadhar_number_last4 = models.CharField(max_length=4, blank=True, default="")
    aadhar_doc_url = models.URLField(max_length=500, null=True, blank=True)  # front
    aadhar_back_doc_url = models.URLField(max_length=500, null=True, blank=True)
    aadhar_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    aadhar_verified_by = models.UUIDField(null=True, blank=True)
    aadhar_verified_at = models.DateTimeField(null=True, blank=True)
    aadhar_rejection_note = models.TextField(null=True, blank=True)

    # Driving Licence
    dl_number = models.CharField(max_length=30, blank=True, default="")
    dl_doc_url = models.URLField(max_length=500, null=True, blank=True)  # front
    dl_back_doc_url = models.URLField(max_length=500, null=True, blank=True)
    dl_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    dl_expiry_date = models.DateField(null=True, blank=True)
    dl_allowed_categories = models.JSONField(default=list)  # matches VehicleType.category values
    dl_verified_by = models.UUIDField(null=True, blank=True)
    dl_verified_at = models.DateTimeField(null=True, blank=True)
    dl_rejection_note = models.TextField(null=True, blank=True)

    # Police Verification
    police_doc_url = models.URLField(max_length=500, null=True, blank=True)
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

    # Set when a driver registered the vehicle themselves (POST /driver/my-vehicles):
    # only that driver can take it on duty or manage it. The company's own fleet
    # leaves it empty and any eligible driver may use those.
    owner_driver = models.ForeignKey(
        "Driver", null=True, blank=True, on_delete=models.SET_NULL, related_name="own_vehicles"
    )

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


class VehiclePhoto(BaseModel):
    """One picture of a vehicle (front, side, number plate ...). `Vehicle.photo_url`
    keeps pointing at the first one so anything that shows "the" vehicle photo
    keeps working."""

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="photos")
    url = models.URLField(max_length=500)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Photo of {self.vehicle.registration_number}"


class VehicleDocument(BaseModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=20, choices=VehicleDocumentType.choices)
    file_url = models.URLField()
    expiry_date = models.DateField(null=True, blank=True)  # null for purchase/other, required for insurance/fitness

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.vehicle.registration_number}"


class WalletTransaction(BaseModel):
    """One line of a driver's wallet ledger. Append-only: a mistake is fixed
    with a further ADJUSTMENT row, never by editing or deleting history, so the
    driver's statement always adds up. `amount` is signed (credits positive,
    payouts and penalties negative) and `balance_after` is the running total at
    the moment it was written — see drivers.wallet.WalletService.
    """

    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="wallet_transactions")
    kind = models.CharField(max_length=20, choices=WalletTransactionKind.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True, default="")
    # A payout's bank/UPI reference (UTR), so a driver can match it to their
    # statement.
    reference = models.CharField(max_length=100, blank=True, default="")

    # Loose UUID references, same convention as Driver.current_vehicle_id: the
    # ledger must outlive the rows it points at, and drivers can't import trips
    # models at module level (trips already imports drivers).
    trip_id = models.UUIDField(null=True, blank=True)
    created_by = models.UUIDField(null=True, blank=True)  # the admin who recorded it, if manual

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["driver", "-created_at"])]
        constraints = [
            # A trip can only ever pay its driver once, even if completion is
            # retried or two requests race.
            models.UniqueConstraint(
                fields=["trip_id", "kind"],
                condition=models.Q(kind=WalletTransactionKind.TRIP_EARNING, trip_id__isnull=False),
                name="one_earning_per_trip",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} — {self.driver.full_name}"
