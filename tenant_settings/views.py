from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser

from . import services
from .models import TenantSetting
from .serializers import TenantSettingSerializer, UpdateTenantSettingSerializer


@extend_schema(
    tags=["Admin: Settings"],
    summary="List tenant settings",
    description=(
        "Lists this company's configurable per-tenant knobs and their current values — "
        "`assignment_window_minutes`, `stationary_radius_meters`, `stationary_duration_minutes`, "
        "`wrong_direction_degrees`, `geofence_meters`, `document_expiry_warning_days`. Seeded "
        "with defaults at company bootstrap; only present keys are configurable."
    ),
)
class TenantSettingListView(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = TenantSettingSerializer

    def get_queryset(self):
        return TenantSetting.objects.filter(company_id=self.request.user.company_id)


@extend_schema(
    tags=["Admin: Settings"],
    summary="Update a tenant setting",
    description="Upserts one setting's value by key for the caller's company (e.g. `geofence_meters`).",
)
class TenantSettingUpdateView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, key=None):
        serializer = UpdateTenantSettingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        setting = services.set_tenant_setting(request.user.company_id, key, serializer.validated_data["value"])
        return Response(TenantSettingSerializer(setting).data)
