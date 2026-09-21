from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.choices import VehicleTypeStatus

from .driver_vehicle_serializers import (
    DriverOwnVehicleSerializer,
    DriverVehiclePhotoUploadSerializer,
    DriverVehicleTypeSerializer,
    DriverVehicleUpdateSerializer,
    DriverVehicleWriteSerializer,
)
from .models import VehicleType
from .permissions import IsDriverUser
from .vehicle_services import DriverVehicleService


def _one(request, vehicle):
    return DriverOwnVehicleSerializer(vehicle, context={"request": request}).data


class DriverVehicleTypesView(APIView):
    """GET /api/v1/driver/vehicle-types - what the company runs, for the "add a vehicle" form."""

    permission_classes = [IsDriverUser]

    def get(self, request, *args, **kwargs):
        types = VehicleType.objects.filter(company_id=request.user.company_id, status=VehicleTypeStatus.ACTIVE).order_by("category", "name")
        return Response({"vehicle_types": DriverVehicleTypeSerializer(types, many=True, context={"request": request}).data})


class DriverMyVehiclesView(APIView):
    """GET /api/v1/driver/my-vehicles; POST registers one (multipart: pictures as repeated `photos`)."""

    permission_classes = [IsDriverUser]
    parser_classes = [MultiPartParser, JSONParser]

    def get(self, request, *args, **kwargs):
        vehicles = DriverVehicleService.own_vehicles(request.user)
        return Response({"vehicles": DriverOwnVehicleSerializer(vehicles, many=True, context={"request": request}).data})

    def post(self, request, *args, **kwargs):
        serializer = DriverVehicleWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        vehicle = DriverVehicleService.create(
            request.user,
            data["vehicle_type"],
            data["registration_number"],
            data.get("capacity_kg"),
            data.get("photos", []),
        )
        return Response(_one(request, DriverVehicleService.get(request.user, vehicle.pk)), status=status.HTTP_201_CREATED)


class DriverMyVehicleDetailView(APIView):
    permission_classes = [IsDriverUser]
    parser_classes = [MultiPartParser, JSONParser]

    def get(self, request, pk=None):
        return Response(_one(request, DriverVehicleService.get(request.user, pk)))

    def patch(self, request, pk=None):
        vehicle = DriverVehicleService.get(request.user, pk)
        serializer = DriverVehicleUpdateSerializer(vehicle, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        DriverVehicleService.update(vehicle, **serializer.validated_data)
        return Response(_one(request, DriverVehicleService.get(request.user, pk)))

    def delete(self, request, pk=None):
        DriverVehicleService.remove(request.user, DriverVehicleService.get(request.user, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class DriverMyVehiclePhotosView(APIView):
    """POST /api/v1/driver/my-vehicles/{id}/photos - add one picture (multipart `photo`)."""

    permission_classes = [IsDriverUser]
    parser_classes = [MultiPartParser]

    def post(self, request, pk=None):
        vehicle = DriverVehicleService.get(request.user, pk)
        serializer = DriverVehiclePhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DriverVehicleService.add_photo(vehicle, serializer.validated_data["photo"])
        return Response(_one(request, DriverVehicleService.get(request.user, pk)), status=status.HTTP_201_CREATED)


class DriverMyVehiclePhotoDetailView(APIView):
    permission_classes = [IsDriverUser]

    def delete(self, request, pk=None, photo_id=None):
        vehicle = DriverVehicleService.get(request.user, pk)
        DriverVehicleService.remove_photo(vehicle, photo_id)
        return Response(_one(request, DriverVehicleService.get(request.user, pk)))
