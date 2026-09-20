from django.conf import settings
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.choices import VehicleStatus
from core.exceptions import DomainError
from core.tenancy import CompanyScopedMixin

from .filters import DriverFilter
from .models import Driver, Vehicle
from .permissions import IsDriverUser
from .serializers import (
    DriverDutyOnSerializer,
    DriverKycDecisionSerializer,
    DriverKycDlSerializer,
    DriverKycSerializer,
    DriverListSerializer,
    DriverLocationSerializer,
    DriverMeSerializer,
    DriverOtpRequestSerializer,
    DriverOtpVerifySerializer,
    DriverSerializer,
)
from .services import DriverKycService, DriverService
from .tokens import DriverTokenService


class DriverOtpRequestView(APIView):
    """POST /api/v1/driver/auth/otp/request"""

    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverOtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        driver, otp = DriverService.request_otp(serializer.validated_data["phone_number"])

        data = {"message": f"OTP sent to {driver.phone_number}."}
        if settings.DRIVER_OTP_DEBUG_RESPONSE:
            data["otp"] = otp
        return Response(data)


class DriverOtpVerifyView(APIView):
    """POST /api/v1/driver/auth/otp/verify"""

    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverOtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        driver = DriverService.verify_otp(
            serializer.validated_data["phone_number"], serializer.validated_data["otp"]
        )
        access, expires_in = DriverTokenService.issue(driver)

        return Response(
            {
                "accessToken": access,
                "tokenType": "Bearer",
                "expiresInSeconds": expires_in,
                "driverName": driver.full_name,
            }
        )


class DriverMeView(APIView):
    """GET /api/v1/driver/me"""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        return Response(DriverMeSerializer(request.user).data)


class DriverDutyStartView(APIView):
    """POST /api/v1/driver/duty/start — go online against a specific vehicle
    so trips.matching can consider this driver for assignment."""

    permission_classes = [IsDriverUser]

    def post(self, request, *args, **kwargs):
        serializer = DriverDutyOnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vehicle = get_object_or_404(
            Vehicle.objects.all(),
            pk=serializer.validated_data["vehicle_id"],
            company_id=request.user.company_id,
        )
        if vehicle.status != VehicleStatus.ACTIVE:
            raise DomainError("VEHICLE_NOT_ACTIVE", "This vehicle is not active.", status_code=409)

        driver = DriverService.go_online(request.user, vehicle)
        return Response(DriverMeSerializer(driver).data)


class DriverDutyEndView(APIView):
    """POST /api/v1/driver/duty/end"""

    permission_classes = [IsDriverUser]

    def post(self, request, *args, **kwargs):
        driver = DriverService.go_offline(request.user)
        return Response(DriverMeSerializer(driver).data)


class DriverLocationView(APIView):
    """POST /api/v1/driver/location — periodic location ping from the
    driver app while on duty."""

    permission_classes = [IsDriverUser]

    def post(self, request, *args, **kwargs):
        serializer = DriverLocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverService.update_location(request.user, **serializer.validated_data)
        return Response({"message": "Location updated."})


class DriverViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    queryset = Driver.objects.select_related("kyc").all()
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = DriverFilter
    search_fields = ["full_name", "phone_number"]

    def get_serializer_class(self):
        if self.action == "list":
            return DriverListSerializer
        return DriverSerializer

    def perform_create(self, serializer):
        # Bypasses serializer.save() (DriverService.create does its own
        # Driver.objects.create, plus the paired DriverKyc row) — instance
        # must still be set by hand so serializer.data renders the created
        # driver back, same contract ModelSerializer.save() would give.
        serializer.instance = DriverService.create(self.request.user.company, **serializer.validated_data)

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        driver = self.get_object()
        DriverService.disable(driver)
        return Response(DriverSerializer(driver).data)


class DriverKycMixin:
    permission_classes = [IsAdminUser]

    def get_driver(self):
        return get_object_or_404(
            Driver.objects.select_related("kyc"), pk=self.kwargs["pk"], company_id=self.request.user.company_id
        )


class DriverKycView(DriverKycMixin, APIView):
    """GET /api/v1/drivers/{id}/kyc"""

    def get(self, request, pk=None):
        return Response(DriverKycSerializer(self.get_driver().kyc).data)


class DriverKycAadharView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/aadhar"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverKycService.verify_aadhar(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver.kyc).data)


class DriverKycPoliceView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/police"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverKycService.verify_police(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver.kyc).data)


class DriverKycDlView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/dl"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverKycService.verify_dl(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver.kyc).data)
