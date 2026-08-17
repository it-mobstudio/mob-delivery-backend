from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from drivers.permissions import IsDriverUser

from . import services
from .serializers import DriverDeviceSerializer, RegisterDeviceSerializer


@extend_schema(
    tags=["notifications"],
    summary="Register a device for push notifications",
    description="Registers (or upserts, if the FCM token already exists) a driver's device for push notifications. Driver-only.",
)
class DeviceRegisterView(APIView):
    permission_classes = [IsDriverUser]

    def post(self, request):
        serializer = RegisterDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = services.register_device(driver=request.user, **serializer.validated_data)
        return Response(DriverDeviceSerializer(device).data, status=201)


@extend_schema(
    tags=["notifications"],
    summary="Deregister a device from push notifications",
    description="Removes one FCM token from the calling driver's registered devices — e.g. on logout.",
)
class DeviceDeregisterView(APIView):
    permission_classes = [IsDriverUser]

    def delete(self, request, token=None):
        services.deregister_device(driver=request.user, fcm_token=token)
        return Response(status=204)
