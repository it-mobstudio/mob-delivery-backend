import django_filters

from .models import WebhookEvent


class WebhookEventFilter(django_filters.FilterSet):
    class Meta:
        model = WebhookEvent
        fields = ["order_ref"]
