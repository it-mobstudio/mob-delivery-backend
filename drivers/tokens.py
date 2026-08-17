from datetime import timedelta

from django.conf import settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


def issue_driver_token(driver):
    """Mirrors ApiClientTokenView's approach (accounts.views): hand-built
    tokens rather than TokenObtainPairSerializer, since Driver isn't a
    Django auth user model. `type` drives principal dispatch in
    JWTMultiPrincipalAuthentication.get_user; `actor_type` is the
    human-readable claim called for by the driver-auth spec.

    The refresh token carries the same claims as the access token — simplejwt
    copies non-reserved claims from a refresh token onto the access token it
    mints at /api/v1/auth/refresh (see accounts.serializers.RefreshTokenSerializer),
    so a driver's refreshed access token comes back correctly typed without
    any driver-specific refresh logic.
    """
    access_lifetime = timedelta(minutes=settings.DRIVER_TOKEN_LIFETIME_MINUTES)
    access = AccessToken()
    access.set_exp(lifetime=access_lifetime)
    access["type"] = "driver"
    access["sub"] = str(driver.id)
    access["company_id"] = str(driver.company_id)
    access["actor_type"] = "Driver"

    refresh_lifetime = timedelta(days=settings.DRIVER_REFRESH_TOKEN_LIFETIME_DAYS)
    refresh = RefreshToken()
    refresh.set_exp(lifetime=refresh_lifetime)
    refresh["type"] = "driver"
    refresh["sub"] = str(driver.id)
    refresh["company_id"] = str(driver.company_id)
    refresh["actor_type"] = "Driver"

    return str(access), str(refresh), int(access_lifetime.total_seconds())
