"""Every TextChoices enum in the project, grouped by the domain that owns
it. Centralized (rather than one per model) so a choice set can be
referenced from another app without reaching into that app's models module
— e.g. trips.serializers validates against VehicleCategory without
importing drivers.models.
"""

from django.db import models

# accounts ------------------------------------------------------------------


class CompanyStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    INACTIVE = "inactive", "Inactive"


class AdminRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    STAFF = "staff", "Staff"


class ApiClientStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


# vehicles --------------------------------------------------------------


class VehicleCategory(models.TextChoices):
    TWO_WHEELER = "two_wheeler", "2 Wheeler"
    THREE_WHEELER = "three_wheeler", "3 Wheeler"
    FOUR_WHEELER = "four_wheeler", "4 Wheeler"


class VehicleTypeStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class VehicleStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    MAINTENANCE = "maintenance", "Maintenance"
    DISABLED = "disabled", "Disabled"


class VehicleDocumentType(models.TextChoices):
    INSURANCE = "insurance", "Insurance"
    FITNESS = "fitness", "Fitness"
    RC = "rc", "RC"
    PURCHASE = "purchase", "Purchase"
    OTHER = "other", "Other"


# drivers -------------------------------------------------------------------


class VerificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class DriverAccountStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    LOCKED_DL_EXPIRED = "locked_dl_expired", "Locked — DL Expired"
    DISABLED = "disabled", "Disabled"


class OnboardingStatus(models.TextChoices):
    """Where a driver is in getting approved — derived from what they've filled
    in and what the company has decided (Driver.onboarding_status), never stored,
    so it can't drift from the facts it summarises."""

    PROFILE_INCOMPLETE = "profile_incomplete", "Profile incomplete"
    DOCUMENTS_REQUIRED = "documents_required", "Documents required"
    UNDER_REVIEW = "under_review", "Under review"
    ACTION_REQUIRED = "action_required", "Action required"
    APPROVED = "approved", "Approved"


class WalletTransactionKind(models.TextChoices):
    TRIP_EARNING = "trip_earning", "Trip earning"
    BONUS = "bonus", "Bonus"
    PENALTY = "penalty", "Penalty"
    PAYOUT = "payout", "Payout"
    ADJUSTMENT = "adjustment", "Adjustment"


# trips -----------------------------------------------------------------


class TripStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    NO_DRIVER_AVAILABLE = "no_driver_available", "No Driver Available"
    ASSIGNED = "assigned", "Assigned"
    ARRIVED_AT_PICKUP = "arrived_at_pickup", "Arrived At Pickup"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class CancelledBy(models.TextChoices):
    COMPANY = "company", "Company"
    DRIVER = "driver", "Driver"
    SYSTEM = "system", "System"


class PaymentMode(models.TextChoices):
    PREPAID = "prepaid", "Prepaid"
    COD = "cod", "Cash on Delivery"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"


class PickupPhotoMode(models.TextChoices):
    """Which photos the driver must take at the pickup before starting the
    delivery (Trip.pickup_photo), or at the drop before finishing it
    (Trip.delivery_photo). Always taken with the camera, never picked from the
    gallery."""

    NONE = "none", "None"
    ORDER = "order", "One photo of the whole order"
    PER_ITEM = "per_item", "One photo of every item"


class ItemVerificationStatus(models.TextChoices):
    """What the driver has said about one line of a trip's item list when the
    company asked for verification (Trip.verify_items)."""

    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    NOT_DELIVERED = "not_delivered", "Not delivered"


# uploads (folded into core — see core/uploads.py) -------------------------


class UploadPurpose(models.TextChoices):
    VEHICLE_TYPE_ICON = "vehicle_type_icon", "Vehicle Type Icon"
    VEHICLE_PHOTO = "vehicle_photo", "Vehicle Photo"
    VEHICLE_DOCUMENT = "vehicle_document", "Vehicle Document"
    DRIVER_DOCUMENT = "driver_document", "Driver Document"
    DRIVER_PHOTO = "driver_photo", "Driver Photo"
    TRIP_INVOICE = "trip_invoice", "Trip Invoice"
    TRIP_ITEM_IMAGE = "trip_item_image", "Trip Item Image"
    DELIVERY_PROOF = "delivery_proof", "Delivery Proof Photo"
    PICKUP_PROOF = "pickup_proof", "Pickup Proof Photo"
