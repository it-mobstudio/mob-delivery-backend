import re

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers


class DevCorsMiddleware:
    """CORS, so a Flutter *web* build can call this API from a browser.

    A web app is served from a different origin than the API (`flutter run -d
    chrome` from http://localhost:<random port>, a deployed build from
    https://something.netlify.app), so the browser sends a preflight and refuses
    the response unless it carries `Access-Control-Allow-Origin`. (Dio reports
    that as a plain connection error - the app's "No internet connection" - even
    though the server answered fine.) Native iOS/Android apps aren't subject to
    CORS.

    Two ways an origin gets through, and nothing else does:

    * it is listed **exactly** in CORS_ALLOWED_ORIGINS (any mode - this is how a
      deployed web build is allowed), or
    * DEBUG is on and it matches DEV_CORS_ORIGIN_REGEX (local dev servers).

    No wildcard is ever sent, and credentials are never allowed cross-origin:
    authentication is a bearer token in a header, not a cookie.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.origin_re = re.compile(settings.DEV_CORS_ORIGIN_REGEX)

    def _allowed(self, origin):
        if not origin:
            return False
        if origin.lower() in settings.CORS_ALLOWED_ORIGINS:
            return True
        return bool(settings.DEBUG and self.origin_re.match(origin))

    def __call__(self, request):
        origin = request.headers.get("Origin")
        allowed = self._allowed(origin)
        is_preflight = request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers

        # Answer the preflight here: it carries no token, so letting it reach
        # the (authenticated) API views would just get a 401.
        response = HttpResponse(status=204) if allowed and is_preflight else self.get_response(request)

        if origin:
            # The answer depends on who asks, whether or not this origin is let
            # in - or a cache could hand one origin's answer to another.
            patch_vary_headers(response, ("Origin",))
        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            if is_preflight:
                response["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                response["Access-Control-Allow-Headers"] = request.headers.get(
                    "Access-Control-Request-Headers", "authorization, content-type, accept"
                )
                response["Access-Control-Max-Age"] = "600"
        return response
