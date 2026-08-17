"""Shared factory_boy factories for the pytest-django suite. Only the
handful of "foundation" models that nearly every app's tests need to stand
up (Company, AdminUser, ApiClient, Driver, VehicleType, Vehicle) live here —
app-specific models stay in that app's own tests.py, hand-rolled, same as
before this file existed.
"""

from decimal import Decimal

import factory

from accounts.models import AdminRole, AdminUser, ApiClient, Company
from drivers.models import Driver
from vehicles.models import Vehicle, VehicleCategory, VehicleType


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Company

    name = factory.Sequence(lambda n: f"Test Co {n}")


class AdminUserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AdminUser

    company = factory.SubFactory(CompanyFactory)
    email = factory.Sequence(lambda n: f"admin{n}@test.invalid")
    role = AdminRole.ADMIN

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        password = kwargs.pop("password", "pass12345")
        return model_class.objects.create_user(password=password, *args, **kwargs)


class ApiClientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ApiClient

    company = factory.SubFactory(CompanyFactory)
    name = factory.Sequence(lambda n: f"Api Client {n}")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        secret = kwargs.pop("secret", "dummy-secret")
        instance = model_class(*args, **kwargs)
        instance.set_secret(secret)
        instance.save()
        return instance


class DriverFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Driver

    company = factory.SubFactory(CompanyFactory)
    full_name = factory.Sequence(lambda n: f"Driver {n}")
    phone_number = factory.Sequence(lambda n: f"+9198765{n:05d}")
    emergency_contact_name = "Emergency Contact"
    emergency_contact_phone = "+919876500000"


class VehicleTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VehicleType

    company = factory.SubFactory(CompanyFactory)
    name = factory.Sequence(lambda n: f"Vehicle Type {n}")
    category = VehicleCategory.FOUR_WHEELER
    default_capacity_kg = Decimal("500.00")


class VehicleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Vehicle

    company = factory.SelfAttribute("vehicle_type.company")
    vehicle_type = factory.SubFactory(VehicleTypeFactory)
    registration_number = factory.Sequence(lambda n: f"KA01AB{n:04d}")
    capacity_kg = Decimal("500.00")
