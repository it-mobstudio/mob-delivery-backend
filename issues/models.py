from django.db import models

from core.models import BaseModel
from trips.models import Trip, TripStop


class IssueType(models.TextChoices):
    PICKUP = "pickup", "Pickup"
    TRANSIT = "transit", "Transit"
    UNLOADING = "unloading", "Unloading"
    TRAFFIC_PENALTY = "traffic_penalty", "Traffic Penalty"


class IssueSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class IssueStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class TripIssue(BaseModel):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="issues")
    trip_stop = models.ForeignKey(TripStop, null=True, blank=True, on_delete=models.SET_NULL, related_name="issues")
    issue_type = models.CharField(max_length=20, choices=IssueType.choices)
    severity = models.CharField(max_length=10, choices=IssueSeverity.choices, default=IssueSeverity.MEDIUM)
    note = models.TextField()
    photo_url = models.URLField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=IssueStatus.choices, default=IssueStatus.OPEN)
    resolution_note = models.TextField(null=True, blank=True)
    resolved_by = models.UUIDField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_issue_type_display()} — trip {self.trip_id}"
