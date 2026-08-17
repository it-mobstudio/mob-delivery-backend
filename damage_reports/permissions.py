from rest_framework.permissions import BasePermission

from accounts.models import AdminUser
from drivers.models import Driver


class IsDriverOrAdminUser(BasePermission):
    """Damage reports can be filed by a Driver (about their own currently
    assigned vehicle) or an AdminUser (about any vehicle) — but not an
    ApiClient, which has no notion of "currently assigned vehicle" or KYC
    identity to attribute the report to.
    """

    def has_permission(self, request, view):
        return isinstance(request.user, (Driver, AdminUser))
