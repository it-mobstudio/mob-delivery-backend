from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from drivers.permissions import IsDriverUser
from idempotency.services import with_idempotency

from . import services
from .filters import SosAlertFilter
from .models import SosAlert
from .serializers import ResolveSosSerializer, SosAlertSerializer, TriggerSosSerializer


@extend_schema_view(
    get=extend_schema(summary="List SOS alerts", description="Admin Panel SOS log — every SOS alert raised for this company, filterable by status."),
    post=extend_schema(
        summary="Trigger an SOS alert",
        description=(
            "Driver-only panic button. Deliberately bypasses every other eligibility rule in "
            "the system — a driver with a locked or disabled account can still trigger this. "
            "No validation beyond a basic lat/lng range check; nothing here should ever block "
            "an emergency signal. Broadcast live to connected admins over WebSocket. Supports "
            "the `Idempotency-Key` header."
        ),
    ),
)
class SosAlertListCreateView(generics.ListCreateAPIView):
    serializer_class = SosAlertSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = SosAlertFilter

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsDriverUser()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return SosAlert.objects.filter(company_id=self.request.user.company_id)

    def create(self, request, *args, **kwargs):
        def handler():
            serializer = TriggerSosSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            alert = services.trigger_sos(driver=request.user, **serializer.validated_data)
            return 201, SosAlertSerializer(alert).data

        status_code, body = with_idempotency(request, handler)
        return Response(body, status=status_code)


@extend_schema(
    summary="Acknowledge an SOS alert",
    description="Admin-only: marks an SOS alert acknowledged (seen, being handled) and notifies the driver.",
)
class SosAcknowledgeView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk=None):
        alert = services.acknowledge_sos(alert_id=pk, actor=request.user)
        return Response(SosAlertSerializer(alert).data)


@extend_schema(
    summary="Resolve an SOS alert",
    description="Admin-only: closes out an SOS alert with a resolution note and notifies the driver.",
)
class SosResolveView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk=None):
        serializer = ResolveSosSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        alert = services.resolve_sos(alert_id=pk, actor=request.user, **serializer.validated_data)
        return Response(SosAlertSerializer(alert).data)
