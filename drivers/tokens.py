from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from core.choices import DriverAccountStatus
from core.constants import DRIVER_REFRESH_TOKEN_REVOKED_CACHE_PREFIX, DRIVER_TOKEN_TYPE_CLAIM
from core.exceptions import DomainError

from .models import Driver


class DriverTokenService:
    """Access + refresh tokens for a Driver. Driver isn't a Django auth user
    model, so these are hand-built (mirrors ApiClientService.issue_token)
    rather than coming from TokenObtainPairSerializer. `type` drives
    principal dispatch in JWTMultiPrincipalAuthentication.get_user;
    `actor_type` is the human-readable claim called for by the driver-auth
    spec.
    """

    @staticmethod
    def _stamp(token, driver):
        token["type"] = DRIVER_TOKEN_TYPE_CLAIM
        token["sub"] = str(driver.id)
        token["company_id"] = str(driver.company_id)
        token["actor_type"] = "Driver"
        return token

    @classmethod
    def issue(cls, driver):
        """Returns (access_token, expires_in_seconds)."""
        lifetime = timedelta(minutes=settings.DRIVER_TOKEN_LIFETIME_MINUTES)
        access = AccessToken()
        access.set_exp(lifetime=lifetime)
        cls._stamp(access, driver)
        return str(access), int(lifetime.total_seconds())

    @classmethod
    def issue_pair(cls, driver):
        """Returns {"access", "access_expires_in", "refresh", "refresh_expires_in"}.

        The refresh token is what keeps a driver signed in across a whole
        shift: the app trades it for a fresh access token whenever the
        short-lived one expires (see refresh()), so the driver isn't kicked
        back to the SMS OTP screen every DRIVER_TOKEN_LIFETIME_MINUTES.
        """
        access, access_expires_in = cls.issue(driver)

        refresh_lifetime = timedelta(days=settings.DRIVER_REFRESH_TOKEN_LIFETIME_DAYS)
        refresh = RefreshToken()
        refresh.set_exp(lifetime=refresh_lifetime)
        cls._stamp(refresh, driver)

        return {
            "access": access,
            "access_expires_in": access_expires_in,
            "refresh": str(refresh),
            "refresh_expires_in": int(refresh_lifetime.total_seconds()),
        }

    @staticmethod
    def _invalid_refresh_error():
        return DomainError(
            "INVALID_REFRESH_TOKEN", "Your session has expired. Please sign in again.", status_code=401
        )

    @classmethod
    def _parse_refresh(cls, raw_refresh):
        try:
            token = RefreshToken(raw_refresh)
        except TokenError:
            raise cls._invalid_refresh_error()

        # A refresh token minted for an AdminUser (simplejwt's own login
        # flow) is a perfectly valid RefreshToken too — the `type` claim is
        # what says this one belongs to a Driver.
        if token.get("type") != DRIVER_TOKEN_TYPE_CLAIM:
            raise cls._invalid_refresh_error()
        if cache.get(f"{DRIVER_REFRESH_TOKEN_REVOKED_CACHE_PREFIX}{token['jti']}"):
            raise cls._invalid_refresh_error()
        return token

    @classmethod
    def refresh(cls, raw_refresh):
        """Exchanges a valid, unrevoked refresh token for a new pair. The
        driver is re-checked here (not just trusted from the token) so a
        driver who was disabled or locked mid-shift can't keep renewing.
        """
        token = cls._parse_refresh(raw_refresh)

        try:
            driver = Driver.objects.get(id=token["sub"], account_status=DriverAccountStatus.ACTIVE)
        except Driver.DoesNotExist:
            raise cls._invalid_refresh_error()

        return driver, cls.issue_pair(driver)

    @classmethod
    def revoke(cls, raw_refresh):
        """Logout. Idempotent, and silent about tokens that are already
        invalid — there's nothing left to revoke, and the caller (a driver
        tapping "sign out") shouldn't be told their sign-out failed.
        """
        try:
            token = cls._parse_refresh(raw_refresh)
        except DomainError:
            return

        remaining = int(token["exp"] - timezone.now().timestamp())
        if remaining > 0:
            cache.set(f"{DRIVER_REFRESH_TOKEN_REVOKED_CACHE_PREFIX}{token['jti']}", 1, timeout=remaining)
