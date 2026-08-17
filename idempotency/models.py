import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class IdempotencyRecord(models.Model):
    # Plain Model, not BaseModel — disposable replay data (24h TTL via a
    # periodic cleanup task, see tasks.py), not business history.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=255)
    driver_id = models.UUIDField()
    # encoder=DjangoJSONEncoder is required, not optional — plain JSONField
    # uses stdlib json.dumps by default, which can't serialize the
    # UUID/Decimal/datetime values that come straight out of a DRF
    # serializer's .data (e.g. SosAlertSerializer(alert).data).
    response_body = models.JSONField(encoder=DjangoJSONEncoder)
    status_code = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["driver_id", "key"], name="unique_idempotency_key_per_driver")
        ]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        return f"{self.driver_id} — {self.key}"
