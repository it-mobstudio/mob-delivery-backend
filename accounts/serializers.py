from django.conf import settings
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer

from .models import AdminUser


class AdminTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["type"] = "admin"
        token["company_id"] = str(user.company_id)
        token["role"] = user.role
        return token

    def validate(self, attrs):
        # Reshape simplejwt's default {"refresh": ..., "access": ...} into
        # this API's established naming (accessToken/refreshToken/tokenType/
        # expiresInSeconds — matches the Driver login response shape).
        data = super().validate(attrs)
        return {
            "access_token": data["access"],
            "refresh_token": data["refresh"],
            "token_type": "Bearer",
            "expires_in_seconds": int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        }


class ApiClientTokenRequestSerializer(serializers.Serializer):
    client_id = serializers.UUIDField()
    client_secret = serializers.CharField(write_only=True, trim_whitespace=False)


class RefreshTokenSerializer(serializers.Serializer):
    """POST /api/v1/auth/refresh — works for ANY principal type whose refresh
    token was hand-built with the right claims (Admin via
    AdminTokenObtainPairSerializer, Driver via drivers.tokens.issue_driver_token):
    simplejwt's TokenRefreshSerializer validates+reissues generically and
    copies claims across, it doesn't care which principal type issued it.
    """

    refresh_token = serializers.CharField()

    def validate(self, attrs):
        inner = TokenRefreshSerializer(data={"refresh": attrs["refresh_token"]})
        try:
            inner.is_valid(raise_exception=True)
        except TokenError as exc:
            # simplejwt's own TokenRefreshSerializer.validate() constructs
            # the token as its very first line, OUTSIDE any try/except —
            # an invalid/expired/malformed token raises a raw TokenError
            # that DRF's is_valid() doesn't catch (it's not a ValidationError),
            # which would otherwise surface as an unhandled 500. Convert it
            # to InvalidToken (an AuthenticationFailed subclass) for a clean 401.
            raise InvalidToken(str(exc))
        return {
            "access_token": inner.validated_data["access"],
            "token_type": "Bearer",
            "expires_in_seconds": int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        }


class CreateAdminUserSerializer(serializers.Serializer):
    """Fix 8 — sub-user management. No company_id field, deliberately —
    the new admin's company is always the creator's own (see
    accounts.services.create_admin_user), never client-supplied.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=100, default="")
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=100, default="")

    def validate_email(self, value):
        value = value.strip().lower()
        if AdminUser.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An admin user with this email already exists.")
        return value


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminUser
        fields = ["id", "email", "first_name", "last_name", "role", "is_active", "created_at", "updated_at"]
        read_only_fields = fields
