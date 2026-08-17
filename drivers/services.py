import random

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.exceptions import DomainError
from notifications.tasks import send_push_notification

from .models import Driver, DriverAccountStatus, VerificationStatus
from .sms import get_sms_provider

OTP_TTL_SECONDS = 300
OTP_THROTTLE_SECONDS = 30


def _otp_cache_key(phone_number):
    return f"driver_otp:{phone_number}"


def _otp_throttle_key(phone_number):
    return f"driver_otp_throttle:{phone_number}"


def get_active_driver_or_error(phone_number):
    """Looked up by phone_number alone: the otp/request and otp/verify
    endpoints are unauthenticated, so there's no company context to scope
    by yet (phone_number is only unique *within* a company — see
    Driver.Meta.constraints). Two companies sharing a driver phone number
    is an edge case this pass doesn't attempt to disambiguate.
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


def request_otp(phone_number):
    driver = get_active_driver_or_error(phone_number)

    if not cache.add(_otp_throttle_key(phone_number), 1, timeout=OTP_THROTTLE_SECONDS):
        raise DomainError(
            "OTP_ALREADY_REQUESTED",
            "An OTP was already requested for this number recently. Please wait before retrying.",
            status_code=429,
        )

    otp = f"{random.randint(0, 999999):06d}"
    cache.set(_otp_cache_key(phone_number), otp, timeout=OTP_TTL_SECONDS)

    get_sms_provider().send_otp(phone_number, otp)

    return driver, otp


def verify_otp(phone_number, otp):
    # Re-checks driver existence/account_status, not just OTP validity — a
    # DL could expire in the few minutes between request and verify.
    driver = get_active_driver_or_error(phone_number)

    cache_key = _otp_cache_key(phone_number)
    stored_otp = cache.get(cache_key)
    if stored_otp is None or stored_otp != otp:
        raise DomainError("INVALID_OTP", "The OTP is invalid or has expired.", status_code=400)

    cache.delete(cache_key)
    return driver


def has_active_trip(driver):
    from trips.models import ACTIVE_TRIP_STATUSES, Trip

    return Trip.objects.filter(driver=driver, status__in=ACTIVE_TRIP_STATUSES).exists()


def disable_driver(driver):
    if has_active_trip(driver):
        raise DomainError(
            "DRIVER_HAS_ACTIVE_TRIP", "This driver has an active trip and cannot be disabled.", status_code=409
        )
    driver.account_status = DriverAccountStatus.DISABLED
    driver.save(update_fields=["account_status"])
    driver.soft_delete()


def _notify_kyc_rejected(driver, doc_type, note):
    driver_id = driver.id
    transaction.on_commit(
        lambda: send_push_notification.delay(
            driver_id=driver_id,
            title="KYC Document Rejected",
            body=f"Your {doc_type} was rejected: {note}",
            data={"type": "kyc_rejected", "doc_type": doc_type},
        )
    )


def verify_aadhar(driver, status, admin_id, note=None):
    driver.aadhar_status = status
    driver.aadhar_verified_by = admin_id
    driver.aadhar_verified_at = timezone.now()
    driver.aadhar_rejection_note = note if status == VerificationStatus.REJECTED else None
    driver.save(
        update_fields=["aadhar_status", "aadhar_verified_by", "aadhar_verified_at", "aadhar_rejection_note"]
    )
    if status == VerificationStatus.REJECTED:
        _notify_kyc_rejected(driver, "Aadhar", note)
    return driver


def verify_police(driver, status, admin_id, note=None):
    driver.police_status = status
    driver.police_verified_by = admin_id
    driver.police_verified_at = timezone.now()
    driver.police_rejection_note = note if status == VerificationStatus.REJECTED else None
    driver.save(
        update_fields=["police_status", "police_verified_by", "police_verified_at", "police_rejection_note"]
    )
    if status == VerificationStatus.REJECTED:
        _notify_kyc_rejected(driver, "Police Verification", note)
    return driver


def verify_dl(driver, status, admin_id, note=None, expiry_date=None, allowed_categories=None):
    driver.dl_status = status
    driver.dl_verified_by = admin_id
    driver.dl_verified_at = timezone.now()
    driver.dl_rejection_note = note if status == VerificationStatus.REJECTED else None

    update_fields = ["dl_status", "dl_verified_by", "dl_verified_at", "dl_rejection_note"]

    if status == VerificationStatus.VERIFIED:
        driver.dl_expiry_date = expiry_date
        driver.dl_allowed_categories = allowed_categories
        update_fields += ["dl_expiry_date", "dl_allowed_categories"]

        # The only place account_status unlocks from locked_dl_expired — tied
        # specifically to a successful DL re-verification with a future
        # expiry date, per the module spec (no separate generic unlock endpoint).
        if driver.account_status == DriverAccountStatus.LOCKED_DL_EXPIRED and expiry_date >= timezone.localdate():
            driver.account_status = DriverAccountStatus.ACTIVE
            update_fields.append("account_status")

    driver.save(update_fields=update_fields)
    if status == VerificationStatus.REJECTED:
        _notify_kyc_rejected(driver, "Driving Licence", note)
    return driver
