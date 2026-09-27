from django.db import models

from core.models import BaseModel


class Customer(BaseModel):
    """Someone who books vehicles on the web app (/book/), signed in with a
    one-time code sent to their phone. Belongs to the company whose fleet
    serves customer bookings (see BookingService.company)."""

    phone_number = models.CharField(max_length=20)
    full_name = models.CharField(max_length=150, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "phone_number"], condition=models.Q(is_deleted=False), name="unique_customer_phone_per_company"
            )
        ]

    def __str__(self):
        return self.full_name or self.phone_number
