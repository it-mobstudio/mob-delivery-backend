from rest_framework.permissions import BasePermission

from .models import AdminUser


class IsAdminUser(BasePermission):
    """Restricts a view to a request authenticated as an AdminUser — as
    opposed to an ApiClient or a Driver, both of which also satisfy
    IsAuthenticated under JWTMultiPrincipalAuthentication.
    """

    def has_permission(self, request, view):
        return isinstance(request.user, AdminUser)
