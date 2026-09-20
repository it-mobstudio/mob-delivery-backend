from datetime import timedelta

from django.conf import settings
from rest_framework_simplejwt.tokens import AccessToken

from core.constants import DRIVER_TOKEN_TYPE_CLAIM


class DriverTokenService:
    @staticmethod
    def issue(driver):
        """Mirrors ApiClientService.issue_token: a hand-built AccessToken
        rather than TokenObtainPairSerializer, since Driver isn't a Django
        auth user model. `type` drives principal dispatch in
        JWTMultiPrincipalAuthentication.get_user; `actor_type` is the
        human-readable claim called for by the driver-auth spec.
        """
        lifetime = timedelta(minutes=settings.DRIVER_TOKEN_LIFETIME_MINUTES)
        access = AccessToken()
        access.set_exp(lifetime=lifetime)
        access["type"] = DRIVER_TOKEN_TYPE_CLAIM
        access["sub"] = str(driver.id)
        access["company_id"] = str(driver.company_id)
        access["actor_type"] = "Driver"
        return str(access), int(lifetime.total_seconds())
