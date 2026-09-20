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


# uploads (folded into core — see core/uploads.py) -------------------------


class UploadPurpose(models.TextChoices):
    VEHICLE_TYPE_ICON = "vehicle_type_icon", "Vehicle Type Icon"
    VEHICLE_PHOTO = "vehicle_photo", "Vehicle Photo"
    VEHICLE_DOCUMENT = "vehicle_document", "Vehicle Document"
    DRIVER_DOCUMENT = "driver_document", "Driver Document"
