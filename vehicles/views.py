from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
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


class VehicleTypeViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    serializer_class = VehicleTypeSerializer
    queryset = VehicleType.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = VehicleTypeFilter

    def perform_destroy(self, instance):
        services.delete_vehicle_type(instance)


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

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        vehicle = self.get_object()
        services.disable_vehicle(vehicle)
        return Response(VehicleDetailSerializer(vehicle).data)


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
