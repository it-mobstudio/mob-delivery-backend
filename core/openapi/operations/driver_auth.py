"""Driver sign-in: phone + SMS OTP, refresh, sign out."""

from core.openapi.dsl import PUBLIC, doc, document, ex, ok, raw_ex
from drivers.serializers import (
    DriverOtpRequestResultSerializer,
    DriverOtpRequestSerializer,
    DriverOtpVerifySerializer,
    DriverSessionSerializer,
    DriverTokenRefreshSerializer,
)
from core.serializers import MessageSerializer
from drivers.views import DriverLogoutView, DriverOtpRequestView, DriverOtpVerifyView, DriverTokenRefreshView

TAG = "Driver sign-in"
PHONE = "+919000000001"

document(
    DriverOtpRequestView,
    post=doc(
        id="driverRequestOtp",
        tag=TAG,
        summary="Send a sign-in code by SMS",
        description="""
**Step 1 of signing a driver in.** Sends a 4-digit one-time code by SMS to `phone_number`. It is valid for **5 minutes**.

The answer is the same whether or not the number belongs to a driver yet, so this endpoint never reveals who is registered. When the
server allows **self sign-up**, a number nobody has registered also gets a code - and becomes a new, empty driver account once
the code is verified (see *Driver onboarding & wallet*). When sign-up is closed, only numbers the company registered get a code
(`DRIVER_NOT_FOUND` otherwise).

On a **non-production server** the response also contains the code in `otp` so the flow can be tested without an SMS gateway. A
production server never returns it.
""",
        auth=PUBLIC,
        request=DriverOtpRequestSerializer,
        request_examples=[raw_ex("Send a code", {"phone_number": PHONE}, request=True)],
        responses={200: ok(DriverOtpRequestResultSerializer, ex("driver_auth.otp_request", "Code sent (test server shows the code)"))},
        errors=["DRIVER_NOT_FOUND", "ACCOUNT_DISABLED", "ACCOUNT_LOCKED", "DRIVER_PHONE_AMBIGUOUS"],
    ),
)

document(
    DriverOtpVerifyView,
    post=doc(
        id="driverVerifyOtp",
        tag=TAG,
        summary="Sign in with the code",
        description="""
**Step 2.** Trade the SMS code for a session: an `accessToken` (60 minutes), a `refreshToken` (30 days) and the driver's profile, so the
app can decide what to show first (`driver.onboarding_status` tells it whether to run onboarding).

Send the access token on every driver call as `Authorization: Bearer <accessToken>`. Before it expires, renew the pair with
`POST /driver/auth/refresh`; when the refresh token finally lapses, the driver signs in with a new SMS code.

A wrong or expired code is `INVALID_OTP`; the code is single-use.
""",
        auth=PUBLIC,
        request=DriverOtpVerifySerializer,
        request_examples=[raw_ex("Phone and the code from the SMS", {"phone_number": PHONE, "otp": "4829"}, request=True)],
        responses={200: ok(DriverSessionSerializer, ex("driver_auth.otp_verify", "Signed in"))},
        errors=["INVALID_OTP", "DRIVER_NOT_FOUND", "ACCOUNT_DISABLED", "ACCOUNT_LOCKED", "DRIVER_PHONE_AMBIGUOUS"],
    ),
)

document(
    DriverTokenRefreshView,
    post=doc(
        id="driverRefreshSession",
        tag=TAG,
        summary="Renew a session",
        description="""
Trades a still-valid `refreshToken` for a **new pair** of tokens (and restarts the refresh token's 30-day lifetime), so a driver
stays signed in across shifts without another SMS. Replace both stored tokens with the new ones.

If the refresh token has expired or was revoked by sign-out, this is `INVALID_REFRESH_TOKEN` (401): send the driver through
the OTP sign-in again.
""",
        auth=PUBLIC,
        request=DriverTokenRefreshSerializer,
        request_examples=[raw_ex("Your refresh token", {"refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}, request=True)],
        responses={200: ok(DriverSessionSerializer, ex("driver_auth.refresh", "New session"))},
        errors=["INVALID_REFRESH_TOKEN", "ACCOUNT_DISABLED", "ACCOUNT_LOCKED"],
    ),
)

document(
    DriverLogoutView,
    post=doc(
        id="driverSignOut",
        tag=TAG,
        summary="Sign out",
        description="""
Revokes the refresh token so this session can't be revived from this device - or from a copy of the token. Discard both tokens locally.
The short-lived access token simply runs out (60 minutes). Signing out with an already-invalid token still succeeds.
""",
        auth=PUBLIC,
        request=DriverTokenRefreshSerializer,
        request_examples=[raw_ex("The refresh token to revoke", {"refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}, request=True)],
        responses={200: ok(MessageSerializer, ex("driver_auth.logout", "Signed out"))},
    ),
)
