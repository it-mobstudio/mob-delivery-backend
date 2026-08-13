from django.contrib import admin

from .models import Driver


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone_number",
        "company",
        "account_status",
        "aadhar_status",
        "dl_status",
        "police_status",
    )
    list_filter = ("account_status", "aadhar_status", "dl_status", "police_status", "company")
    search_fields = ("full_name", "phone_number")
