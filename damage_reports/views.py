from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAdminUser
from core.tenancy import CompanyScopedMixin
from idempotency.services import with_idempotency
from vehicles.models import Vehicle

from . import services
from .filters import DamageReportFilter
from .models import VehicleDamageReport
from .permissions import IsDriverOrAdminUser
from .serializers import (
    CreateDamageReportSerializer,
    DamageReportListSerializer,
    DamageReportSerializer,
    ResolveDamageReportSerializer,
)


@extend_schema_view(
    get=extend_schema(tags=["damage-reports"], summary="List a vehicle's damage reports", description="Lists damage reports filed against one specific vehicle."),
    post=extend_schema(
        tags=["damage-reports"],
        summary="Report vehicle damage",
        description=(
            "Files a damage report for a vehicle. A driver can only report against their own "
            "`current_vehicle_id` (403 `NOT_YOUR_VEHICLE` otherwise); an admin can report "
            "against any vehicle in their company. Supports the `Idempotency-Key` header."
        ),
    ),
)
class VehicleDamageReportListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend]
    filterset_class = DamageReportFilter

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsDriverOrAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_vehicle(self):
        return get_object_or_404(
            Vehicle.objects.all(), pk=self.kwargs["vehicle_pk"], company_id=self.request.user.company_id
        )

    def get_queryset(self):
        return VehicleDamageReport.objects.filter(vehicle=self.get_vehicle()).select_related("vehicle")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return CreateDamageReportSerializer
        return DamageReportSerializer

    def create(self, request, *args, **kwargs):
        def handler():
            serializer = CreateDamageReportSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            report = services.create_damage_report(
                vehicle_id=self.kwargs["vehicle_pk"], actor=request.user, **serializer.validated_data
            )
            return 201, DamageReportSerializer(report).data

        status_code, body = with_idempotency(request, handler)
        return Response(body, status=status_code)


@extend_schema_view(
    list=extend_schema(summary="List damage reports", description="Company-wide list of all vehicle damage reports, filterable by status/vehicle."),
    retrieve=extend_schema(summary="Get a damage report"),
)
class DamageReportViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = VehicleDamageReport.objects.select_related("vehicle").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = DamageReportFilter

    def get_permissions(self):
        if self.action == "resolve":
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "list":
            return DamageReportListSerializer
        return DamageReportSerializer

    @extend_schema(summary="Resolve a damage report", description="Admin-only: marks a damage report resolved. A second resolve attempt on an already-resolved report is rejected.")
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        serializer = ResolveDamageReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = services.resolve_damage_report(report_id=pk, actor=request.user, **serializer.validated_data)
        return Response(DamageReportSerializer(report).data)
