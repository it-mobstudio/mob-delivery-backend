import hashlib
import hmac
import json

from django.conf import settings
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.choices import CancelledBy, TripStatus, VehicleTypeStatus
from core.exceptions import DomainError
from core.tenancy import CompanyScopedMixin
from drivers.models import VehicleType
from drivers.permissions import IsDriverUser

from .filters import TripFilter
from .models import Trip
from .permissions import IsCompanyPrincipal
from .serializers import (
    DriverNavigationQuerySerializer,
    DriverTripListSerializer,
    DriverTripSerializer,
    TripCancelSerializer,
    TripCompleteSerializer,
    TripCreateSerializer,
    TripEstimateRequestSerializer,
    TripItemVerifySerializer,
    TripListSerializer,
    TripPickupPhotoSerializer,
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
    queryset = Trip.objects.select_related("vehicle_type", "driver", "vehicle").prefetch_related("items")
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
            notes=data.get("notes", "").strip(),
            delivery_otp=data.get("delivery_otp", False),
            invoice_url=data.get("invoice_url", ""),
            invoice_number=data.get("invoice_number", ""),
            verify_items=data.get("verify_items", False),
            items=data.get("items"),
            bonus_fare=data.get("bonus_fare"),
            pickup_photo=data.get("pickup_photo", "none"),
            delivery_photo=data.get("delivery_photo", "none"),
        )
        return Response(TripSerializer(trip, context={"request": request}).data, status=201)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        trip = TripService.retry_assignment(self.get_object())
        return Response(TripSerializer(trip, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = TripCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = TripService.cancel_trip(self.get_object(), serializer.validated_data["reason"], CancelledBy.COMPANY)
        return Response(TripSerializer(trip, context={"request": request}).data)


class DriverTripMixin:
    permission_classes = [IsDriverUser]

    def get_trip(self):
        return get_object_or_404(Trip.objects.all(), pk=self.kwargs["pk"], driver=self.request.user)

    def trip_response(self, trip):
        return Response(DriverTripSerializer(trip, context={"request": self.request}).data)


class DriverActiveTripView(APIView):
    """GET /api/v1/driver/trips/active — the authenticated driver's
    currently assigned/in-progress trip, if any."""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        trip = (
            Trip.objects.select_related("vehicle_type", "driver", "vehicle")
            .prefetch_related("items")
            .filter(
                driver=request.user,
                status__in=[TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS],
            )
            .order_by("-created_at")
            .first()
        )
        if trip is None:
            return Response({"trip": None})
        return Response({"trip": DriverTripSerializer(trip, context={"request": request}).data})


class DriverTripListView(generics.ListAPIView):
    """GET /api/v1/driver/trips — the authenticated driver's trip history,
    newest first. Filter with ?status=completed (comma-separated for
    several, e.g. ?status=completed,cancelled)."""

    permission_classes = [IsDriverUser]
    serializer_class = DriverTripListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # API schema generation, no request
            return Trip.objects.none()
        qs = Trip.objects.select_related("vehicle_type", "driver", "vehicle").filter(driver=self.request.user)
        statuses = [s.strip() for s in self.request.query_params.get("status", "").split(",") if s.strip()]
        if statuses:
            qs = qs.filter(status__in=statuses)
        return qs.order_by("-created_at")


class DriverTripDetailView(DriverTripMixin, APIView):
    """GET /api/v1/driver/trips/{id}"""

    def get(self, request, pk=None):
        trip = get_object_or_404(
            Trip.objects.select_related("vehicle_type", "driver", "vehicle").prefetch_related("items"),
            pk=pk,
            driver=request.user,
        )
        return self.trip_response(trip)


class DriverTripNavigationView(DriverTripMixin, APIView):
    """GET /api/v1/driver/trips/{id}/navigation?lat=..&lng=.. — route from
    the driver's current position to the trip's next stop (pickup, then
    drop). See TripService.navigation_route."""

    def get(self, request, pk=None):
        query = DriverNavigationQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        trip = get_object_or_404(Trip.objects.select_related("vehicle_type"), pk=pk, driver=request.user)
        route = TripService.navigation_route(
            trip, request.user, query.validated_data["lat"], query.validated_data["lng"]
        )
        return Response(route)


class DriverTripArriveView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/arrive"""

    def post(self, request, pk=None):
        trip = TripService.driver_arrive(self.get_trip(), request.user)
        return self.trip_response(trip)


class DriverTripStartView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/start"""

    def post(self, request, pk=None):
        trip = TripService.driver_start(self.get_trip(), request.user)
        return self.trip_response(trip)


class DriverTripPaymentQrView(DriverTripMixin, APIView):
    """GET /api/v1/driver/trips/{id}/payment/qr — the scan-to-pay QR for a COD
    trip's fare: a Razorpay-generated image (`image_url`), or for the local
    stand-in provider a UPI link to draw (`qr_payload`)."""

    def get(self, request, pk=None):
        qr = TripService.generate_payment_qr(self.get_trip())
        return Response(
            {
                "provider": qr.provider,
                "reference": qr.reference or None,
                "qr_payload": qr.payload,
                "image_url": qr.image_url,
                "amount": qr.amount,
                "currency": qr.currency,
                "expires_at": qr.expires_at,
            }
        )


class DriverTripPaymentCollectView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/payment/collect — driver confirms the
    customer has paid. Sends the delivery OTP that finalizes the trip."""

    def post(self, request, pk=None):
        trip, otp = TripService.collect_cod_payment(self.get_trip(), request.user)
        data = {"message": f"Payment collected. An OTP was sent to {trip.drop_contact_phone} to finalize the trip."}
        if settings.DRIVER_OTP_DEBUG_RESPONSE:
            data["otp"] = otp
        return Response(data)


class DriverTripDeliveryOtpResendView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/delivery-otp/resend — a fresh delivery
    OTP for a COD trip whose payment is already collected (the first one
    expired or never arrived)."""

    def post(self, request, pk=None):
        trip = self.get_trip()
        otp = TripService.resend_delivery_otp(trip, request.user)
        data = {"message": f"A new OTP was sent to {trip.drop_contact_phone}."}
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
        return self.trip_response(trip)


class DriverTripCancelView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/cancel"""

    def post(self, request, pk=None):
        serializer = TripCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = TripService.driver_cancel(self.get_trip(), request.user, serializer.validated_data["reason"])
        return self.trip_response(trip)


class _DriverTripPhotoView(DriverTripMixin, APIView):
    """Multipart: `photo` (taken with the phone's camera) and, when the trip
    wants one per item, `item_id`. Answers with the whole trip."""

    parser_classes = [MultiPartParser]
    stage = None

    def post(self, request, pk=None):
        serializer = TripPickupPhotoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        trip = self.get_trip()
        TripService.add_photo(trip, request.user, self.stage, data["photo"], item_id=data.get("item_id"))
        return self.trip_response(Trip.objects.prefetch_related("items").get(pk=trip.pk))


class DriverTripPickupPhotoView(_DriverTripPhotoView):
    """POST /api/v1/driver/trips/{id}/pickup-photo"""

    stage = "pickup"


class DriverTripDeliveryPhotoView(_DriverTripPhotoView):
    """POST /api/v1/driver/trips/{id}/delivery-photo"""

    stage = "delivery"


class DriverTripItemVerifyView(DriverTripMixin, APIView):
    """POST /api/v1/driver/trips/{id}/items/{item_id}/verify — multipart:
    `status` (delivered | not_delivered), `note`, optional `photo` (taken with
    the phone's camera). Answers with the whole trip so the app's checklist
    and its "all verified" state come from one source.
    DELETE takes the item back to pending."""

    parser_classes = [MultiPartParser]

    def post(self, request, pk=None, item_id=None):
        serializer = TripItemVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        trip = self.get_trip()
        TripService.verify_item(
            trip, request.user, item_id, data["status"], note=data["note"], photo=data.get("photo")
        )
        return self.trip_response(Trip.objects.prefetch_related("items").get(pk=trip.pk))

    def delete(self, request, pk=None, item_id=None):
        trip = self.get_trip()
        TripService.reset_item(trip, request.user, item_id)
        return self.trip_response(Trip.objects.prefetch_related("items").get(pk=trip.pk))


class RazorpayWebhookView(APIView):
    """POST /api/v1/webhooks/razorpay — Razorpay tells us a QR code was paid.
    Unauthenticated by design (Razorpay can't log in); what protects it is the
    `X-Razorpay-Signature` header: an HMAC-SHA256 of the raw body, keyed with
    RAZORPAY_WEBHOOK_SECRET. Anything not signed with it is refused."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not secret:
            raise DomainError(
                "WEBHOOK_NOT_CONFIGURED", "Payment webhooks aren't set up on this server.", status_code=503
            )
        body = request.body
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Razorpay-Signature", "")):
            raise DomainError("INVALID_SIGNATURE", "The webhook signature doesn't match.", status_code=400)

        try:
            event = json.loads(body)
        except ValueError:
            raise DomainError("INVALID_PAYLOAD", "The webhook body isn't valid JSON.", status_code=400)
        if not isinstance(event, dict):
            raise DomainError("INVALID_PAYLOAD", "The webhook body isn't a JSON object.", status_code=400)

        return Response({"status": TripService.apply_razorpay_event(event)})
