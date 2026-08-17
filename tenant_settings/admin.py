from django.contrib import admin

from .models import TenantSetting


@admin.register(TenantSetting)
class TenantSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "company")
    list_filter = ("company",)
    search_fields = ("key",)
