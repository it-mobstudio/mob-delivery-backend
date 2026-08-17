from django.urls import path

from .views import WebhookEventDetailView, WebhookEventListView, WebhookRetryView

urlpatterns = [
    path("webhooks/deliveries", WebhookEventListView.as_view(), name="webhook-event-list"),
    path("webhooks/deliveries/<uuid:pk>", WebhookEventDetailView.as_view(), name="webhook-event-detail"),
    path("webhooks/retry/<uuid:pk>", WebhookRetryView.as_view(), name="webhook-event-retry"),
]
