from django.db import models

from core.models import BaseModel
from vehicles.models import Vehicle


class DamageSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class DamageReportStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class ReporterType(models.TextChoices):
    DRIVER = "driver", "Driver"
    ADMIN = "admin", "Admin"


class VehicleDamageReport(BaseModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="damage_reports")
    reported_by_id = models.UUIDField()  # Driver.id or AdminUser.id, depending on reporter_type
    reporter_type = models.CharField(max_length=10, choices=ReporterType.choices)
    description = models.TextField()
    photo_url = models.URLField(null=True, blank=True)
    severity = models.CharField(max_length=10, choices=DamageSeverity.choices, default=DamageSeverity.LOW)
    status = models.CharField(max_length=20, choices=DamageReportStatus.choices, default=DamageReportStatus.OPEN)
    resolution_note = models.TextField(null=True, blank=True)
    resolved_by = models.UUIDField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_severity_display()} damage — {self.vehicle_id}"
