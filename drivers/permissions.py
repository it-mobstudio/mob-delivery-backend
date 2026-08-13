from rest_framework.permissions import BasePermission

from .models import Driver


class IsDriverUser(BasePermission):
    """Restricts a view to a request authenticated as a Driver — as opposed
    to an AdminUser or ApiClient, both of which also satisfy IsAuthenticated
    under JWTMultiPrincipalAuthentication.
    """

    def has_permission(self, request, view):
        return isinstance(request.user, Driver)
