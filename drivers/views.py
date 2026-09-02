from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.exceptions import DomainError
from core.tenancy import CompanyScopedMixin
from uploads.constants import UploadPurpose
from uploads.services import store_upload

from . import services
from .filters import DriverFilter
from .models import Driver
from .permissions import IsDriverUser
from .serializers import (
    DOC_FILE_TO_URL_FIELDS,
    DriverCreateSerializer,
    DriverKycSerializer,
    DriverListSerializer,
    DriverMeSerializer,
    DriverOtpRequestSerializer,
    DriverOtpVerifySerializer,
    DriverSerializer,
    DriverUpdateSerializer,
)
from .tokens import issue_driver_token


def _upload_kyc_doc(file, request):
    """Shared by driver creation and updates — uploads a directly-submitted
    document file to blob storage (or local filesystem in dev) and returns
    its URL, the same shape a doc_url input already takes. See
    uploads.services.store_upload.
    """
    try:
        return store_upload(file, UploadPurpose.DRIVER_DOCUMENT, request.user.company_id)
    except DjangoValidationError as exc:
        raise DomainError("INVALID_UPLOAD", "; ".join(exc.messages), status_code=400)


def _resolve_doc_files(serializer, request):
    """Pops every doc file (aadhar_doc/dl_doc/police_doc) out of
    serializer.validated_data, if present, and replaces it with the
    matching *_doc_url after uploading — called between is_valid() and
    save() so create()/update() only ever see a URL, uploaded here or
    already hosted. Shared by perform_create and perform_update.
    """
    for file_field, url_field in DOC_FILE_TO_URL_FIELDS.items():
        file = serializer.validated_data.pop(file_field, None)
        if file is not None:
            serializer.validated_data[url_field] = _upload_kyc_doc(file, request)


@extend_schema(
    tags=["Driver: Auth"],
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
    tags=["Driver: Auth"],
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
    tags=["Driver: Profile"],
    summary="Get the logged-in driver's own profile",
    description="Returns the authenticated driver's own profile and verification statuses. Driver-only — an AdminUser/ApiClient token is rejected.",
)
class DriverMeView(APIView):
    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        return Response(DriverMeSerializer(request.user).data)


@extend_schema_view(
    list=extend_schema(tags=["Admin: Drivers"], summary="List drivers", description="Lists this company's drivers, searchable by name/phone, filterable by verification/account status."),
    retrieve=extend_schema(tags=["Admin: Drivers"], summary="Get a driver"),
    create=extend_schema(
        tags=["Admin: Drivers"],
        summary="Create a driver profile",
        description=(
            "Creates a driver record. Optionally accepts documents for a one-shot onboarding "
            "when an admin already has them on hand: either an already-hosted URL "
            "(`aadharDocUrl`/`dlDocUrl`/`policeDocUrl`, e.g. from a prior `POST /uploads` call) "
            "or a direct multipart file (`aadharDoc`/`dlDoc`/`policeDoc`) that this endpoint "
            "uploads to blob storage itself — provide at most one of the two per document. Also "
            "accepts `dlExpiryDate`. None of this verifies anything: every status still defaults "
            "to `pending`. Verification is always a separate, explicit admin decision made via a "
            "subsequent `PATCH` (see `partial_update` below), whether the documents were "
            "attached here or later."
        ),
    ),
    update=extend_schema(
        tags=["Admin: Drivers"],
        summary="Replace a driver",
        description=(
            "Also handles KYC in the same call — see `partial_update` below for the full shape; "
            "PUT semantics still apply to the base profile fields."
        ),
    ),
    partial_update=extend_schema(
        tags=["Admin: Drivers"],
        summary="Update a driver, including KYC",
        description=(
            "True partial update: only whichever fields are sent get changed, everything else "
            "on the driver record is left exactly as it was. Beyond the base profile fields, "
            "accepts any subset of `aadharDocUrl`/`aadharDoc`/`aadharStatus`/`aadharNote`, the "
            "same three for `dl` and `police`, plus `dlExpiryDate`/`dlAllowedCategories` — this "
            "one endpoint now covers what used to be three separate `kyc/{docType}` endpoints. "
            "`*Doc` is a direct multipart file upload (mutually exclusive with the matching "
            "`*DocUrl`); providing either with no status resets that document to `pending` "
            "review; a `*Note` is required when rejecting; verifying the DL needs "
            "`dlExpiryDate`/`dlAllowedCategories` either in this request or already on file."
        ),
    ),
    destroy=extend_schema(
        tags=["Admin: Drivers"],
        summary="Delete a driver",
        description=(
            "Soft-deletes and deactivates the driver — same effect as the `disable` action "
            "below, including the 409 `DRIVER_HAS_ACTIVE_TRIP` refusal. A raw hard delete is "
            "never performed via the API."
        ),
    ),
)
class DriverViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    queryset = Driver.objects.all()
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = DriverFilter
    search_fields = ["full_name", "phone_number"]
    # Every update spec here is PATCH (partial) — PUT (full replacement) was
    # only ever reachable as a side effect of ModelViewSet's defaults, never
    # an intended contract, and on fields like KYC status/doc URLs an
    # accidental full-replacement call could silently wipe real review work.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def perform_destroy(self, instance):
        # Model.delete() bypasses SoftDeleteQuerySet.delete() (that override
        # only intercepts queryset-level bulk deletes, not instance.delete())
        # — routing through the same domain service as `disable` below keeps
        # DELETE from hard-deleting the row and skipping the active-trip check.
        services.disable_driver(instance)

    def perform_create(self, serializer):
        # DriverCreateSerializer already rejected the case where both a
        # file and a URL were given for the same document. company= mirrors
        # CompanyScopedMixin.perform_create, which this override replaces —
        # omitting it would leave company_id unset on the new row.
        _resolve_doc_files(serializer, self.request)
        serializer.save(company=self.request.user.company)

    def perform_update(self, serializer):
        # DriverUpdateSerializer already rejected the case where both a
        # file and a URL were given for the same document.
        _resolve_doc_files(serializer, self.request)
        serializer.save()

    def get_serializer_class(self):
        if self.action == "list":
            return DriverListSerializer
        if self.action == "create":
            return DriverCreateSerializer
        if self.action in ("update", "partial_update"):
            return DriverUpdateSerializer
        return DriverSerializer

    @extend_schema(
        tags=["Admin: Drivers"],
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


@extend_schema(
    tags=["Admin: Driver KYC"],
    summary="Get a driver's KYC status",
    description="Returns the Aadhar, driving-licence, and police-verification status/documents for one driver.",
    responses={200: DriverKycSerializer},
)
class DriverKycView(DriverKycMixin, APIView):
    def get(self, request, pk=None):
        return Response(DriverKycSerializer(self.get_driver()).data)
