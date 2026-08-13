from django.contrib import admin

from .models import Vehicle, VehicleDocument, VehicleType


@admin.register(VehicleType)
class VehicleTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "company", "status", "default_capacity_kg")
    list_filter = ("category", "status", "company")
    search_fields = ("name",)


class VehicleDocumentInline(admin.TabularInline):
    model = VehicleDocument
    extra = 0


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("registration_number", "vehicle_type", "company", "status", "capacity_kg")
    list_filter = ("status", "company", "vehicle_type__category")
    search_fields = ("registration_number",)
    inlines = [VehicleDocumentInline]
