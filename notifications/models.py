from django.db import models

from core.models import BaseModel
from drivers.models import Driver


class DevicePlatform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"


class DriverDevice(BaseModel):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="devices")
    fcm_token = models.CharField(max_length=255)
    platform = models.CharField(max_length=10, choices=DevicePlatform.choices)
    last_active_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["driver", "fcm_token"], name="unique_driver_device_token")
        ]

    def __str__(self):
        return f"{self.driver_id} — {self.platform}"
