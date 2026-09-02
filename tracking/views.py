from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.exceptions import DomainError
from core.tenancy import CompanyScopedMixin
from damage_reports.permissions import IsDriverOrAdminUser
from drivers.permissions import IsDriverUser
from idempotency.services import with_idempotency

from . import services
from .filters import AlertFilter, ShiftFilter
from .models import DriverShift, TripAnomalyAlert
from .serializers import (
    DriverShiftAdminSerializer,
    DriverShiftSerializer,
    EndShiftResultSerializer,
    EndShiftSerializer,
    LocationPingSerializer,
    PauseSerializer,
    StartShiftSerializer,
    TimeSummarySerializer,
    TripAnomalyAlertSerializer,
    TripPauseSerializer,
)


@extend_schema(
    tags=["Driver: Tracking"],
    summary="Record a live GPS ping for a trip's vehicle",
    description=(
        "Appends one location ping (lat/lng/timestamp/source) for the trip's currently "
        "assigned vehicle. Feeds the live-tracking WebSocket broadcast and the anomaly "
        "detection jobs (stationary/wrong-direction). Supports the `Idempotency-Key` header. "
        "Pings older than `LOCATION_PING_RETENTION_DAYS` are purged weekly. Driver-only."
    ),
    request=LocationPingSerializer,
    responses={200: OpenApiTypes.OBJECT},
)
class LocationPingView(APIView):
    permission_classes = [IsDriverUser]

    def post(self, request, pk=None):
        def handler():
            serializer = LocationPingSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            ping = services.record_location_ping(trip_id=pk, actor=request.user, **serializer.validated_data)
            return 200, {"id": str(ping.id), "recorded_at": ping.recorded_at.isoformat()}

        status_code, body = with_idempotency(request, handler)
        return Response(body, status=status_code)


@extend_schema(
    tags=["Driver: Tracking"],
    summary="Pause a trip",
    description="Driver-only: opens a TripPause window (e.g. driver on a break) — while open, the trip is excluded from the stationary-vehicle anomaly check. Supports the `Idempotency-Key` header.",
    request=PauseSerializer,
    responses={200: TripPauseSerializer},
)
class TripPauseView(APIView):
    permission_classes = [IsDriverUser]

    def post(self, request, pk=None):
        def handler():
            serializer = PauseSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            pause = services.pause_trip(trip_id=pk, actor=request.user, **serializer.validated_data)
            return 200, TripPauseSerializer(pause).data

        status_code, body = with_idempotency(request, handler)
        return Response(body, status=status_code)


@extend_schema(
    tags=["Driver: Tracking"],
    summary="Resume a paused trip",
    description="Driver-only: closes the trip's currently open TripPause window, resuming stationary-vehicle anomaly checks. Supports the `Idempotency-Key` header.",
    responses={200: TripPauseSerializer},
)
class TripResumeView(APIView):
    permission_classes = [IsDriverUser]

    def post(self, request, pk=None):
        def handler():
            pause = services.resume_trip(trip_id=pk, actor=request.user)
            return 200, TripPauseSerializer(pause).data

        status_code, body = with_idempotency(request, handler)
        return Response(body, status=status_code)


@extend_schema(
    tags=["Admin: Trips", "Driver: Tracking"],
    summary="Get a trip's time summary",
    description="Returns total elapsed time and total paused time for a trip, computed from its TripPause windows. Admin or Driver only.",
    responses={200: TimeSummarySerializer},
)
class TripTimeSummaryView(APIView):
    permission_classes = [IsDriverOrAdminUser]

    def get(self, request, pk=None):
        trip = services.get_trip(pk, request.user.company_id)
        summary = services.get_trip_time_summary(trip)
        return Response(TimeSummarySerializer(summary).data)


@extend_schema(
    tags=["Admin: Shifts", "Driver: Shifts"],
    summary="Start a driver's shift",
    description="Admin or Driver: opens a new DriverShift for a driver/vehicle pair with a starting odometer reading — an admin can start one on a driver's behalf (e.g. at a hub) as well as the driver themself. Rejected with 409 `SHIFT_ALREADY_ACTIVE` if that driver already has one open — one active shift per driver at a time.",
    request=StartShiftSerializer,
    responses={200: DriverShiftSerializer},
)
class ShiftStartView(APIView):
    permission_classes = [IsDriverOrAdminUser]

    def post(self, request):
        serializer = StartShiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shift = services.start_shift(actor=request.user, **serializer.validated_data)
        return Response(DriverShiftSerializer(shift).data)


@extend_schema(
    tags=["Admin: Shifts", "Driver: Shifts"],
    summary="Get a driver's active shift",
    description="Admin or Driver: looks up the currently open DriverShift for `driver_id` (required query param). Returns null if the driver has no active shift.",
    responses={200: DriverShiftSerializer},
)
class ActiveShiftView(APIView):
    permission_classes = [IsDriverOrAdminUser]

    def get(self, request):
        driver_id = request.query_params.get("driver_id")
        if not driver_id:
            raise DomainError("DRIVER_ID_REQUIRED", "driver_id query parameter is required.", status_code=400)
        shift = services.get_active_shift(driver_id=driver_id, actor=request.user)
        return Response(DriverShiftSerializer(shift).data if shift else None)


@extend_schema(
    tags=["Admin: Shifts", "Driver: Shifts"],
    summary="End a driver's shift",
    description=(
        "Admin or Driver: closes a shift and computes total km/working minutes. Requires both "
        "a cleanliness photo and a charging-plugged photo (422 `CLEANLINESS_PHOTO_REQUIRED` / "
        "`CHARGING_PHOTO_REQUIRED` if either is missing) — never optional. Refused with 409 "
        "`TRIP_IN_PROGRESS` if the driver/vehicle still has an active trip; end that first."
    ),
    request=EndShiftSerializer,
    responses={200: EndShiftResultSerializer},
)
class ShiftEndView(APIView):
    permission_classes = [IsDriverOrAdminUser]

    def post(self, request, pk=None):
        serializer = EndShiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.end_shift(shift_id=pk, actor=request.user, **serializer.validated_data)
        return Response(EndShiftResultSerializer(result).data)


@extend_schema_view(
    list=extend_schema(
        tags=["Admin: Shifts"],
        summary="List driver shifts",
        description=(
            "Admin-side, day-level view of DriverShift records (separate from individual "
            "trips) — driver, vehicle, odometer readings, total km/working minutes, status, "
            "and the two required end-of-shift photos. Filterable by `status`, `driver`, "
            "`vehicle`, and `date_from`/`date_to` (inclusive, against `shift_date`)."
        ),
    ),
)
class ShiftViewSet(CompanyScopedMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAdminUser]
    queryset = DriverShift.objects.select_related("driver", "vehicle").all()
    serializer_class = DriverShiftAdminSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = ShiftFilter


@extend_schema_view(
    list=extend_schema(
        tags=["Admin: Tracking"],
        summary="List anomaly alerts",
        description=(
            "Lists automated anomaly alerts (a vehicle stationary too long, or heading the "
            "wrong direction) raised by the tracking Celery Beat jobs for in-transit trips. "
            "Filterable by `alert_type` and `acknowledged`."
        ),
    ),
)
class AlertViewSet(CompanyScopedMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = TripAnomalyAlert.objects.all()
    serializer_class = TripAnomalyAlertSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = AlertFilter

    @extend_schema(tags=["Admin: Tracking"], summary="Acknowledge an alert", description="Marks an alert acknowledged, clearing the way for a fresh alert of that type to be raised later if the anomaly recurs.")
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = services.acknowledge_alert(alert_id=pk, actor=request.user)
        return Response(TripAnomalyAlertSerializer(alert).data)
