from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import DomainError

from .serializers import UploadSerializer
from .services import store_upload


class UploadView(APIView):
    """POST /api/v1/uploads — the one reusable upload endpoint for VehicleType
    icons, Vehicle photos, and VehicleDocument scans. Returns {"url": ...};
    the frontend passes that URL along in a separate, later call that
    creates/updates the actual record.
    """

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
