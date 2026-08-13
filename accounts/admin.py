from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AdminUser, ApiClient, Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "created_at")
    search_fields = ("name",)
    list_filter = ("status",)


@admin.register(AdminUser)
class AdminUserAdmin(DjangoUserAdmin):
    model = AdminUser
    list_display = ("email", "company", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "company")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "company", "role")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "company", "role", "password1", "password2"),
            },
        ),
    )
    filter_horizontal = ("groups", "user_permissions")


@admin.register(ApiClient)
class ApiClientAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "client_id", "status")
    list_filter = ("status", "company")
    readonly_fields = ("client_id",)
