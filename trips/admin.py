from django.contrib import admin

from .models import Trip


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "status", "vehicle_type", "driver", "total_fare", "created_at")
    list_filter = ("status", "company", "vehicle_type")
    search_fields = ("id", "reference_id", "pickup_address", "drop_address")
