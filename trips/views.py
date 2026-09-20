from django.conf import settings
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from core.choices import CancelledBy, TripStatus, VehicleTypeStatus
from core.tenancy import CompanyScopedMixin
from drivers.models import VehicleType
from drivers.permissions import IsDriverUser

from .filters import TripFilter
from .models import Trip
from .permissions import IsCompanyPrincipal
from .serializers import (
    TripCancelSerializer,
    TripCompleteSerializer,
    TripCreateSerializer,
    TripEstimateRequestSerializer,
    TripListSerializer,
    TripSerializer,
)
from .services import TripService


class TripEstimateView(APIView):
    """POST /api/v1/trips/estimate — route + fare for every active vehicle
    type in the caller's fleet, given a pickup/drop pair. Used to render a
    Porter-style vehicle-type picker before the caller commits to POST
    /trips with one of the returned vehicle_type ids.
    """

    permission_classes = [IsCompanyPrincipal]

    def post(self, request, *args, **kwargs):
        serializer = TripEstimateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pickup = serializer.validated_data["pickup"]
        drop = serializer.validated_data["drop"]

        vehicle_types = VehicleType.objects.filter(
            company_id=request.user.company_id, status=VehicleTypeStatus.ACTIVE
        )

        estimates = []
        for vehicle_type in vehicle_types:
            estimate = TripService.estimate_trip(
                request.user.company, vehicle_type, pickup["lat"], pickup["lng"], drop["lat"], drop["lng"]
            )
            estimates.append(
                {
                    "vehicle_type_id": str(vehicle_type.id),
                    "vehicle_type_name": vehicle_type.name,
                    "category": vehicle_type.category,
                    "icon_image_url": vehicle_type.icon_image_url,
                    "distance_meters": estimate["distance_meters"],
                    "duration_seconds": estimate["duration_seconds"],
                    "route_polyline": estimate["polyline"],
                    "polyline_precision": estimate["polyline_precision"],
                    "base_fare": estimate["base_fare"],
                    "distance_fare": estimate["distance_fare"],
                    "time_fare": estimate["time_fare"],
                    "surge_multiplier": estimate["surge_multiplier"],
                    "total_fare": estimate["total_fare"],
                    "currency": estimate["currency"],
                }
            )

        return Response({"estimates": estimates})


class TripViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    queryset = Trip.objects.select_related("vehicle_type", "driver", "vehicle")
    permission_classes = [IsCompanyPrincipal]
    filter_backends = [DjangoFilterBackend]
    filterset_class = TripFilter
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return TripListSerializer
        if self.action == "create":
            return TripCreateSerializer
        return TripSerializer

    def create(self, request, *args, **kwargs):
        serializer = TripCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        trip = TripService.create_trip(
            company=request.user.company,
            vehicle_type=data["vehicle_type"],
            pickup=data["pickup"],
            drop=data["drop"],
            payment_mode=data["payment_mode"],
            reference_id=data.get("reference_id", ""),
        )
        return Response(TripSerializer(trip).data, status=201)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        trip = TripService.retry_assignment(self.get_object())
        return Response(TripSerializer(trip).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = TripCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = TripService.cancel_trip(self.get_object(), serializer.validated_data["reason"], CancelledBy.COMPANY)
        return Response(TripSerializer(trip).data)


class DriverTripMixin:
    permission_classes = [IsDriverUser]

    def get_trip(self):
        return get_object_or_404(Trip.objects.all(), pk=self.kwargs["pk"], driver=self.request.user)


class DriverActiveTripView(APIView):
    """GET /api/v1/driver/trips/active — the authenticated driver's
    currently assigned/in-progress trip, if any."""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        trip = (
            Trip.objects.select_related("vehicle_type", "driver", "vehicle")
            .filter(
                driver=request.user,
                status__in=[TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS],
            )
            .order_by("-created_at")
            .first()
        )
        if trip is None:
            return Response({"trip": None})
        return Response({"trip": TripSerializer(trip).data})


class DriverTripArriveView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/arrive"""

    def post(self, request, pk=None):
        trip = TripService.driver_arrive(self.get_trip(), request.user)
        return Response(TripSerializer(trip).data)


class DriverTripStartView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/start"""

    def post(self, request, pk=None):
        trip = TripService.driver_start(self.get_trip(), request.user)
        return Response(TripSerializer(trip).data)


class DriverTripPaymentQrView(DriverTripMixin, APIView):
    """GET /api/v1/driver/trips/{id}/payment/qr — QR payload for the
    customer to scan and pay a COD trip's fare."""

    def get(self, request, pk=None):
        qr = TripService.generate_payment_qr(self.get_trip())
        return Response(qr)


class DriverTripPaymentCollectView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/payment/collect — driver confirms the
    customer has paid. Sends the delivery OTP that finalizes the trip."""

    def post(self, request, pk=None):
        trip, otp = TripService.collect_cod_payment(self.get_trip(), request.user)
        data = {"message": f"Payment collected. An OTP was sent to {trip.drop_contact_phone} to finalize the trip."}
        if settings.DRIVER_OTP_DEBUG_RESPONSE:
            data["otp"] = otp
        return Response(data)


class DriverTripCompleteView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/complete — for a COD trip, requires
    the delivery OTP sent by DriverTripPaymentCollectView."""

    def post(self, request, pk=None):
        serializer = TripCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = TripService.driver_complete(self.get_trip(), request.user, otp=serializer.validated_data.get("otp"))
        return Response(TripSerializer(trip).data)


class DriverTripCancelView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/cancel"""

    def post(self, request, pk=None):
        serializer = TripCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = TripService.driver_cancel(self.get_trip(), request.user, serializer.validated_data["reason"])
        return Response(TripSerializer(trip).data)
