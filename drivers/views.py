from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.choices import VehicleStatus
from core.exceptions import DomainError
from core.tenancy import CompanyScopedMixin
from trips.services import TripService

from .filters import DriverFilter
from .models import Driver, Vehicle, WalletTransaction
from .permissions import IsDriverUser
from .serializers import (
    DriverAadharSubmitSerializer,
    DriverDlSubmitSerializer,
    DriverDutyOnSerializer,
    DriverKycDecisionSerializer,
    DriverKycDlSerializer,
    DriverKycSerializer,
    DriverListSerializer,
    DriverLocationSerializer,
    DriverMeSerializer,
    DriverOtpRequestSerializer,
    DriverOtpVerifySerializer,
    DriverPhotoSerializer,
    DriverPoliceSubmitSerializer,
    DriverProfileUpdateSerializer,
    DriverSerializer,
    DriverStatsQuerySerializer,
    DriverTokenRefreshSerializer,
    DriverVehicleSummarySerializer,
    WalletEntryCreateSerializer,
    WalletQuerySerializer,
    WalletTransactionSerializer,
)
from .services import DriverKycService, DriverService
from .tokens import DriverTokenService
from .wallet import WalletService


class DriverOtpRequestView(APIView):
    """POST /api/v1/driver/auth/otp/request"""

    # No authenticators on the unauthenticated auth endpoints: DRF runs them
    # even for AllowAny views, and would 401 a login attempt that happens to
    # carry a stale/expired Bearer token from the driver's previous session.
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverOtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        otp = DriverService.request_otp(phone_number)

        data = {"message": f"OTP sent to {phone_number}."}
        if settings.DRIVER_OTP_DEBUG_RESPONSE:
            data["otp"] = otp
        return Response(data)


def _token_response(driver, tokens, request):
    return {
        "accessToken": tokens["access"],
        "tokenType": "Bearer",
        "expiresInSeconds": tokens["access_expires_in"],
        "refreshToken": tokens["refresh"],
        "refreshExpiresInSeconds": tokens["refresh_expires_in"],
        "driverName": driver.full_name,
        "driver": DriverMeSerializer(driver, context={"request": request}).data,
    }


class DriverOtpVerifyView(APIView):
    """POST /api/v1/driver/auth/otp/verify"""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverOtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        driver = DriverService.verify_otp(
            serializer.validated_data["phone_number"], serializer.validated_data["otp"]
        )
        return Response(_token_response(driver, DriverTokenService.issue_pair(driver), request))


class DriverTokenRefreshView(APIView):
    """POST /api/v1/driver/auth/refresh — trades a refresh token for a new
    access token (and a new refresh token, restarting its lifetime)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverTokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        driver, tokens = DriverTokenService.refresh(serializer.validated_data["refreshToken"])
        return Response(_token_response(driver, tokens, request))


class DriverLogoutView(APIView):
    """POST /api/v1/driver/auth/logout — revokes the refresh token so the
    session can't be silently revived from this device or a copy of it."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DriverTokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        DriverTokenService.revoke(serializer.validated_data["refreshToken"])
        return Response({"message": "Signed out."})


def _me(request, driver=None):
    return Response(DriverMeSerializer(driver or request.user, context={"request": request}).data)


class DriverMeView(APIView):
    """GET /api/v1/driver/me — the profile, KYC state and onboarding status.
    PATCH edits the driver's own details. DELETE closes the account."""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        return _me(request)

    def patch(self, request, *args, **kwargs):
        serializer = DriverProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        driver = DriverService.update_profile(request.user, **serializer.validated_data)
        return _me(request, driver)

    def delete(self, request, *args, **kwargs):
        DriverService.delete_account(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DriverSubmissionView(APIView):
    """Base for the multipart endpoints a driver uses to submit documents:
    validate the form, hand it to DriverKycService, answer with the refreshed
    profile so the app updates in one round trip."""

    permission_classes = [IsDriverUser]
    parser_classes = [MultiPartParser]
    serializer_class = None

    def submit(self, driver, data):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _me(request, self.submit(request.user, serializer.validated_data))


class DriverPhotoView(DriverSubmissionView):
    """POST /api/v1/driver/me/photo — multipart `photo` (a selfie)."""

    serializer_class = DriverPhotoSerializer

    def submit(self, driver, data):
        return DriverKycService.set_profile_photo(driver, data["photo"])


class DriverAadharSubmitView(DriverSubmissionView):
    """POST /api/v1/driver/me/kyc/aadhar — multipart `number`, `front`, `back`."""

    serializer_class = DriverAadharSubmitSerializer

    def submit(self, driver, data):
        return DriverKycService.submit_aadhar(driver, data["number"], data["front"], data["back"])


class DriverDlSubmitView(DriverSubmissionView):
    """POST /api/v1/driver/me/kyc/dl — multipart `number`, `expiry_date`,
    `front`, optional `back`."""

    serializer_class = DriverDlSubmitSerializer

    def submit(self, driver, data):
        return DriverKycService.submit_dl(
            driver, data["number"], data["expiry_date"], data["front"], data.get("back")
        )


class DriverPoliceSubmitView(DriverSubmissionView):
    """POST /api/v1/driver/me/kyc/police — multipart `document`."""

    serializer_class = DriverPoliceSubmitSerializer

    def submit(self, driver, data):
        return DriverKycService.submit_police(driver, data["document"])


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

        driver = DriverService.go_online(
            request.user,
            vehicle,
            lat=serializer.validated_data.get("lat"),
            lng=serializer.validated_data.get("lng"),
        )
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


class DriverVehicleListView(APIView):
    """GET /api/v1/driver/vehicles — the vehicles this driver may go on
    duty with (see DriverService.available_vehicles). The app shows this as
    its vehicle picker; each row carries `is_current` so the driver's own
    vehicle can be pre-selected."""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        vehicles = DriverService.available_vehicles(request.user)
        data = DriverVehicleSummarySerializer(vehicles, many=True).data
        for row in data:
            row["is_current"] = str(row["id"]) == str(request.user.current_vehicle_id)
        return Response({"vehicles": data})


class DriverStatsView(APIView):
    """GET /api/v1/driver/stats?utc_offset_minutes=330 — today's and
    all-time trip counts/fare totals for the driver's dashboard."""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        query = DriverStatsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        # Local midnight for the driver, expressed as a UTC instant.
        offset = timedelta(minutes=query.validated_data["utc_offset_minutes"])
        local_now = timezone.now().astimezone(dt_timezone(offset))
        start_of_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

        return Response(
            {
                "today": TripService.driver_stats(request.user, since=start_of_today),
                "all_time": TripService.driver_stats(request.user),
            }
        )


class DriverWalletView(APIView):
    """GET /api/v1/driver/wallet?utc_offset_minutes=330 — balance, today /
    week / month / lifetime earnings and the last seven days, for the wallet
    screen."""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        query = WalletQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(WalletService.summary(request.user, query.validated_data["utc_offset_minutes"]))


class DriverWalletTransactionsView(generics.ListAPIView):
    """GET /api/v1/driver/wallet/transactions?kind=trip_earning,bonus&page=N —
    the driver's statement, newest first."""

    permission_classes = [IsDriverUser]
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # API schema generation, no request
            return WalletTransaction.objects.none()
        return WalletService.transactions(self.request.user, self.request.query_params.get("kind"))


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

    def perform_destroy(self, instance):
        # Same as `disable`: retires the driver and keeps their history (trips,
        # wallet ledger). A hard delete would also be refused by the ledger.
        DriverService.disable(instance)

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
        return Response(DriverKycSerializer(self.get_driver().kyc, context={"request": request}).data)


class DriverKycAadharView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/aadhar"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverKycService.verify_aadhar(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver.kyc, context={"request": request}).data)


class DriverKycPoliceView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/police"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverKycService.verify_police(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver.kyc, context={"request": request}).data)


class DriverKycDlView(DriverKycMixin, APIView):
    """PATCH /api/v1/drivers/{id}/kyc/dl"""

    def patch(self, request, pk=None):
        driver = self.get_driver()
        serializer = DriverKycDlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverKycService.verify_dl(driver, admin_id=request.user.id, **serializer.validated_data)
        return Response(DriverKycSerializer(driver.kyc, context={"request": request}).data)


class DriverWalletAdminView(DriverKycMixin, APIView):
    """GET /api/v1/drivers/{id}/wallet — a driver's balance and recent
    statement, for the company. (Reuses DriverKycMixin only for its
    company-scoped driver lookup.)"""

    def get(self, request, pk=None):
        driver = self.get_driver()
        recent = WalletService.transactions(driver)[:20]
        return Response(
            {**WalletService.summary(driver), "recent": WalletTransactionSerializer(recent, many=True).data}
        )


class DriverWalletEntryView(DriverKycMixin, APIView):
    """POST /api/v1/drivers/{id}/wallet/transactions — record a payout you've
    sent, a bonus, a penalty, or a correction. Trip earnings are never entered
    by hand: they're credited when the driver completes the trip."""

    def post(self, request, pk=None):
        driver = self.get_driver()
        serializer = WalletEntryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = WalletService.record_manual(driver, created_by=request.user.id, **serializer.validated_data)
        return Response(WalletTransactionSerializer(entry).data, status=status.HTTP_201_CREATED)
