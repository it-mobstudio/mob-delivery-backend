from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import DomainError

from .serializers import UploadSerializer
from .services import store_upload


@extend_schema(
    summary="Upload a file",
    description=(
        "The one reusable multipart upload endpoint used across the API — vehicle-type icons, "
        "vehicle photos, document scans, KYC documents, damage-report/proof photos, and more, "
        "distinguished by the `purpose` field. Stores to Azure Blob if configured, otherwise "
        "local filesystem storage for dev. Returns `{\"url\": ...}`; the caller passes that URL "
        "into a separate, later request that creates/updates the actual record (e.g. "
        "registering a vehicle document)."
    ),
)
class UploadView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            url = store_upload(
                serializer.validated_data["file"],
                serializer.validated_data["purpose"],
                request.user.company_id,
            )
        except DjangoValidationError as exc:
            raise DomainError("INVALID_UPLOAD", "; ".join(exc.messages), status_code=400)

        return Response({"url": url}, status=status.HTTP_201_CREATED)
