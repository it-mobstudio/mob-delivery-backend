import django_filters

from .models import Driver


class DriverFilter(django_filters.FilterSet):
    class Meta:
        model = Driver
        fields = ["account_status", "aadhar_status", "dl_status", "police_status"]
