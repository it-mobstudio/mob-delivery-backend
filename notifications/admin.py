from django.contrib import admin

from .models import DriverDevice


@admin.register(DriverDevice)
class DriverDeviceAdmin(admin.ModelAdmin):
    list_display = ("driver", "platform", "last_active_at", "company")
    list_filter = ("platform", "company")
