"""Company authentication: API client token, admin login, admin refresh."""

from rest_framework_simplejwt.views import TokenRefreshView

from accounts.serializers import (
    AdminAccessTokenSerializer,
    AdminLoginRequestSerializer,
    AdminTokenPairSerializer,
    AdminTokenRefreshRequestSerializer,
    ApiClientTokenRequestSerializer,
    ApiClientTokenSerializer,
)
from accounts.views import AdminLoginView, ApiClientTokenView
from core.openapi.dsl import PUBLIC, doc, document, ex, ok, raw_ex

TAG = "Company authentication"

document(
    ApiClientTokenView,
    post=doc(
        id="createClientToken",
        tag=TAG,
        summary="Get an access token (API client)",
        description="""
Exchange your **client id and client secret** for a short-lived access token. This is how a
company's *backend* signs in (OAuth "client credentials" style): call it from your server,
keep the token in memory, and send it on every other call as `Authorization: Bearer <access>`.

The client id and secret are issued when your API client is created; **the secret is shown only
once**, so store it like a password and never put it in a browser or mobile app.

The token is valid for `expires_in` seconds (3600 = 60 minutes). There is no refresh token: when it
expires - or when any call returns `401 TOKEN_NOT_VALID` - simply call this endpoint again.
""",
        auth=PUBLIC,
        request=ApiClientTokenRequestSerializer,
        request_examples=[
            raw_ex(
                "Your credentials",
                {"client_id": "0b1c3a52-6f0e-4d3e-9a55-1f2b7c9d4e10", "client_secret": "sk_live_..."},
                request=True,
            )
        ],
        responses={200: ok(ApiClientTokenSerializer, ex("auth.client_token", "Token issued"))},
        errors=["AUTHENTICATION_FAILED"],
    ),
)

document(
    AdminLoginView,
    post=doc(
        id="adminLogin",
        tag=TAG,
        summary="Sign in (admin panel)",
        description="""
Email + password sign-in for a company **admin user** (the people who use the company's admin
panel). Returns a short-lived `access` token and a long-lived `refresh` token.

Send `access` as `Authorization: Bearer <access>`. When it expires, trade the `refresh` token for a new
one at `POST /auth/refresh` instead of asking for the password again.

Integrations that run without a person should use an API client
(`POST /auth/client-token`) rather than an admin's password.
""",
        auth=PUBLIC,
        request=AdminLoginRequestSerializer,
        request_examples=[raw_ex("Admin credentials", {"email": "ops@yourcompany.com", "password": "correct horse battery"}, request=True)],
        responses={200: ok(AdminTokenPairSerializer, ex("auth.login", "Signed in"))},
        errors=["AUTHENTICATION_FAILED"],
    ),
)

document(
    TokenRefreshView,
    post=doc(
        id="refreshAdminToken",
        tag=TAG,
        summary="Renew an admin access token",
        description="""
Trade the `refresh` token from `POST /auth/login` for a fresh `access` token.
The refresh token itself stays valid until it expires (7 days by default), so keep it and reuse it.

If the refresh token has expired, the admin has to sign in again.
""",
        auth=PUBLIC,
        request=AdminTokenRefreshRequestSerializer,
        request_examples=[raw_ex("Refresh", {"refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}, request=True)],
        responses={200: ok(AdminAccessTokenSerializer, ex("auth.refresh", "New access token"))},
        errors=["TOKEN_NOT_VALID"],
    ),
)
