from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils.html import format_html

from core.admin_utils import badge, is_platform_admin
from core.choices import AdminRole, ApiClientStatus, TripStatus

from .models import AdminUser, ApiClient, Company

# Access here is role-based (AdminUser.role), not group-based.
admin.site.unregister(Group)


class PlatformOnlyAdmin(admin.ModelAdmin):
    """Companies are the platform's business, not a company operator's."""

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return False  # a company owns drivers, trips and money — suspend it instead


@admin.register(Company)
class CompanyAdmin(PlatformOnlyAdmin):
    list_display = ("name", "state", "drivers", "orders", "completed", "created_at")
    search_fields = ("name",)
    list_filter = ("status", "created_at")
    actions = ["suspend", "activate"]

    @admin.display(description="Status", ordering="status")
    def state(self, c):
        return badge(c.status)

    @admin.display(description="Drivers")
    def drivers(self, c):
        from drivers.models import Driver

        n = Driver.objects.filter(company=c).count()
        return format_html('<a href="{}?company__id__exact={}">{}</a>', reverse("admin:drivers_driver_changelist"), c.pk, n)

    @admin.display(description="Orders")
    def orders(self, c):
        from trips.models import Trip

        n = Trip.objects.filter(company=c).count()
        return format_html('<a href="{}?company__id__exact={}">{}</a>', reverse("admin:trips_trip_changelist"), c.pk, n)

    @admin.display(description="Delivered")
    def completed(self, c):
        from trips.models import Trip

        return Trip.objects.filter(company=c, status=TripStatus.COMPLETED).count()

    @admin.action(description="Suspend (API and drivers stop working)")
    def suspend(self, request, queryset):
        n = queryset.update(status="suspended")
        self.message_user(request, f"Suspended {n} company(ies).", messages.WARNING)

    @admin.action(description="Activate")
    def activate(self, request, queryset):
        n = queryset.update(status="active")
        self.message_user(request, f"Activated {n} company(ies).", messages.SUCCESS)


@admin.register(AdminUser)
class AdminUserAdmin(DjangoUserAdmin):
    """Who can sign in here. A company owner manages their own company's
    operators; the platform manages everyone."""

    model = AdminUser
    list_display = ("email", "full_name", "company", "role_badge", "is_active", "is_superuser", "last_login")
    list_filter = ("role", "is_active", "is_staff", "is_superuser", "company")
    search_fields = ("email", "first_name", "last_name", "company__name")
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Person", {"fields": ("first_name", "last_name", "company", "role")}),
        ("Access", {"fields": ("is_active", "is_staff", "is_superuser"),
                    "description": "Owner / admin can manage their company's data here; staff can only look. "
                                   "Staff status is what lets them sign in to this admin."}),
        ("Activity", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "company", "role", "password1", "password2")}),
    )
    readonly_fields = ("last_login",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if is_platform_admin(request.user) else qs.filter(company_id=request.user.company_id)

    def _owner(self, request):
        return is_platform_admin(request.user) or (request.user.is_staff and request.user.role == AdminRole.OWNER)

    def has_module_permission(self, request):
        return self._owner(request)

    def has_view_permission(self, request, obj=None):
        return self._owner(request)

    def has_change_permission(self, request, obj=None):
        return self._owner(request)

    def has_add_permission(self, request):
        return self._owner(request)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if not is_platform_admin(request.user):
            ro += ["company", "is_superuser"]  # an owner can't promote anyone to platform or move companies
        return ro

    def get_changeform_initial_data(self, request):
        return {"company": request.user.company_id, "is_staff": True}

    def save_model(self, request, obj, form, change):
        if not is_platform_admin(request.user):
            obj.company_id = request.user.company_id
            obj.is_superuser = False
        if not change:
            obj.is_staff = True
        super().save_model(request, obj, form, change)

    @admin.display(description="Name")
    def full_name(self, u):
        return f"{u.first_name} {u.last_name}".strip() or "—"

    @admin.display(description="Role", ordering="role")
    def role_badge(self, u):
        return badge({"owner": "verified", "admin": "assigned", "staff": "inactive"}.get(u.role, u.role), u.get_role_display())


@admin.register(ApiClient)
class ApiClientAdmin(PlatformOnlyAdmin):
    """The machine credentials a company's systems book trips with. Secrets
    are only ever shown once, when made with `manage.py create_api_client`;
    here they can be looked at and revoked."""

    list_display = ("name", "company", "client_id", "state", "created_at")
    list_filter = ("status", "company")
    search_fields = ("name", "company__name", "=client_id")
    readonly_fields = ("client_id", "company", "created_at")
    fields = ("name", "company", "client_id", "status", "created_at")
    actions = ["revoke"]

    def has_add_permission(self, request):
        return False  # see the class docstring

    @admin.display(description="Status", ordering="status")
    def state(self, c):
        return badge(c.status)

    @admin.action(description="Revoke (stop accepting its tokens)")
    def revoke(self, request, queryset):
        n = queryset.update(status=ApiClientStatus.INACTIVE)
        self.message_user(request, f"Revoked {n} client(s).", messages.WARNING)
