import django_filters

from core.choices import VerificationStatus

from .models import Driver


class DriverFilter(django_filters.FilterSet):
    aadhar_status = django_filters.ChoiceFilter(field_name="kyc__aadhar_status", choices=VerificationStatus.choices)
    dl_status = django_filters.ChoiceFilter(field_name="kyc__dl_status", choices=VerificationStatus.choices)
    police_status = django_filters.ChoiceFilter(field_name="kyc__police_status", choices=VerificationStatus.choices)

    class Meta:
        model = Driver
        fields = ["account_status"]
