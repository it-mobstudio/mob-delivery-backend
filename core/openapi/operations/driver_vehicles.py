"""The driver's own vehicles, with pictures."""

from core.openapi.dsl import DRIVER, doc, document, ok, path_param, raw_ex
from drivers.driver_vehicle_serializers import (
    DriverOwnVehicleSerializer,
    DriverOwnVehiclesSerializer,
    DriverVehiclePhotoUploadSerializer,
    DriverVehicleTypesSerializer,
    DriverVehicleUpdateSerializer,
    DriverVehicleWriteSerializer,
)
from drivers.driver_vehicle_views import (
    DriverMyVehicleDetailView,
    DriverMyVehiclePhotoDetailView,
    DriverMyVehiclePhotosView,
    DriverMyVehiclesView,
    DriverVehicleTypesView,
)

TAG = "Driver vehicles"
MULTIPART = "multipart/form-data"
VEHICLE_ID = path_param("id", "The vehicle's id (UUID) - from `GET /driver/my-vehicles`.")
PHOTO_ID = path_param("photo_id", "The picture's id (UUID) - one of the vehicle's `photos[].id`.")

VEHICLE = {
    "id": "8f0c2b5e-3a1d-4e57-9c0a-6d1f2e7b9a44",
    "vehicle_type": {"id": "725d6bae-1bdc-4a7a-ad03-8ef8ae0c6f13", "name": "Bike", "category": "two_wheeler", "icon_image_url": None},
    "registration_number": "KA05MN7788",
    "capacity_kg": "20.00",
    "photo_url": "https://your-server.example.com/media/3b79.../vehicles/6f1c....jpg",
    "photos": [
        {"id": "b1d7c0a2-52c4-4a58-8a0e-0b3c7f1e2d10", "url": "https://your-server.example.com/media/3b79.../vehicles/6f1c....jpg"},
        {"id": "e4a9f3d8-7b21-4c96-b0d5-9a8e1c2f4b67", "url": "https://your-server.example.com/media/3b79.../vehicles/a93e....jpg"},
    ],
    "status": "active",
    "is_current": False,
    "created_at": "2026-09-21T09:20:11.482910Z",
}

INTRO = """
**Own vehicles.** A driver can register their own vehicle(s) - up to **10**, each with up to **6 pictures** - and then take any of them on duty, next to the company's fleet
vehicles. Only the driver who registered a vehicle can see, change or use it; the company sees it in its fleet with `owner_driver_id` set and can disable it.
"""

document(
    DriverVehicleTypesView,
    get=doc(
        id="driverListVehicleTypes",
        tag=TAG,
        summary="Vehicle types to choose from",
        description="The company's **active** vehicle types (Bike, Tempo, ...), for the \"add a vehicle\" form. Send the chosen type's `id` as `vehicle_type_id` when registering a vehicle.",
        auth=DRIVER,
        responses={
            200: ok(
                DriverVehicleTypesSerializer,
                raw_ex("types", {"vehicle_types": [{"id": "725d6bae-1bdc-4a7a-ad03-8ef8ae0c6f13", "name": "Bike", "category": "two_wheeler", "default_capacity_kg": "20.00", "icon_image_url": None}]}, "Two types the company runs"),
            )
        },
    ),
)

document(
    DriverMyVehiclesView,
    get=doc(
        id="listMyVehicles",
        tag=TAG,
        summary="My vehicles",
        description="The vehicles this driver registered, newest first, each with its pictures. (`GET /driver/vehicles` is different: it lists what the driver may take **on duty**, which includes the company's fleet.)",
        auth=DRIVER,
        responses={200: ok(DriverOwnVehiclesSerializer, raw_ex("mine", {"vehicles": [VEHICLE]}, "One registered vehicle"))},
        notes=INTRO,
    ),
    post=doc(
        id="registerMyVehicle",
        tag=TAG,
        summary="Add a vehicle",
        description="""
Registers a vehicle for the driver. **Required:** `vehicle_type_id` and `registration_number`. **Optional:** `capacity_kg` (defaults to the type's) and `photos`.

Send it as **`multipart/form-data`**, repeating the `photos` part once per picture:

```bash
curl -X POST "$BASE_URL/driver/my-vehicles" -H "Authorization: Bearer $DRIVER_TOKEN" \\
  -F "vehicle_type_id=725d6bae-1bdc-4a7a-ad03-8ef8ae0c6f13" -F "registration_number=KA05MN7788" \\
  -F "photos=@front.jpg" -F "photos=@side.jpg" -F "photos=@back.jpg"
```

The plate is upper-cased and must be letters, digits and hyphens (no spaces), unique in the company. All pictures are checked **before** anything is saved, so one bad file adds nothing.
The first picture becomes the vehicle's main one. A driver can register this even while still being onboarded; going on duty still needs their documents verified.
""",
        auth=DRIVER,
        request={MULTIPART: DriverVehicleWriteSerializer},
        responses={201: ok(DriverOwnVehicleSerializer, raw_ex("registered", VEHICLE, "Registered with two pictures"))},
        errors=["VEHICLE_LIMIT_REACHED", "INVALID_UPLOAD"],
        notes=INTRO,
    ),
)

document(
    DriverMyVehicleDetailView,
    get=doc(
        id="getMyVehicle",
        tag=TAG,
        summary="One of my vehicles",
        description="A vehicle the driver registered, with its pictures. Someone else's vehicle is a plain `404`.",
        auth=DRIVER,
        params=[VEHICLE_ID],
        by_id=True,
        responses={200: ok(DriverOwnVehicleSerializer, raw_ex("vehicle", VEHICLE, "A vehicle"))},
    ),
    patch=doc(
        id="updateMyVehicle",
        tag=TAG,
        summary="Correct a vehicle",
        description="Changes only the fields sent: the plate (to fix a typo), the capacity, or the type. Pictures have their own endpoints.",
        auth=DRIVER,
        params=[VEHICLE_ID],
        by_id=True,
        request=DriverVehicleUpdateSerializer,
        request_examples=[raw_ex("Fix the plate", {"registration_number": "KA05MN7789"}, request=True), raw_ex("Change the capacity", {"capacity_kg": "35.5"}, request=True)],
        responses={200: ok(DriverOwnVehicleSerializer, raw_ex("updated", VEHICLE, "Updated"))},
    ),
    delete=doc(
        id="removeMyVehicle",
        tag=TAG,
        summary="Remove a vehicle",
        description="""
Retires the vehicle: it drops out of the driver's list and the company's fleet, and its plate can be registered again. Past trips keep their record.

Refused while the driver is **on duty with it** (`VEHICLE_ON_DUTY` - go off duty first) or while a **trip is using it** (`VEHICLE_HAS_ACTIVE_TRIP`).
""",
        auth=DRIVER,
        params=[VEHICLE_ID],
        by_id=True,
        responses={204: ok(None, description="Removed. No body.")},
        errors=["VEHICLE_ON_DUTY", "VEHICLE_HAS_ACTIVE_TRIP"],
    ),
)

document(
    DriverMyVehiclePhotosView,
    post=doc(
        id="addMyVehiclePhoto",
        tag=TAG,
        summary="Add a picture",
        description="Adds one picture to a vehicle (max 6 - `PHOTO_LIMIT_REACHED` beyond that). If it is the vehicle's first, it becomes the main picture. Send as `multipart/form-data`. Returns the whole vehicle.",
        auth=DRIVER,
        params=[VEHICLE_ID],
        by_id=True,
        request={MULTIPART: DriverVehiclePhotoUploadSerializer},
        responses={201: ok(DriverOwnVehicleSerializer, raw_ex("with_photo", VEHICLE, "The vehicle with the new picture"))},
        errors=["PHOTO_LIMIT_REACHED", "INVALID_UPLOAD"],
    ),
)

document(
    DriverMyVehiclePhotoDetailView,
    delete=doc(
        id="removeMyVehiclePhoto",
        tag=TAG,
        summary="Remove a picture",
        description="Removes one picture. If it was the main one, the next picture becomes the main one (or `photo_url` becomes `null` when none is left). Returns the whole vehicle.",
        auth=DRIVER,
        params=[VEHICLE_ID, PHOTO_ID],
        by_id=True,
        responses={200: ok(DriverOwnVehicleSerializer, raw_ex("without_photo", VEHICLE, "The vehicle after the removal"))},
    ),
)
