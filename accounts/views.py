from datetime import timedelta

from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import ApiClient, ApiClientStatus
from .serializers import AdminTokenObtainPairSerializer, ApiClientTokenRequestSerializer


class AdminLoginView(TokenObtainPairView):
    """POST /api/v1/auth/login — email + password -> access/refresh JWT for an AdminUser."""

    serializer_class = AdminTokenObtainPairSerializer
    permission_classes = [AllowAny]


class ApiClientTokenView(APIView):
    """POST /api/v1/auth/client-token — client_id + client_secret -> a short-lived
    access token for an ApiClient (client-credentials style, no refresh token)."""

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = ApiClientTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client_id = serializer.validated_data["client_id"]
        client_secret = serializer.validated_data["client_secret"]

        try:
            client = ApiClient.objects.get(client_id=client_id, status=ApiClientStatus.ACTIVE)
        except ApiClient.DoesNotExist:
            raise AuthenticationFailed("Invalid client credentials.")

        if not client.check_secret(client_secret):
            raise AuthenticationFailed("Invalid client credentials.")

        lifetime = timedelta(minutes=settings.API_CLIENT_TOKEN_LIFETIME_MINUTES)
        access = AccessToken()
        access.set_exp(lifetime=lifetime)
        access["type"] = "api_client"
        access["client_id"] = str(client.client_id)
        access["company_id"] = str(client.company_id)

        return Response(
            {"access": str(access), "expires_in": int(lifetime.total_seconds())}
        )
