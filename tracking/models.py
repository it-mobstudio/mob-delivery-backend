import uuid

from django.db import models

from core.models import BaseModel
from drivers.models import Driver
from trips.models import Trip
from vehicles.models import Vehicle


class ShiftStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ENDED = "ended", "Ended"


class DriverShift(BaseModel):
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="shifts")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="shifts")
    shift_date = models.DateField()
    start_odometer = models.DecimalField(max_digits=10, decimal_places=2)
    end_odometer = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=ShiftStatus.choices, default=ShiftStatus.ACTIVE)
    total_km = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    total_working_minutes = models.IntegerField(null=True, blank=True)

    # Point 24 — required to end the shift, not optional
    cleanliness_photo_url = models.URLField(null=True, blank=True)
    charging_plugged_photo_url = models.URLField(null=True, blank=True)

    # KM variance reconciliation (see services.end_shift) — total_km stays
    # the odometer-based figure of record; these compare it against the
    # independently GPS-derived distance from this shift's TripLocationPing
    # rows, purely as a review signal. needs_variance_review never blocks
    # closing the shift, it just flags it for admin follow-up.
    gps_distance_km = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    variance_km = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    needs_variance_review = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["driver"],
                condition=models.Q(status=ShiftStatus.ACTIVE),
                name="unique_active_shift_per_driver",
            )
        ]

    def __str__(self):
        return f"{self.driver_id} — {self.shift_date}"


class LocationSource(models.TextChoices):
    DRIVER_PHONE = "driver_phone", "Driver Phone"
    HARDWARE_GPS = "hardware_gps", "Hardware GPS"  # unused for now — no vendor chosen, field exists for later


class TripLocationPing(models.Model):
    # Plain Model, NOT BaseModel — high-volume append-only telemetry, no
    # soft delete or audit columns needed at this write volume.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_id = models.UUIDField()
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="location_pings")
    vehicle_id = models.UUIDField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    speed_kmph = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=20, choices=LocationSource.choices, default=LocationSource.DRIVER_PHONE)
    recorded_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    # Speed-sanity check (see services.record_location_ping): distance from
    # the previous ping for the same vehicle, in km. Left at 0 (not the raw
    # haversine distance) when the implied speed exceeds
    # MAX_REASONABLE_SPEED_KPH, so one implausible GPS jump can't pollute a
    # GPS-derived KM total — DriverShift.total_km itself stays odometer-based
    # (start/end odometer, more accurate than GPS) and is untouched by this;
    # this field is for future KM-based dashboard metrics that want a
    # GPS-derived distance. The ping itself is still stored either way, for
    # debugging device issues.
    distance_from_previous_km = models.DecimalField(max_digits=8, decimal_places=3, default=0)

    class Meta:
        indexes = [models.Index(fields=["vehicle_id", "-recorded_at"])]

    def __str__(self):
        return f"{self.vehicle_id} @ {self.recorded_at}"


class PauseReason(models.TextChoices):
    LUNCH = "lunch", "Lunch"
    RECHARGE = "recharge", "Recharge"
    DRIVER_BREAK = "driver_break", "Driver Break"


class TripPause(BaseModel):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="pauses")
    reason = models.CharField(max_length=20, choices=PauseReason.choices)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["trip"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_open_pause_per_trip",
            )
        ]

    def __str__(self):
        return f"{self.get_reason_display()} — trip {self.trip_id}"


class AnomalyType(models.TextChoices):
    STATIONARY_TOO_LONG = "stationary_too_long", "Stationary Too Long"
    WRONG_DIRECTION = "wrong_direction", "Wrong Direction"
    GPS_OFFLINE = "gps_offline", "GPS Offline"


class TripAnomalyAlert(BaseModel):
    # Nullable — a gps_offline alert raised for a driver on an active shift
    # but with no active trip yet has no trip to attach to (see
    # tracking.tasks.detect_offline_vehicles), so it's attached to `shift`
    # instead. Every other alert type is always trip-scoped.
    trip = models.ForeignKey(Trip, null=True, blank=True, on_delete=models.CASCADE, related_name="anomaly_alerts")
    shift = models.ForeignKey(
        "DriverShift", null=True, blank=True, on_delete=models.CASCADE, related_name="anomaly_alerts"
    )
    vehicle_id = models.UUIDField()
    alert_type = models.CharField(max_length=30, choices=AnomalyType.choices)
    details = models.TextField(null=True, blank=True)
    detected_at = models.DateTimeField()
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-detected_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["trip", "alert_type"],
                condition=models.Q(acknowledged=False),
                name="unique_open_alert_per_trip_and_type",
            ),
            models.UniqueConstraint(
                fields=["shift", "alert_type"],
                condition=models.Q(acknowledged=False),
                name="unique_open_alert_per_shift_and_type",
            ),
        ]

    def __str__(self):
        return f"{self.get_alert_type_display()} — trip {self.trip_id}"
