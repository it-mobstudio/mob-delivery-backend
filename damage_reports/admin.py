from django.contrib import admin

from .models import VehicleDamageReport


@admin.register(VehicleDamageReport)
class VehicleDamageReportAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "reporter_type", "severity", "status", "company", "created_at")
    list_filter = ("status", "severity", "reporter_type", "company")
    search_fields = ("description",)
