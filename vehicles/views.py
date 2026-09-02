from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, generics, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAdminUser
from core.tenancy import CompanyScopedMixin
from drivers.permissions import IsDriverUser

from . import services
from .filters import VehicleFilter, VehicleTypeFilter
from .models import Vehicle, VehicleDocument, VehicleType
from .serializers import (
    DriverVehicleSerializer,
    VehicleDetailSerializer,
    VehicleDocumentSerializer,
    VehicleListSerializer,
    VehicleSerializer,
    VehicleTypeSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=["Admin: Vehicle Types"], summary="List vehicle types", description="Lists this company's vehicle types (Bike, Auto, Tempo, Mini Van, Truck, ...)."),
    retrieve=extend_schema(tags=["Admin: Vehicle Types"], summary="Get a vehicle type"),
    create=extend_schema(tags=["Admin: Vehicle Types"], summary="Create a vehicle type"),
    update=extend_schema(tags=["Admin: Vehicle Types"], summary="Replace a vehicle type"),
    partial_update=extend_schema(tags=["Admin: Vehicle Types"], summary="Update a vehicle type"),
    destroy=extend_schema(
        tags=["Admin: Vehicle Types"],
        summary="Delete a vehicle type",
        description=(
            "Soft-deletes the vehicle type. Refuses with 409 `VEHICLE_TYPE_IN_USE` if any "
            "non-deleted Vehicle still references it, instead of letting a raw DB integrity "
            "error surface."
        ),
    ),
)
class VehicleTypeViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    serializer_class = VehicleTypeSerializer
    queryset = VehicleType.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = VehicleTypeFilter
    # Every update spec here is PATCH (partial) — PUT (full replacement) was
    # only ever reachable as a side effect of ModelViewSet's defaults, never
    # an intended contract, and risks nulling out fields a client omits.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def perform_destroy(self, instance):
        services.delete_vehicle_type(instance)


@extend_schema_view(
    list=extend_schema(tags=["Admin: Vehicles"], summary="List vehicles", description="Lists this company's vehicles, searchable by registration number."),
    retrieve=extend_schema(tags=["Admin: Vehicles"], summary="Get a vehicle", description="Returns full vehicle detail including its uploaded documents."),
    create=extend_schema(tags=["Admin: Vehicles"], summary="Register a vehicle", description="Registers a new vehicle. Registration number must be unique within the company."),
    update=extend_schema(tags=["Admin: Vehicles"], summary="Replace a vehicle"),
    partial_update=extend_schema(tags=["Admin: Vehicles"], summary="Update a vehicle"),
    destroy=extend_schema(
        tags=["Admin: Vehicles"],
        summary="Delete a vehicle",
        description=(
            "Soft-deletes and deactivates the vehicle — same effect as the `disable` action "
            "below, including the 409 `VEHICLE_HAS_ACTIVE_TRIP` refusal. A raw hard delete is "
            "never performed via the API."
        ),
    ),
)
class VehicleViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = Vehicle.objects.select_related("vehicle_type").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = VehicleFilter
    search_fields = ["registration_number"]
    # Every update spec here is PATCH (partial) — PUT (full replacement) was
    # only ever reachable as a side effect of ModelViewSet's defaults, never
    # an intended contract, and risks nulling out fields a client omits.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def perform_destroy(self, instance):
        # Model.delete() bypasses SoftDeleteQuerySet.delete() (that override
        # only intercepts queryset-level bulk deletes, not instance.delete())
        # — routing through the same domain service as `disable` below keeps
        # DELETE from hard-deleting the row and skipping the active-trip check.
        services.disable_vehicle(instance)

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
        tags=["Admin: Vehicles"],
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
    list=extend_schema(tags=["Admin: Vehicle Documents"], summary="List a vehicle's documents", description="Lists uploaded documents (insurance, fitness, RC, purchase, other) for one vehicle."),
    create=extend_schema(tags=["Admin: Vehicle Documents"], summary="Upload a vehicle document", description="Attaches a document to a vehicle. Insurance/fitness documents should include an expiry_date so the expiry-alert job can track them."),
    partial_update=extend_schema(tags=["Admin: Vehicle Documents"], summary="Update a vehicle document"),
)
class VehicleDocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
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


@extend_schema(
    tags=["Driver: Vehicle"],
    summary="Get the logged-in driver's currently-assigned vehicle",
    description=(
        "Returns full detail (including vehicle type) for the vehicle currently assigned to the "
        "authenticated driver. Driver-only — an AdminUser/ApiClient token is rejected. Returns "
        "404 `NO_VEHICLE_ASSIGNED` if the driver has no vehicle assigned right now."
    ),
    responses={200: DriverVehicleSerializer},
)
class DriverCurrentVehicleView(generics.RetrieveAPIView):
    permission_classes = [IsDriverUser]
    serializer_class = DriverVehicleSerializer

    def get_object(self):
        return services.get_driver_current_vehicle(self.request.user)
