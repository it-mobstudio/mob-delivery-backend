import random

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Company
from core.choices import (
    CompanyStatus,
    DriverAccountStatus,
    UploadPurpose,
    VehicleStatus,
    VerificationStatus,
)
from core.constants import OTP_LENGTH, OTP_THROTTLE_SECONDS, OTP_TTL_SECONDS
from core.exceptions import DomainError
from core.uploads import UploadService

from .models import Driver, DriverKyc, Vehicle
from .sms import get_sms_provider
from .wallet import WalletService

# Once the company has verified the Aadhaar, these can't be edited any more:
# they're what the verified ID says, and changing them would quietly
# invalidate the verification.
PROFILE_FIELDS_LOCKED_BY_KYC = ("full_name", "date_of_birth")


class DriverService:
    """Profile, duty/location, and account-lifecycle operations for
    Driver. KYC decisions live on DriverKycService below — a Driver's
    verification state is a distinct concern from the driver record itself
    (see drivers.models.DriverKyc).
    """

    @staticmethod
    def _otp_cache_key(phone_number):
        return f"driver_otp:{phone_number}"

    @staticmethod
    def _otp_throttle_key(phone_number):
        return f"driver_otp_throttle:{phone_number}"

    @staticmethod
    def signup_company():
        """The company that self-registered drivers join, or None when sign-up
        is closed (see DRIVER_SIGNUP_COMPANY_ID in settings)."""
        companies = Company.objects.filter(status=CompanyStatus.ACTIVE)
        configured = (settings.DRIVER_SIGNUP_COMPANY_ID or "").strip()
        if configured:
            try:
                return companies.filter(pk=configured).first()
            except (DjangoValidationError, ValueError):  # not a uuid
                return None
        if settings.DEBUG:
            found = list(companies[:2])
            return found[0] if len(found) == 1 else None
        return None

    @staticmethod
    def find_driver(phone_number):
        """The driver registered under this number, or None. Raises if the
        number belongs to someone who isn't allowed to sign in.

        Looked up by phone_number alone: the otp/request and otp/verify
        endpoints are unauthenticated, so there's no company context to
        scope by yet (phone_number is only unique *within* a company — see
        Driver.Meta.constraints). Two companies sharing a driver phone
        number is an edge case this pass doesn't attempt to disambiguate.
        """
        try:
            driver = Driver.objects.get(phone_number=phone_number)
        except Driver.DoesNotExist:
            return None
        except Driver.MultipleObjectsReturned:
            raise DomainError(
                "DRIVER_PHONE_AMBIGUOUS",
                "This phone number matches drivers in more than one company.",
                status_code=409,
            )

        if driver.account_status == DriverAccountStatus.LOCKED_DL_EXPIRED:
            raise DomainError(
                "ACCOUNT_LOCKED",
                "This driver's account is locked because their driving licence has expired.",
                status_code=403,
            )
        if driver.account_status == DriverAccountStatus.DISABLED:
            raise DomainError("ACCOUNT_DISABLED", "This driver's account has been disabled.", status_code=403)

        return driver

    @classmethod
    def get_active_driver_or_error(cls, phone_number):
        driver = cls.find_driver(phone_number)
        if driver is None:
            raise DomainError("DRIVER_NOT_FOUND", "No driver found with this phone number.", status_code=404)
        return driver

    @classmethod
    def _require_known_or_signup(cls, phone_number):
        """Returns the existing driver, or None for a number that may sign up.
        Raises DRIVER_NOT_FOUND for an unknown number when sign-up is closed."""
        driver = cls.find_driver(phone_number)
        if driver is None and cls.signup_company() is None:
            raise DomainError(
                "DRIVER_NOT_FOUND",
                "No driver found with this phone number. Ask your company to register you.",
                status_code=404,
            )
        return driver

    @classmethod
    def request_otp(cls, phone_number):
        # A number nobody has registered gets an OTP too (when sign-up is open),
        # and the response is identical either way — so this endpoint doesn't
        # reveal who's already a driver.
        cls._require_known_or_signup(phone_number)

        # if not cache.add(cls._otp_throttle_key(phone_number), 1, timeout=OTP_THROTTLE_SECONDS):
        #     raise DomainError(
        #         "OTP_ALREADY_REQUESTED",
        #         "An OTP was already requested for this number recently. Please wait before retrying.",
        #         status_code=429,
        #     )

        otp = f"{random.randint(0, 10**OTP_LENGTH - 1):0{OTP_LENGTH}d}"
        cache.set(cls._otp_cache_key(phone_number), otp, timeout=OTP_TTL_SECONDS)

        get_sms_provider().send_otp(phone_number, otp)

        return otp

    @classmethod
    def verify_otp(cls, phone_number, otp):
        # Re-checks driver existence/account_status, not just OTP validity —
        # a DL could expire in the few minutes between request and verify.
        driver = cls._require_known_or_signup(phone_number)

        cache_key = cls._otp_cache_key(phone_number)
        stored_otp = cache.get(cache_key)
        if stored_otp is None or stored_otp != otp:
            raise DomainError("INVALID_OTP", "The OTP is invalid or has expired.", status_code=400)

        cache.delete(cache_key)
        if driver is None:
            driver = cls.register(cls.signup_company(), phone_number)
        return driver

    @staticmethod
    def register(company, phone_number):
        """Creates the account for a phone number that just proved it owns
        itself with an OTP. It starts empty — no name, no documents — and
        can't take trips until the driver fills in their details and the
        company verifies their documents (Driver.onboarding_status)."""
        with transaction.atomic():
            driver, created = Driver.objects.get_or_create(company=company, phone_number=phone_number)
            if created:
                DriverKyc.objects.create(driver=driver)
        return driver

    @classmethod
    def update_profile(cls, driver, **fields):
        if driver.kyc.aadhar_status == VerificationStatus.VERIFIED:
            changed = [
                name for name in PROFILE_FIELDS_LOCKED_BY_KYC
                if name in fields and fields[name] != getattr(driver, name)
            ]
            if changed:
                raise DomainError(
                    "PROFILE_LOCKED",
                    "Your name and date of birth can't be changed after your ID is verified. Contact support.",
                    status_code=409,
                )
        for name, value in fields.items():
            setattr(driver, name, value)
        driver.save(update_fields=[*fields, "updated_at"])
        return driver

    @classmethod
    def delete_account(cls, driver):
        """Self-service account deletion (an app-store requirement for any app
        that lets people sign themselves up). Refused while it would strand
        something: a trip in flight, or money the company still owes them."""
        if cls.has_active_trip(driver):
            raise DomainError(
                "DRIVER_HAS_ACTIVE_TRIP", "Finish or cancel your active trip before deleting your account.", status_code=409
            )
        balance = WalletService.balance(driver)
        if balance > 0:
            raise DomainError(
                "WALLET_BALANCE_PENDING",
                f"You still have ₹{balance:.2f} in your wallet. Ask your company to pay it out, then delete your account.",
                status_code=409,
            )
        cls.disable(driver)

    @staticmethod
    def create(company, **fields):
        """Creates a Driver and its (initially all-pending) DriverKyc row
        together — DriverKyc is assumed to always exist once a Driver does
        (see DriverKyc's docstring)."""
        driver = Driver.objects.create(company=company, **fields)
        DriverKyc.objects.create(driver=driver)
        return driver

    @staticmethod
    def has_active_trip(driver):
        # Local import: trips depends on drivers (Trip.driver -> Driver), so
        # importing it at module level here would be circular.
        from trips.models import ACTIVE_TRIP_STATUSES, Trip

        return Trip.objects.filter(driver=driver, status__in=ACTIVE_TRIP_STATUSES).exists()

    @classmethod
    def disable(cls, driver):
        if cls.has_active_trip(driver):
            raise DomainError(
                "DRIVER_HAS_ACTIVE_TRIP", "This driver has an active trip and cannot be disabled.", status_code=409
            )
        driver.account_status = DriverAccountStatus.DISABLED
        driver.is_online = False
        driver.save(update_fields=["account_status", "is_online"])
        driver.soft_delete()

    @staticmethod
    def available_vehicles(driver):
        """Vehicles this driver may go on duty with: active, in their
        company, of a category their driving licence is verified for, and
        not currently being driven by some *other* driver who's on duty.
        (`Vehicle.current_driver_id` alone can't tell "in use" from "was
        used earlier" — go_offline deliberately leaves it set — so an
        on-duty check against Driver is what actually decides that.)
        """
        allowed_categories = driver.kyc.dl_allowed_categories or []
        taken_vehicle_ids = (
            Driver.objects.filter(company_id=driver.company_id, is_online=True, current_vehicle_id__isnull=False)
            .exclude(pk=driver.pk)
            .values_list("current_vehicle_id", flat=True)
        )
        return (
            Vehicle.objects.select_related("vehicle_type")
            .filter(
                # The company's own fleet, or a vehicle this driver registered —
                # never one that belongs to another driver.
                Q(owner_driver__isnull=True) | Q(owner_driver=driver),
                company_id=driver.company_id,
                status=VehicleStatus.ACTIVE,
                vehicle_type__category__in=allowed_categories,
            )
            .exclude(pk__in=taken_vehicle_ids)
            .order_by("registration_number")
        )

    @classmethod
    def go_online(cls, driver, vehicle, lat=None, lng=None):
        if not driver.is_eligible_for_assignment:
            raise DomainError(
                "DRIVER_NOT_ELIGIBLE",
                "This driver's KYC is incomplete or their account is not active.",
                status_code=403,
            )
        if vehicle.vehicle_type.category not in (driver.kyc.dl_allowed_categories or []):
            raise DomainError(
                "VEHICLE_CATEGORY_NOT_ALLOWED",
                "Your driving licence is not verified for this type of vehicle.",
                status_code=403,
            )
        if not cls.available_vehicles(driver).filter(pk=vehicle.pk).exists():
            raise DomainError("VEHICLE_IN_USE", "This vehicle is being used by another driver.", status_code=409)

        # trips.matching only considers drivers with a reported location, so
        # taking the first fix here (rather than waiting for the app's first
        # periodic ping) means the driver is assignable the moment they go
        # on duty — and never matched on a location left over from their
        # previous shift.
        update_fields = ["current_vehicle_id", "is_online"]
        if lat is not None and lng is not None:
            driver.last_known_lat = lat
            driver.last_known_lng = lng
            driver.last_location_at = timezone.now()
            update_fields += ["last_known_lat", "last_known_lng", "last_location_at"]

        previous_vehicle_id = driver.current_vehicle_id
        if previous_vehicle_id and previous_vehicle_id != vehicle.id:
            # A trip is bound to the vehicle it was assigned on (Trip.vehicle),
            # so swapping vehicles underneath one would leave that record
            # pointing at a vehicle the driver is no longer in.
            if cls.has_active_trip(driver):
                raise DomainError(
                    "DRIVER_HAS_ACTIVE_TRIP",
                    "Finish or cancel your active trip before switching vehicles.",
                    status_code=409,
                )
            Vehicle.objects.filter(pk=previous_vehicle_id, current_driver_id=driver.id).update(current_driver_id=None)

        driver.current_vehicle_id = vehicle.id
        driver.is_online = True
        driver.save(update_fields=update_fields)

        # Loose UUID reference by design (see Vehicle.current_driver_id) —
        # deliberately not a FK even though both models now live in this
        # same app; see the comment on Vehicle.current_driver_id.
        vehicle.current_driver_id = driver.id
        vehicle.save(update_fields=["current_driver_id"])

        return driver

    @classmethod
    def go_offline(cls, driver):
        if cls.has_active_trip(driver):
            raise DomainError(
                "DRIVER_HAS_ACTIVE_TRIP", "This driver has an active trip and cannot go offline.", status_code=409
            )
        driver.is_online = False
        driver.save(update_fields=["is_online"])
        return driver

    @staticmethod
    def update_location(driver, lat, lng):
        driver.last_known_lat = lat
        driver.last_known_lng = lng
        driver.last_location_at = timezone.now()
        driver.save(update_fields=["last_known_lat", "last_known_lng", "last_location_at"])
        return driver

    @staticmethod
    def lock_expired_licenses():
        """Locks any active driver whose DL has expired. Called daily by
        drivers.tasks.lock_expired_driver_licenses (see
        CELERY_BEAT_SCHEDULE). The unlock half lives in
        DriverKycService.verify_dl, triggered by successful DL
        re-verification.
        """
        return Driver.objects.filter(
            kyc__dl_expiry_date__lt=timezone.localdate(), account_status=DriverAccountStatus.ACTIVE
        ).update(account_status=DriverAccountStatus.LOCKED_DL_EXPIRED)


class DriverKycService:
    """What a driver submits for verification (submit_*) and what the company
    decides about it (verify_*), both against the driver's DriverKyc row."""

    # -- what the driver submits -------------------------------------------

    @staticmethod
    def store_file(file, driver, purpose=UploadPurpose.DRIVER_DOCUMENT):
        """Saves an uploaded file under the driver's company and returns its
        URL, turning a bad file into an error the app can show."""
        try:
            return UploadService.store(file, purpose, driver.company_id)
        except DjangoValidationError as exc:
            raise DomainError("INVALID_UPLOAD", "; ".join(exc.messages), status_code=400)

    @staticmethod
    def _require_editable(status, label):
        if status == VerificationStatus.VERIFIED:
            raise DomainError(
                "KYC_ALREADY_VERIFIED",
                f"Your {label} is already verified. Contact support if it needs to change.",
                status_code=409,
            )

    @staticmethod
    def _back_to_pending(kyc, part):
        """A new submission starts the review over: whatever decision (and
        rejection note) the old one got no longer applies."""
        setattr(kyc, f"{part}_status", VerificationStatus.PENDING)
        setattr(kyc, f"{part}_verified_by", None)
        setattr(kyc, f"{part}_verified_at", None)
        setattr(kyc, f"{part}_rejection_note", None)

    @classmethod
    def submit_aadhar(cls, driver, number, front, back):
        kyc = driver.kyc
        cls._require_editable(kyc.aadhar_status, "Aadhaar")
        kyc.aadhar_doc_url = cls.store_file(front, driver)
        kyc.aadhar_back_doc_url = cls.store_file(back, driver)
        kyc.aadhar_number_last4 = number[-4:]
        cls._back_to_pending(kyc, "aadhar")
        kyc.save()
        return driver

    @classmethod
    def submit_dl(cls, driver, number, expiry_date, front, back=None):
        kyc = driver.kyc
        cls._require_editable(kyc.dl_status, "driving licence")
        kyc.dl_doc_url = cls.store_file(front, driver)
        kyc.dl_back_doc_url = cls.store_file(back, driver) if back is not None else None
        kyc.dl_number = number
        # The driver's own claim; the company confirms or corrects it when it
        # verifies the licence (verify_dl), which is what eligibility rests on.
        kyc.dl_expiry_date = expiry_date
        cls._back_to_pending(kyc, "dl")
        kyc.save()
        return driver

    @classmethod
    def submit_police(cls, driver, document):
        kyc = driver.kyc
        cls._require_editable(kyc.police_status, "police verification")
        kyc.police_doc_url = cls.store_file(document, driver)
        cls._back_to_pending(kyc, "police")
        kyc.save()
        return driver

    @classmethod
    def set_profile_photo(cls, driver, photo):
        driver.profile_photo_url = cls.store_file(photo, driver, UploadPurpose.DRIVER_PHOTO)
        driver.save(update_fields=["profile_photo_url", "updated_at"])
        return driver

    # -- what the company decides ------------------------------------------

    @staticmethod
    def verify_aadhar(driver, status, admin_id, note=None):
        kyc = driver.kyc
        kyc.aadhar_status = status
        kyc.aadhar_verified_by = admin_id
        kyc.aadhar_verified_at = timezone.now()
        kyc.aadhar_rejection_note = note if status == VerificationStatus.REJECTED else None
        kyc.save(
            update_fields=["aadhar_status", "aadhar_verified_by", "aadhar_verified_at", "aadhar_rejection_note"]
        )
        return driver

    @staticmethod
    def verify_police(driver, status, admin_id, note=None):
        kyc = driver.kyc
        kyc.police_status = status
        kyc.police_verified_by = admin_id
        kyc.police_verified_at = timezone.now()
        kyc.police_rejection_note = note if status == VerificationStatus.REJECTED else None
        kyc.save(
            update_fields=["police_status", "police_verified_by", "police_verified_at", "police_rejection_note"]
        )
        return driver

    @staticmethod
    def verify_dl(driver, status, admin_id, note=None, expiry_date=None, allowed_categories=None):
        kyc = driver.kyc
        kyc.dl_status = status
        kyc.dl_verified_by = admin_id
        kyc.dl_verified_at = timezone.now()
        kyc.dl_rejection_note = note if status == VerificationStatus.REJECTED else None

        update_fields = ["dl_status", "dl_verified_by", "dl_verified_at", "dl_rejection_note"]

        if status == VerificationStatus.VERIFIED:
            kyc.dl_expiry_date = expiry_date
            kyc.dl_allowed_categories = allowed_categories
            update_fields += ["dl_expiry_date", "dl_allowed_categories"]

        kyc.save(update_fields=update_fields)

        # The only place account_status unlocks from locked_dl_expired —
        # tied specifically to a successful DL re-verification with a
        # future expiry date, per the module spec (no separate generic
        # unlock endpoint).
        if (
            status == VerificationStatus.VERIFIED
            and driver.account_status == DriverAccountStatus.LOCKED_DL_EXPIRED
            and expiry_date >= timezone.localdate()
        ):
            driver.account_status = DriverAccountStatus.ACTIVE
            driver.save(update_fields=["account_status"])

        return driver
