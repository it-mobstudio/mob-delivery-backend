from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


def authenticate_ws_token(scope):
    """Resolves the JWT passed as a `?token=` query param on a Channels
    WebSocket scope to its principal (AdminUser/ApiClient/Driver).

    Channels doesn't run DRF authentication classes, so this is done by
    hand, reusing JWTMultiPrincipalAuthentication's token validation and
    principal resolution rather than re-implementing it in every consumer.
    The access token is a query param (not a header) since browser
    WebSocket clients can't set custom headers.

    Returns None if the token is missing or invalid.
    """
    # Local import: avoids pulling DRF/simplejwt auth machinery in at
    # Channels routing import time (mob_delivery.asgi imports routing before
    # django.setup() side effects are guaranteed to have fully settled).
    from accounts.authentication import JWTMultiPrincipalAuthentication

    query_string = scope.get("query_string", b"").decode()
    params = dict(pair.split("=", 1) for pair in query_string.split("&") if "=" in pair)
    raw_token = params.get("token")
    if not raw_token:
        return None

    auth = JWTMultiPrincipalAuthentication()
    try:
        validated_token = auth.get_validated_token(raw_token.encode())
        return auth.get_user(validated_token)
    except (InvalidToken, TokenError, AuthenticationFailed):
        return None
