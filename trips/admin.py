from django.contrib import admin

from .models import AddressChangeLog, Trip, TripStop, TripVehicleHistory


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "status", "vehicle", "driver", "total_weight_kg", "started_at", "completed_at")
    list_filter = ("status", "company")


@admin.register(TripStop)
class TripStopAdmin(admin.ModelAdmin):
    list_display = ("order_ref", "trip", "stop_type", "status", "sequence_no")
    list_filter = ("stop_type", "status")
    search_fields = ("order_ref", "parent_order_ref")


@admin.register(TripVehicleHistory)
class TripVehicleHistoryAdmin(admin.ModelAdmin):
    list_display = ("trip", "previous_vehicle", "new_vehicle", "previous_driver", "new_driver", "created_at")


@admin.register(AddressChangeLog)
class AddressChangeLogAdmin(admin.ModelAdmin):
    list_display = ("trip_stop", "old_address", "new_address", "created_at")
