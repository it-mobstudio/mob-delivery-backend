"""Who may use the console, whose data they see, and what they may change.

Same rules as the Django admin (core.admin_utils): a superuser runs the whole
platform and can narrow every screen to one company with the switcher; a
company's owner / admin manage their own company; staff can look but not
change anything."""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from core.choices import AdminRole

SESSION_COMPANY = "console_company"


def is_platform(user):
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def can_edit(user):
    return is_platform(user) or getattr(user, "role", None) in (AdminRole.OWNER, AdminRole.ADMIN)


def can_manage_team(user):
    return is_platform(user) or getattr(user, "role", None) == AdminRole.OWNER


def company_id_for(request):
    """The company every screen is narrowed to, or None for all (platform)."""
    if is_platform(request.user):
        return request.session.get(SESSION_COMPANY) or None
    return request.user.company_id


def scoped(request, queryset, field="company"):
    company_id = company_id_for(request)
    return queryset.filter(**{f"{field}_id": company_id}) if company_id else queryset


def console_view(edit=False, platform=False, team=False):
    """Decorator: signed-in staff only; `edit` needs owner/admin, `platform`
    a superuser, `team` an owner (or superuser)."""

    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not (user.is_authenticated and user.is_active and user.is_staff):
                return redirect_to_login(request.get_full_path(), "console:login")
            if platform and not is_platform(user):
                raise PermissionDenied
            if team and not can_manage_team(user):
                raise PermissionDenied
            if edit and request.method == "POST" and not can_edit(user):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorate
