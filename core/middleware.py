import re

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers


class DevCorsMiddleware:
    """Development-only CORS, so the Flutter *web* build can call this API.

    `flutter run -d chrome` serves the app from http://localhost:<random port>,
    a different origin from the API, so the browser sends a preflight and
    refuses the response unless it carries `Access-Control-Allow-Origin`.
    (Dio reports that as a plain connection error — the app's "No internet
    connection" — even though the server answered fine.) Native iOS/Android
    apps aren't subject to CORS, so this exists purely for browser testing.

    It does nothing at all unless DEBUG is on, and only for local origins
    (DEV_CORS_ORIGIN_REGEX), so it can't widen a deployed API's exposure.
    Authentication is a bearer token, not a cookie, so credentials are never
    allowed cross-origin.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.origin_re = re.compile(settings.DEV_CORS_ORIGIN_REGEX)

    def __call__(self, request):
        origin = request.headers.get("Origin")
        allowed = bool(settings.DEBUG and origin and self.origin_re.match(origin))
        is_preflight = request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers

        # Answer the preflight here: it carries no token, so letting it reach
        # the (authenticated) API views would just get a 401.
        response = HttpResponse(status=204) if allowed and is_preflight else self.get_response(request)

        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            patch_vary_headers(response, ("Origin",))
            if is_preflight:
                response["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                response["Access-Control-Allow-Headers"] = request.headers.get(
                    "Access-Control-Request-Headers", "authorization, content-type, accept"
                )
                response["Access-Control-Max-Age"] = "600"
        return response
