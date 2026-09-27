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


class SavedPlace(BaseModel):
    """An address a customer keeps for one-tap booking — Home, Work, or any
    other place they name ("Shop", "Mom's")."""

    class Kind(models.TextChoices):
        HOME = "home", "Home"
        WORK = "work", "Work"
        OTHER = "other", "Other"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="saved_places")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    label = models.CharField(max_length=40, blank=True, default="")
    address = models.CharField(max_length=255)
    details = models.CharField(max_length=120, blank=True, default="")  # flat / floor / landmark
    lat = models.DecimalField(max_digits=9, decimal_places=6)
    lng = models.DecimalField(max_digits=9, decimal_places=6)
    contact_name = models.CharField(max_length=150, blank=True, default="")
    contact_phone = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ["kind", "-updated_at"]

    def __str__(self):
        return self.name

    @property
    def name(self):
        return self.label or self.get_kind_display()

    def as_json(self):
        return {"id": str(self.pk), "kind": self.kind, "name": self.name, "label": self.label,
                "address": self.address, "details": self.details, "lat": float(self.lat), "lng": float(self.lng),
                "contact_name": self.contact_name, "contact_phone": self.contact_phone}
