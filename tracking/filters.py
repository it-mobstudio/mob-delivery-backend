import django_filters

from .models import TripAnomalyAlert


class AlertFilter(django_filters.FilterSet):
    class Meta:
        model = TripAnomalyAlert
        fields = ["alert_type", "acknowledged"]
