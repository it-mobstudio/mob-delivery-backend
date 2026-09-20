from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from core.choices import ApiClientStatus

from .models import ApiClient


class JWTMultiPrincipalAuthentication(JWTAuthentication):
    """Resolves request.user to either an AdminUser or an ApiClient, based on
    the `type` claim embedded in the token at issuance time (see
    AdminTokenObtainPairSerializer and ApiClientTokenView). Both principal
    models expose `.company`, so downstream code (tenant scoping,
    permissions) doesn't need to know which one it got.
    """

    def get_user(self, validated_token):
        principal_type = validated_token.get("type", "admin")

        if principal_type == "api_client":
            client_id = validated_token.get("client_id")
            if not client_id:
                raise AuthenticationFailed("Token is missing the client_id claim.")
            try:
                return ApiClient.objects.select_related("company").get(
                    client_id=client_id, status=ApiClientStatus.ACTIVE
                )
            except ApiClient.DoesNotExist:
                raise AuthenticationFailed("API client not found or inactive.")

        if principal_type == "driver":
            # Local import: drivers depends on accounts (Driver extends
            # core.BaseModel -> Company), so importing it at module level
            # here would be circular.
            from core.choices import DriverAccountStatus
            from drivers.models import Driver

            driver_id = validated_token.get("sub")
            if not driver_id:
                raise AuthenticationFailed("Token is missing the sub claim.")
            try:
                return Driver.objects.select_related("company").get(
                    id=driver_id, account_status=DriverAccountStatus.ACTIVE
                )
            except Driver.DoesNotExist:
                raise AuthenticationFailed("Driver not found or inactive.")

        return super().get_user(validated_token)
