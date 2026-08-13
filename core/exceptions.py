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

    default_code = getattr(exc, "default_code", "error")
    detail = response.data
    if isinstance(detail, dict) and set(detail.keys()) == {"detail"}:
        message = str(detail["detail"])
        details = None
    else:
        message = "Request could not be processed."
        details = detail

    error = {"code": str(default_code).upper(), "message": message}
    if details is not None:
        error["details"] = details

    response.data = {"success": False, "error": error}
    return response
