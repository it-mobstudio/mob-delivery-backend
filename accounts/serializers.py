from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class AdminTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["type"] = "admin"
        token["company_id"] = str(user.company_id)
        token["role"] = user.role
        return token


class ApiClientTokenRequestSerializer(serializers.Serializer):
    client_id = serializers.UUIDField(help_text="The API client's id, as shown once when the client was created.")
    client_secret = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        help_text="The API client's secret. Keep it on your server — never ship it in a browser or mobile app.",
    )


# -- Documentation shapes ------------------------------------------------------
# The views above use simplejwt's own request handling, so these describe what
# goes over the wire for the API schema (core.openapi) rather than being used
# to parse it.


class AdminLoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="The admin user's email address.")
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, help_text="The admin user's password."
    )


class AdminTokenPairSerializer(serializers.Serializer):
    access = serializers.CharField(
        help_text="Short-lived JWT (`ACCESS_TOKEN_LIFETIME_MINUTES`, 60 by default). Send it as `Authorization: Bearer <access>`."
    )
    refresh = serializers.CharField(
        help_text="Long-lived JWT (`REFRESH_TOKEN_LIFETIME_DAYS`, 7 by default). Exchange it at `POST /auth/refresh` for a new access token."
    )


class AdminTokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(help_text="The `refresh` token from `POST /auth/login`.")


class AdminAccessTokenSerializer(serializers.Serializer):
    access = serializers.CharField(help_text="A new short-lived access token.")


class ApiClientTokenSerializer(serializers.Serializer):
    access = serializers.CharField(help_text="Short-lived JWT. Send it as `Authorization: Bearer <access>`.")
    expires_in = serializers.IntegerField(
        help_text="Seconds until the token expires (`API_CLIENT_TOKEN_LIFETIME_MINUTES`, 60 minutes = 3600 by default). There is no refresh token: request a new one with the same credentials."
    )
