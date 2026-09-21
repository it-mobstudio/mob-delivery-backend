from django.contrib import admin

from .models import Trip, TripItem


class TripItemInline(admin.TabularInline):
    model = TripItem
    extra = 0
    fields = ("position", "name", "quantity", "unit", "status", "verified_at", "proof_image_url", "driver_note")
    readonly_fields = ("status", "verified_at", "proof_image_url", "driver_note")


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "status", "vehicle_type", "driver", "total_fare", "verify_items", "created_at")
    list_filter = ("status", "company", "vehicle_type", "verify_items")
    search_fields = ("id", "reference_id", "invoice_number", "pickup_address", "drop_address")
    inlines = [TripItemInline]
