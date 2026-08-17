import django_filters

from .models import SosAlert


class SosAlertFilter(django_filters.FilterSet):
    class Meta:
        model = SosAlert
        fields = ["status"]
