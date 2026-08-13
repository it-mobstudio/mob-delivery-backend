from django.conf import settings
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.tenancy import CompanyScopedMixin

from . import services
from .filters import DriverFilter
from .models import Driver
from .permissions import IsDriverUser
from .serializers import (
    DriverKycDecisionSerializer,
    DriverKycDlSerializer,
    DriverKycSerializer,
    DriverListSerializer,
    DriverMeSerializer,
    DriverOtpRequestSerializer,
    DriverOtpVerifySerializer,
    DriverSerializer,
)
from .tokens import issue_driver_token


class DriverOtpRequestView(APIView):
    """POST /api/v1/driver/auth/otp/request"""

    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverOtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        driver, otp = services.request_otp(serializer.validated_data["phone_number"])

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

        driver = services.verify_otp(
            serializer.validated_data["phone_number"], serializer.validated_data["otp"]
        )
        access, expires_in = issue_driver_token(driver)

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


class DriverViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    queryset = Driver.objects.all()
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = DriverFilter
    search_fields = ["full_name", "phone_number"]

    def get_serializer_class(self):
        if self.action == "list":
            return DriverListSerializer
        return DriverSerializer

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        driver = self.get_object()
        services.disable_driver(driver)
        return Response(DriverSerializer(driver).data)


class DriverKycMixin:
    permission_classes = [IsAdminUser]

    def get_driver(self):
        return get_object_or_404(
            Driver.objects.all(), pk=self.kwargs["pk"], company_id=self.request.user.company_id
        )


class DriverKycView(DriverKycMixin, APIView):
    """GET /api/v1/drivers/{id}/kyc"""

    def get(self, request, pk=None):
        return Response(DriverKycSerializer(self.get_driver()).data)


class DriverKycAadharView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/aadhar"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.verify_aadhar(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver).data)


class DriverKycPoliceView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/police"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.verify_police(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver).data)


class DriverKycDlView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/dl"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.verify_dl(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver).data)
