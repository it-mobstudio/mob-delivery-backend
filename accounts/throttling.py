from rest_framework.throttling import SimpleRateThrottle

from .models import ApiClient


class ApiClientRateThrottle(SimpleRateThrottle):
    """Per-ApiClient rate limit (Fix 6) — applied globally via
    DEFAULT_THROTTLE_CLASSES so no endpoint can be forgotten, but only
    actually throttles when the caller is an ApiClient. AdminUser/Driver
    requests return None from get_cache_key(), which DRF treats as
    "skip throttling for this request" (SimpleRateThrottle.allow_request).

    Rate is a single flat default for now (DEFAULT_THROTTLE_RATES["api_client"]
    in settings) — not per-client-configurable yet, per the module spec.
    """

    scope = "api_client"

    def get_cache_key(self, request, view):
        if not isinstance(request.user, ApiClient):
            return None
        return self.cache_format % {"scope": self.scope, "ident": request.user.pk}
