from rest_framework.permissions import BasePermission

from .models import AdminUser, ApiClient


class IsAdminUser(BasePermission):
    """Restricts a view to a request authenticated as an AdminUser — as
    opposed to an ApiClient or a Driver, both of which also satisfy
    IsAuthenticated under JWTMultiPrincipalAuthentication.
    """

    def has_permission(self, request, view):
        return isinstance(request.user, AdminUser)


class IsCompanyPrincipal(BasePermission):
    """Restricts a view to a request authenticated as an AdminUser or an
    ApiClient — i.e. the company's own panel or backend, as opposed to a
    Driver. Trips, the fleet and uploads are managed by the company, not by
    the drivers who work for it (see drivers.permissions.IsDriverUser for the
    reverse case).
    """

    def has_permission(self, request, view):
        return isinstance(request.user, (AdminUser, ApiClient))
