from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.tenancy import CompanyScopedMixin
from idempotency.services import with_idempotency

from . import services
from .filters import TripFilter
from .models import StopType, Trip, TripStop
from .serializers import (
    AssignmentCandidateSerializer,
    AssignmentCandidatesRequestSerializer,
    AssignVehicleSerializer,
    CancelSerializer,
    CompleteStopSerializer,
    IntakeOrderSerializer,
    ReassignVehicleSerializer,
    TripDetailSerializer,
    TripListSerializer,
    TripStopSerializer,
    UpdateDeliveryAddressSerializer,
)


@extend_schema(
    summary="Submit an order for pickup/delivery",
    description=(
        "Accepts a client order into the trip pipeline. If `parent_order_ref` is given and "
        "another order sharing it already has an open trip (still collecting pickups), this "
        "suborder's pickup stop is added to that SAME trip instead of creating a new one — "
        "multiple suborders under one parent are staggered onto a single trip with one shared "
        "drop stop. Calling this again with an `order_ref` already seen is idempotent: it "
        "returns the original trip/stop ids without creating anything new."
    ),
)
class IntakeOrderView(APIView):
    def post(self, request):
        serializer = IntakeOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.intake_order(
            company_id=request.user.company_id,
            order_ref=data["order_ref"],
            parent_order_ref=data.get("parent_order_ref"),
            pickup=data["pickup"],
            delivery=data["delivery"],
            weight_kg=data["weight_kg"],
            actor=request.user,
        )
        return Response(result)


@extend_schema(
    summary="Cancel a single order before pickup",
    description=(
        "Pre-pickup cancellation of one suborder (Scenario A). Rejected with 409 "
        "`CANNOT_CANCEL_AFTER_PICKUP` once its pickup stop is already completed — use the "
        "Issues module for that case instead. Open to any authenticated principal (admin or "
        "an external client backend), unlike whole-trip cancellation which is admin-only."
    ),
)
class OrderCancelView(APIView):
    def post(self, request, order_ref=None):
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.cancel_order_stop(order_ref=order_ref, actor=request.user, **serializer.validated_data)
        return Response(result)


@extend_schema(
    summary="List orders grouped by parent order",
    description="Groups every suborder sharing a `parent_order_ref` together with their shared trip's status — the staggered-acceptance grouping made visible for the Admin Panel.",
)
class GroupedOrdersView(APIView):
    def get(self, request):
        stops = (
            TripStop.objects.filter(
                company_id=request.user.company_id,
                stop_type=StopType.PICKUP,
                parent_order_ref__isnull=False,
            )
            .exclude(parent_order_ref="")
            .select_related("trip")
            .order_by("parent_order_ref", "sequence_no")
        )

        groups = {}
        for stop in stops:
            group = groups.setdefault(
                stop.parent_order_ref,
                {
                    "parent_order_ref": stop.parent_order_ref,
                    "trip_id": stop.trip_id,
                    "trip_status": stop.trip.status,
                    "suborders": [],
                },
            )
            group["suborders"].append(
                {"order_ref": stop.order_ref, "stop_id": stop.id, "status": stop.status}
            )

        return Response(list(groups.values()))


@extend_schema(
    summary="Find vehicle/driver candidates for assignment",
    description=(
        "Returns available vehicles (active, unassigned, sufficient capacity) paired with "
        "drivers eligible to drive them (fully verified, active, DL not expired, and whose "
        "`dl_allowed_categories` include the vehicle's category). Accepts either `trip_id` or "
        "`order_refs` to determine the required weight capacity."
    ),
)
class AssignmentCandidatesView(APIView):
    def post(self, request):
        serializer = AssignmentCandidatesRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        candidates = services.get_assignment_candidates(
            company_id=request.user.company_id,
            trip_id=data.get("trip_id"),
            order_refs=data.get("order_refs"),
            within_minutes=data.get("within_minutes"),
        )
        return Response(AssignmentCandidateSerializer(candidates, many=True).data)


@extend_schema_view(
    list=extend_schema(summary="List trips", description="Lists this company's trips with their current status, filterable by status/vehicle/driver."),
    retrieve=extend_schema(summary="Get a trip", description="Returns full trip detail: all stops, current vehicle/driver, and reassignment history."),
)
class TripViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Trip.objects.select_related("vehicle", "driver").prefetch_related("stops", "vehicle_history")
    filter_backends = [DjangoFilterBackend]
    filterset_class = TripFilter

    def get_serializer_class(self):
        if self.action == "retrieve":
            return TripDetailSerializer
        return TripListSerializer

    def get_permissions(self):
        if self.action == "cancel":
            # Scenario B — an external client must never be able to cancel a
            # trip that may be carrying another company's orders combined
            # onto the same vehicle (point 20).
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

    @extend_schema(
        summary="Lock a trip's pickups",
        description="Moves a trip from collecting_pickups to pickups_locked once at least one pickup stop is completed — no further suborders can attach to it after this.",
    )
    @action(detail=True, methods=["post"], url_path="lock-pickups")
    def lock_pickups(self, request, pk=None):
        trip = services.lock_pickups(trip_id=pk, actor=request.user)
        return Response(TripDetailSerializer(trip).data)

    @extend_schema(
        summary="Assign a vehicle and driver to a trip",
        description=(
            "First assignment for a trip. Validates the driver is fully eligible, the "
            "vehicle's category matches the driver's `dl_allowed_categories` (409 "
            "`VEHICLE_TYPE_NOT_PERMITTED` if not), and the vehicle's capacity covers the "
            "trip's total weight. Pushes a notification to the assigned driver."
        ),
    )
    @action(detail=True, methods=["post"], url_path="assign-vehicle")
    def assign_vehicle(self, request, pk=None):
        serializer = AssignVehicleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = services.assign_vehicle(trip_id=pk, actor=request.user, **serializer.validated_data)
        return Response(TripDetailSerializer(trip).data)

    @extend_schema(
        summary="Reassign a trip to a different vehicle/driver",
        description=(
            "Swaps a trip's vehicle and/or driver mid-flight (e.g. vehicle breakdown). Same "
            "eligibility/type-match/capacity checks as initial assignment. Writes a "
            "TripVehicleHistory row recording both the old and new vehicle/driver, and "
            "notifies the outgoing and incoming drivers."
        ),
    )
    @action(detail=True, methods=["post"], url_path="reassign-vehicle")
    def reassign_vehicle(self, request, pk=None):
        serializer = ReassignVehicleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = services.reassign_vehicle(trip_id=pk, actor=request.user, **serializer.validated_data)
        return Response(TripDetailSerializer(trip).data)

    @extend_schema(
        summary="Cancel an entire trip",
        description=(
            "Whole-trip cancellation (Scenario B) — admin-only, an ApiClient token is rejected "
            "with 403. Any pickup already completed with no matching completed drop is flagged "
            "as a TripIssue for manual follow-up rather than silently lost."
        ),
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = services.cancel_trip(trip_id=pk, actor=request.user, **serializer.validated_data)
        return Response(TripDetailSerializer(trip).data)


@extend_schema(
    summary="Complete a trip stop",
    description=(
        "Marks a pickup/drop stop completed. A proof photo is required for the trip's very "
        "first stop overall and its very last remaining stop (422 `PROOF_PHOTO_REQUIRED` "
        "otherwise); stops in between don't require one. Completing the trip's last stop marks "
        "the whole trip delivered. Soft-flags (never blocks) a `location_mismatch` if the "
        "vehicle's last GPS ping was further than the tenant's geofence threshold from the "
        "stop's coordinates. Supports the `Idempotency-Key` header — retried with the same key "
        "replays the original response instead of re-processing."
    ),
)
class TripStopCompleteView(APIView):
    def post(self, request, pk=None, stop_pk=None):
        def handler():
            serializer = CompleteStopSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            stop = services.complete_stop(
                stop_id=stop_pk,
                proof_photo_url=serializer.validated_data.get("proof_photo_url"),
                actor=request.user,
            )
            return 200, TripStopSerializer(stop).data

        status_code, body = with_idempotency(request, handler)
        return Response(body, status=status_code)


@extend_schema(
    summary="Update a stop's delivery address",
    description="Changes a stop's address/coordinates mid-trip, logging the old and new values to an AddressChangeLog and notifying the assigned driver.",
)
class TripStopDeliveryAddressView(APIView):
    def patch(self, request, pk=None, stop_pk=None):
        serializer = UpdateDeliveryAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stop = services.update_delivery_address(stop_id=stop_pk, actor=request.user, **serializer.validated_data)
        return Response(TripStopSerializer(stop).data)
