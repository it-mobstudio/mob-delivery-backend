from datetime import timedelta

from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import AccessToken

from core.choices import ApiClientStatus
from core.constants import API_CLIENT_TOKEN_TYPE_CLAIM

from .models import ApiClient


class ApiClientService:
    """Client-credentials style token issuance for ApiClient principals —
    see accounts.authentication.JWTMultiPrincipalAuthentication for how the
    resulting token gets resolved back to an ApiClient on later requests.
    """

    @staticmethod
    def issue_token(client_id, client_secret):
        try:
            client = ApiClient.objects.get(client_id=client_id, status=ApiClientStatus.ACTIVE)
        except ApiClient.DoesNotExist:
            raise AuthenticationFailed("Invalid client credentials.")

        if not client.check_secret(client_secret):
            raise AuthenticationFailed("Invalid client credentials.")

        lifetime = timedelta(minutes=settings.API_CLIENT_TOKEN_LIFETIME_MINUTES)
        access = AccessToken()
        access.set_exp(lifetime=lifetime)
        access["type"] = API_CLIENT_TOKEN_TYPE_CLAIM
        access["client_id"] = str(client.client_id)
        access["company_id"] = str(client.company_id)

        return str(access), int(lifetime.total_seconds())
