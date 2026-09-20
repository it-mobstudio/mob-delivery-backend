import django_filters

from .models import Trip


class TripFilter(django_filters.FilterSet):
    class Meta:
        model = Trip
        fields = ["status", "vehicle_type", "driver"]
