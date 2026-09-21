from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import AdminTokenObtainPairSerializer, ApiClientTokenRequestSerializer
from .services import ApiClientService


class AdminLoginView(TokenObtainPairView):
    """POST /api/v1/auth/login — email + password -> access/refresh JWT for an AdminUser."""

    serializer_class = AdminTokenObtainPairSerializer
    permission_classes = [AllowAny]


class ApiClientTokenView(APIView):
    """POST /api/v1/auth/client-token — client_id + client_secret -> a short-lived
    access token for an ApiClient (client-credentials style, no refresh token)."""

    # No authenticators: DRF runs them even for AllowAny views, so a stale or
    # expired Bearer token left in a client's default headers would 401 the very
    # request that is supposed to get it a fresh one.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = ApiClientTokenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        access, expires_in = ApiClientService.issue_token(
            serializer.validated_data["client_id"], serializer.validated_data["client_secret"]
        )

        return Response({"access": access, "expires_in": expires_in})
