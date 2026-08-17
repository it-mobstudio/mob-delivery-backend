from django.conf import settings
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
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


@extend_schema(
    tags=["drivers"],
    summary="Request a driver login OTP",
    description=(
        "Sends a 6-digit OTP to the driver's registered phone number, valid for 5 minutes. "
        "Throttled to one request per 30 seconds per phone number (429 if retried too soon). "
        "Rejected with 403 if the driver's account is locked (DL expired) or disabled. When "
        "`DRIVER_OTP_DEBUG_RESPONSE=True` (non-prod), the generated OTP is echoed back in the "
        "response so the flow is testable without a real SMS gateway."
    ),
)
class DriverOtpRequestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverOtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        driver, otp = services.request_otp(serializer.validated_data["phone_number"])

        data = {"message": f"OTP sent to {driver.phone_number}."}
        if settings.DRIVER_OTP_DEBUG_RESPONSE:
            data["otp"] = otp
        return Response(data)


@extend_schema(
    tags=["drivers"],
    summary="Verify a driver login OTP",
    description=(
        "Verifies the OTP and, on success, logs the driver in — returns an access + refresh "
        "JWT pair. An invalid, wrong, or expired OTP returns 400 `INVALID_OTP`. Re-checks the "
        "driver's account status too, in case it changed between request and verify."
    ),
)
class DriverOtpVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverOtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        driver = services.verify_otp(
            serializer.validated_data["phone_number"], serializer.validated_data["otp"]
        )
        access, refresh, expires_in = issue_driver_token(driver)

        return Response(
            {
                "accessToken": access,
                "refreshToken": refresh,
                "tokenType": "Bearer",
                "expiresInSeconds": expires_in,
                "driverName": driver.full_name,
            }
        )


@extend_schema(
    tags=["drivers"],
    summary="Get the logged-in driver's own profile",
    description="Returns the authenticated driver's own profile and verification statuses. Driver-only — an AdminUser/ApiClient token is rejected.",
)
class DriverMeView(APIView):
    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        return Response(DriverMeSerializer(request.user).data)


@extend_schema_view(
    list=extend_schema(summary="List drivers", description="Lists this company's drivers, searchable by name/phone, filterable by verification/account status."),
    retrieve=extend_schema(summary="Get a driver"),
    create=extend_schema(summary="Create a driver profile", description="Creates a driver record (KYC/verification is done separately via the kyc endpoints below)."),
    update=extend_schema(summary="Replace a driver"),
    partial_update=extend_schema(summary="Update a driver"),
    destroy=extend_schema(summary="Delete a driver"),
)
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

    @extend_schema(
        summary="Disable a driver",
        description="Soft-deletes and deactivates a driver. Refuses with 409 `DRIVER_HAS_ACTIVE_TRIP` if they currently have a trip in progress.",
    )
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


@extend_schema(tags=["drivers"], summary="Get a driver's KYC status", description="Returns the Aadhar, driving-licence, and police-verification status/documents for one driver.")
class DriverKycView(DriverKycMixin, APIView):
    def get(self, request, pk=None):
        return Response(DriverKycSerializer(self.get_driver()).data)


@extend_schema(
    tags=["drivers"],
    summary="Verify or reject a driver's Aadhar",
    description="Admin decision on a driver's uploaded Aadhar document. A `note` is required when rejecting; the driver is pushed a notification on rejection.",
)
class DriverKycAadharView(DriverKycMixin, APIView):
    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.verify_aadhar(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver).data)


@extend_schema(
    tags=["drivers"],
    summary="Verify or reject a driver's police verification",
    description="Admin decision on a driver's police-verification document. A `note` is required when rejecting; the driver is pushed a notification on rejection.",
)
class DriverKycPoliceView(DriverKycMixin, APIView):
    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.verify_police(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver).data)


@extend_schema(
    tags=["drivers"],
    summary="Verify or reject a driver's driving licence",
    description=(
        "Admin decision on a driver's DL. Verifying requires `expiry_date` (must be in the "
        "future) and `allowed_categories` (which vehicle categories this DL permits — used by "
        "the vehicle-type-match check during assignment). If the driver was locked for an "
        "expired DL, verifying with a future expiry date automatically unlocks their account."
    ),
)
class DriverKycDlView(DriverKycMixin, APIView):
    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.verify_dl(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver).data)
