from datetime import timedelta

from django.conf import settings
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.views import TokenObtainPairView

from . import services
from .models import AdminUser, ApiClient, ApiClientStatus
from .permissions import IsAdminUser
from .serializers import (
    AdminTokenObtainPairSerializer,
    AdminUserSerializer,
    ApiClientTokenRequestSerializer,
    CreateAdminUserSerializer,
    RefreshTokenSerializer,
)


@extend_schema(
    tags=["Admin: Auth"],
    summary="Admin login",
    description=(
        "Exchanges an AdminUser's email/password for an access + refresh JWT pair. "
        "Rejects disabled accounts (`is_active=False`) with a generic invalid-credentials "
        "error, same as a wrong password — it never reveals which part was wrong."
    ),
)
class AdminLoginView(TokenObtainPairView):
    serializer_class = AdminTokenObtainPairSerializer
    permission_classes = [AllowAny]


@extend_schema(
    tags=["Admin: Auth", "Driver: Auth"],
    summary="Refresh an access token",
    description=(
        "Exchanges a still-valid refresh token for a new access token. Works for any "
        "principal type whose refresh token was issued by this API — Admin (via login) "
        "or Driver (via OTP verify) — the token's own claims determine who it's reissued "
        "for. An invalid, malformed, or expired refresh token returns 401."
    ),
)
class RefreshTokenView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


@extend_schema(
    tags=["Integrations: Auth"],
    summary="Get an ApiClient access token",
    description=(
        "Client-credentials style exchange: a partner integration's `client_id` + "
        "`client_secret` (issued out-of-band by an admin) for a short-lived access "
        "token scoped to that client's company. No refresh token is issued — the "
        "caller re-authenticates with the same credentials once the token expires."
    ),
)
class ApiClientTokenView(APIView):
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


@extend_schema_view(
    get=extend_schema(
        tags=["Admin: Users"],
        summary="List admin users",
        description="Lists every AdminUser (sub-user) in the caller's own company, newest first.",
    ),
    post=extend_schema(
        tags=["Admin: Users"],
        summary="Create a sub-user",
        description=(
            "Creates another AdminUser under the caller's own company — the company is always "
            "taken from the authenticated caller, never from the request body, so it can't be "
            "spoofed to create a user in a different company. Every sub-user gets the same full "
            "access within their company; there are no roles/permission tiers in this pass."
        ),
    ),
)
class AdminUserListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = AdminUserSerializer

    def get_queryset(self):
        return AdminUser.objects.filter(company_id=self.request.user.company_id).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = CreateAdminUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin_user = services.create_admin_user(creator=request.user, **serializer.validated_data)
        return Response(AdminUserSerializer(admin_user).data, status=201)


@extend_schema(
    tags=["Admin: Users"],
    summary="Disable a sub-user",
    description=(
        "Soft-deletes and deactivates another AdminUser in the caller's own company — their "
        "existing JWTs stop working immediately. An admin cannot disable their own account "
        "(409 `CANNOT_DISABLE_SELF`), and an id belonging to a different company 404s rather "
        "than leaking its existence."
    ),
)
class AdminUserDisableView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk=None):
        admin_user = services.disable_admin_user(user_id=pk, actor=request.user)
        return Response(AdminUserSerializer(admin_user).data)
