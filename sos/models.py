from django.db import models

from core.models import BaseModel
from drivers.models import Driver
from trips.models import Trip


class SosStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    RESOLVED = "resolved", "Resolved"


class SosAlert(BaseModel):
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="sos_alerts")
    trip = models.ForeignKey(Trip, null=True, blank=True, on_delete=models.SET_NULL, related_name="sos_alerts")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    triggered_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=SosStatus.choices, default=SosStatus.ACTIVE)
    acknowledged_by = models.UUIDField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.UUIDField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-triggered_at"]

    def __str__(self):
        return f"SOS — {self.driver_id} @ {self.triggered_at}"
