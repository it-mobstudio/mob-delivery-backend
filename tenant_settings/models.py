from django.db import models

from core.models import BaseModel


class TenantSetting(BaseModel):
    key = models.CharField(max_length=100)  # e.g. "assignment_window_minutes"
    value = models.CharField(max_length=255)  # stored as string, cast at read time

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(fields=["company_id", "key"], name="unique_tenant_setting")
        ]

    def __str__(self):
        return f"{self.key}={self.value}"
