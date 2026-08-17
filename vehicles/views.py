from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.tenancy import CompanyScopedMixin

from . import services
from .filters import VehicleFilter, VehicleTypeFilter
from .models import Vehicle, VehicleDocument, VehicleType
from .serializers import (
    VehicleDetailSerializer,
    VehicleDocumentSerializer,
    VehicleListSerializer,
    VehicleSerializer,
    VehicleTypeSerializer,
)


@extend_schema_view(
    list=extend_schema(summary="List vehicle types", description="Lists this company's vehicle types (Bike, Auto, Tempo, Mini Van, Truck, ...)."),
    retrieve=extend_schema(summary="Get a vehicle type"),
    create=extend_schema(summary="Create a vehicle type"),
    update=extend_schema(summary="Replace a vehicle type"),
    partial_update=extend_schema(summary="Update a vehicle type"),
    destroy=extend_schema(
        summary="Delete a vehicle type",
        description=(
            "Soft-deletes the vehicle type. Refuses with 409 `VEHICLE_TYPE_IN_USE` if any "
            "non-deleted Vehicle still references it, instead of letting a raw DB integrity "
            "error surface."
        ),
    ),
)
class VehicleTypeViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    serializer_class = VehicleTypeSerializer
    queryset = VehicleType.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = VehicleTypeFilter

    def perform_destroy(self, instance):
        services.delete_vehicle_type(instance)


@extend_schema_view(
    list=extend_schema(summary="List vehicles", description="Lists this company's vehicles, searchable by registration number."),
    retrieve=extend_schema(summary="Get a vehicle", description="Returns full vehicle detail including its uploaded documents."),
    create=extend_schema(summary="Register a vehicle", description="Registers a new vehicle. Registration number must be unique within the company."),
    update=extend_schema(summary="Replace a vehicle"),
    partial_update=extend_schema(summary="Update a vehicle"),
    destroy=extend_schema(summary="Delete a vehicle"),
)
class VehicleViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    queryset = Vehicle.objects.select_related("vehicle_type").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = VehicleFilter
    search_fields = ["registration_number"]

    def get_serializer_class(self):
        if self.action == "list":
            return VehicleListSerializer
        if self.action == "retrieve":
            return VehicleDetailSerializer
        return VehicleSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "retrieve":
            qs = qs.prefetch_related("documents")
        return qs

    @extend_schema(
        summary="Disable a vehicle",
        description=(
            "Marks the vehicle disabled and soft-deletes it. Refuses with 409 "
            "`VEHICLE_HAS_ACTIVE_TRIP` if it currently has a trip in progress."
        ),
    )
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        vehicle = self.get_object()
        services.disable_vehicle(vehicle)
        return Response(VehicleDetailSerializer(vehicle).data)


@extend_schema_view(
    list=extend_schema(summary="List a vehicle's documents", description="Lists uploaded documents (insurance, fitness, RC, purchase, other) for one vehicle."),
    create=extend_schema(summary="Upload a vehicle document", description="Attaches a document to a vehicle. Insurance/fitness documents should include an expiry_date so the expiry-alert job can track them."),
    partial_update=extend_schema(summary="Update a vehicle document"),
)
class VehicleDocumentViewSet(viewsets.ModelViewSet):
    serializer_class = VehicleDocumentSerializer
    http_method_names = ["get", "post", "patch"]

    def get_vehicle(self):
        return get_object_or_404(
            Vehicle.objects.all(),
            pk=self.kwargs["vehicle_pk"],
            company_id=self.request.user.company_id,
        )

    def get_queryset(self):
        return VehicleDocument.objects.filter(vehicle=self.get_vehicle())

    def perform_create(self, serializer):
        vehicle = self.get_vehicle()
        serializer.save(vehicle=vehicle, company=vehicle.company)
