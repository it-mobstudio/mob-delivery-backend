import django_filters

from .models import Vehicle, VehicleType


class VehicleTypeFilter(django_filters.FilterSet):
    class Meta:
        model = VehicleType
        fields = ["category", "status"]


class VehicleFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(field_name="vehicle_type__category")

    class Meta:
        model = Vehicle
        fields = ["category", "status"]
