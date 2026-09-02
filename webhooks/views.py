from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser

from . import services
from .filters import WebhookEventFilter
from .models import WebhookEvent
from .serializers import WebhookEventDetailSerializer, WebhookEventSerializer


@extend_schema(
    tags=["Admin: Webhooks"],
    summary="List webhook deliveries",
    description=(
        "Lists outbound webhook events for this company's registered ApiClient endpoints "
        "(order.status_changed, order.cancelled, order.trip_cancelled, "
        "order.delivery_address_updated, ...), filterable by `order_ref`. Each event carries "
        "its current delivery status (pending/delivered/failed) and retry attempt count."
    ),
)
class WebhookEventListView(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = WebhookEventSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = WebhookEventFilter

    def get_queryset(self):
        return WebhookEvent.objects.filter(company_id=self.request.user.company_id)


@extend_schema(
    tags=["Admin: Webhooks"],
    summary="Get a webhook delivery's detail",
    description="Returns one webhook event plus its full delivery log history (every attempt, response status, and timestamp).",
)
class WebhookEventDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, pk=None):
        event = get_object_or_404(
            WebhookEvent.objects.prefetch_related("delivery_logs"), pk=pk, company_id=request.user.company_id
        )
        return Response(WebhookEventDetailSerializer(event).data)


@extend_schema(
    tags=["Admin: Webhooks"],
    summary="Retry a failed webhook delivery",
    description=(
        "Manually resets a `failed` webhook event back to pending for immediate redelivery "
        "(bypassing the normal exponential-backoff schedule, which stops retrying after 5 "
        "attempts). Rejected if the event isn't currently in `failed` status."
    ),
)
class WebhookRetryView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk=None):
        event = services.retry_webhook_event(event_id=pk, actor=request.user)
        return Response(WebhookEventSerializer(event).data)
