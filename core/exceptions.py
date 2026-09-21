from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class DomainError(APIException):
    """A business-rule violation. Rendered as a Result-style error envelope
    instead of a raw DB integrity error or a generic DRF validation error.
    """

    def __init__(self, code, message, status_code=409):
        self.code = code
        self.status_code = status_code
        super().__init__(detail=message)


def exception_handler(exc, context):
    """Renders every DRF-handled exception as
    {"success": false, "error": {"code": ..., "message": ..., "details": ...}}.

    `code` is stable and machine-readable (clients branch on it); `message` is
    for people; `details` only appears for field validation errors
    ({"field": ["problem", ...]}).
    """
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, DomainError):
        response.data = {
            "success": False,
            "error": {"code": exc.code, "message": str(exc.detail)},
        }
        return response

    # Django's own 404/403 are turned into DRF exceptions *inside* the DRF
    # handler, so the original exception has no `default_code` of its own.
    if isinstance(exc, Http404):
        default_code = "not_found"
    elif isinstance(exc, DjangoPermissionDenied):
        default_code = "permission_denied"
    else:
        default_code = getattr(exc, "default_code", "error")

    data = response.data
    if isinstance(data, dict) and "detail" in data:
        # NotAuthenticated, PermissionDenied, NotFound, MethodNotAllowed,
        # ParseError, Throttled, an invalid token (simplejwt adds extra keys
        # next to `detail`; they're internals, not for clients)...
        message = str(data["detail"])
        details = None
    else:
        message = "Request could not be processed."
        details = data

    error = {"code": str(default_code).upper(), "message": message}
    if details is not None:
        error["details"] = details

    response.data = {"success": False, "error": error}
    return response
