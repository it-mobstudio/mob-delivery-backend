import django_filters

from .models import DriverShift, TripAnomalyAlert


class AlertFilter(django_filters.FilterSet):
    class Meta:
        model = TripAnomalyAlert
        fields = ["alert_type", "acknowledged"]


class ShiftFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="shift_date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="shift_date", lookup_expr="lte")

    class Meta:
        model = DriverShift
        fields = ["status", "driver", "vehicle", "date_from", "date_to", "needs_variance_review"]
