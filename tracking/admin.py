from django.contrib import admin

from .models import DriverShift, TripAnomalyAlert, TripLocationPing, TripPause


@admin.register(DriverShift)
class DriverShiftAdmin(admin.ModelAdmin):
    list_display = (
        "driver",
        "vehicle",
        "shift_date",
        "status",
        "total_km",
        "total_working_minutes",
        "needs_variance_review",
        "company",
    )
    list_filter = ("status", "needs_variance_review", "company")


@admin.register(TripLocationPing)
class TripLocationPingAdmin(admin.ModelAdmin):
    list_display = ("vehicle_id", "trip", "recorded_at", "speed_kmph", "source")
    list_filter = ("source",)


@admin.register(TripPause)
class TripPauseAdmin(admin.ModelAdmin):
    list_display = ("trip", "reason", "started_at", "ended_at")
    list_filter = ("reason",)


@admin.register(TripAnomalyAlert)
class TripAnomalyAlertAdmin(admin.ModelAdmin):
    list_display = ("trip", "shift", "alert_type", "detected_at", "acknowledged", "company")
    list_filter = ("alert_type", "acknowledged", "company")
