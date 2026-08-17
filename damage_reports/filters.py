import django_filters

from .models import VehicleDamageReport


class DamageReportFilter(django_filters.FilterSet):
    vehicle_id = django_filters.UUIDFilter(field_name="vehicle_id")

    class Meta:
        model = VehicleDamageReport
        fields = ["status", "severity", "vehicle_id"]
