import random

from django.core.cache import cache
from django.utils import timezone

from core.choices import DriverAccountStatus, VerificationStatus
from core.constants import OTP_THROTTLE_SECONDS, OTP_TTL_SECONDS
from core.exceptions import DomainError

from .models import Driver, DriverKyc
from .sms import get_sms_provider


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
    def get_active_driver_or_error(phone_number):
        """Looked up by phone_number alone: the otp/request and otp/verify
        endpoints are unauthenticated, so there's no company context to
        scope by yet (phone_number is only unique *within* a company — see
        Driver.Meta.constraints). Two companies sharing a driver phone
        number is an edge case this pass doesn't attempt to disambiguate.
        """
        try:
            driver = Driver.objects.get(phone_number=phone_number)
        except Driver.DoesNotExist:
            raise DomainError("DRIVER_NOT_FOUND", "No driver found with this phone number.", status_code=404)
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
    def request_otp(cls, phone_number):
        driver = cls.get_active_driver_or_error(phone_number)

        if not cache.add(cls._otp_throttle_key(phone_number), 1, timeout=OTP_THROTTLE_SECONDS):
            raise DomainError(
                "OTP_ALREADY_REQUESTED",
                "An OTP was already requested for this number recently. Please wait before retrying.",
                status_code=429,
            )

        otp = f"{random.randint(0, 999999):06d}"
        cache.set(cls._otp_cache_key(phone_number), otp, timeout=OTP_TTL_SECONDS)

        get_sms_provider().send_otp(phone_number, otp)

        return driver, otp

    @classmethod
    def verify_otp(cls, phone_number, otp):
        # Re-checks driver existence/account_status, not just OTP validity —
        # a DL could expire in the few minutes between request and verify.
        driver = cls.get_active_driver_or_error(phone_number)

        cache_key = cls._otp_cache_key(phone_number)
        stored_otp = cache.get(cache_key)
        if stored_otp is None or stored_otp != otp:
            raise DomainError("INVALID_OTP", "The OTP is invalid or has expired.", status_code=400)

        cache.delete(cache_key)
        return driver

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
    def go_online(driver, vehicle):
        if not driver.is_eligible_for_assignment:
            raise DomainError(
                "DRIVER_NOT_ELIGIBLE",
                "This driver's KYC is incomplete or their account is not active.",
                status_code=403,
            )

        driver.current_vehicle_id = vehicle.id
        driver.is_online = True
        driver.save(update_fields=["current_vehicle_id", "is_online"])

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
    """Verification decisions against a Driver's DriverKyc row."""

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
