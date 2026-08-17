from django.contrib import admin

from .models import SosAlert


@admin.register(SosAlert)
class SosAlertAdmin(admin.ModelAdmin):
    list_display = ("driver", "status", "triggered_at", "company")
    list_filter = ("status", "company")
