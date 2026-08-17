import hashlib
import secrets
import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from core.models import AllObjectsManager, SoftDeleteManager, SoftDeleteModel, TimeStampedUUIDModel


class CompanyStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    INACTIVE = "inactive", "Inactive"


class Company(TimeStampedUUIDModel, SoftDeleteModel):
    """The tenant root. Every company-scoped model (core.BaseModel) points
    back to one of these — it does not point to itself."""

    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=CompanyStatus.choices, default=CompanyStatus.ACTIVE)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class AdminRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    STAFF = "staff", "Staff"


class AdminUserManager(BaseUserManager):
    def create_user(self, email, company, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        if not company:
            raise ValueError("Company is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, company=company, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, company=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", AdminRole.OWNER)
        if company is None:
            # `manage.py createsuperuser` has no way to prompt for a
            # company (it only asks about USERNAME_FIELD/REQUIRED_FIELDS),
            # so a platform-level superuser gets parked in a default company
            # rather than the command failing outright.
            company, _ = Company.objects.get_or_create(name="Platform")
        return self.create_user(email, company, password, **extra_fields)


class AdminUser(TimeStampedUUIDModel, SoftDeleteModel, AbstractBaseUser, PermissionsMixin):
    """A human user of the Admin Panel, scoped to one company."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="admin_users")
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=20, choices=AdminRole.choices, default=AdminRole.ADMIN)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = AdminUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class ApiClientStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class ApiClient(TimeStampedUUIDModel, SoftDeleteModel):
    """A machine principal (partner integration, mobile backend, etc.)
    authenticated via client_id/client_secret exchanged for a JWT — see
    accounts.authentication.JWTMultiPrincipalAuthentication.
    """

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="api_clients")
    name = models.CharField(max_length=100)
    client_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client_secret_hash = models.CharField(max_length=128)
    status = models.CharField(max_length=20, choices=ApiClientStatus.choices, default=ApiClientStatus.ACTIVE)
    webhook_url = models.URLField(null=True, blank=True)
    webhook_signing_secret = models.CharField(max_length=100, null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    # Duck-typed to satisfy DRF/simplejwt's expectations of a "user" object.
    is_authenticated = True
    is_anonymous = False

    @property
    def is_active(self):
        return self.status == ApiClientStatus.ACTIVE and not self.is_deleted

    @staticmethod
    def hash_secret(raw_secret):
        return hashlib.sha256(raw_secret.encode()).hexdigest()

    def set_secret(self, raw_secret):
        self.client_secret_hash = self.hash_secret(raw_secret)

    def check_secret(self, raw_secret):
        return secrets.compare_digest(self.client_secret_hash, self.hash_secret(raw_secret))

    def __str__(self):
        return self.name
