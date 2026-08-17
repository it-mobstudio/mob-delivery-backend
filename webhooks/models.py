import uuid

from django.db import models

from core.models import BaseModel


class WebhookEventStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


class WebhookEvent(BaseModel):
    event_type = models.CharField(max_length=50)  # "order.status_changed", "order.cancelled", etc.
    order_ref = models.CharField(max_length=100)
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=WebhookEventStatus.choices, default=WebhookEventStatus.PENDING)
    attempt_count = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.event_type} — {self.order_ref}"


class WebhookDeliveryLog(models.Model):
    # Plain Model, not BaseModel — append-only delivery history, no soft delete needed.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    webhook_event = models.ForeignKey(WebhookEvent, on_delete=models.CASCADE, related_name="delivery_logs")
    attempt_number = models.IntegerField()
    response_status_code = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(null=True, blank=True)  # truncated before storing
    error_message = models.TextField(null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-attempted_at"]

    def __str__(self):
        return f"Attempt {self.attempt_number} — {self.webhook_event_id}"
