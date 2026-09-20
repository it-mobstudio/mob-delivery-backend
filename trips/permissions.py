from rest_framework.permissions import BasePermission

from accounts.models import AdminUser, ApiClient


class IsCompanyPrincipal(BasePermission):
    """Restricts a view to a request authenticated as an AdminUser or an
    ApiClient — i.e. the company's own panel or backend, as opposed to a
    Driver. Trips are booked by the company, not by the driver fulfilling
    them (see drivers.permissions.IsDriverUser for the reverse case).
    """

    def has_permission(self, request, view):
        return isinstance(request.user, (AdminUser, ApiClient))
