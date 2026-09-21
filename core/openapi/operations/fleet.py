"""The company's fleet: vehicle types (with fare cards), vehicles, vehicle documents."""

from core.choices import VehicleCategory, VehicleStatus, VehicleTypeStatus
from core.openapi.dsl import COMPANY, doc, document, ex, ok, path_param, query_param, raw_ex
from drivers.vehicle_serializers import (
    VehicleDetailSerializer,
    VehicleDocumentSerializer,
    VehicleListSerializer,
    VehicleSerializer,
    VehicleTypeSerializer,
)
from drivers.vehicle_views import VehicleDocumentViewSet, VehicleTypeViewSet, VehicleViewSet

TYPE_ID = path_param("id", "The vehicle type's id (UUID).")
VEHICLE_ID = path_param("id", "The vehicle's id (UUID).")
PAGING = [
    query_param("page", "Page number, starting at 1.", type=int),
    query_param("page_size", "Rows per page (default 20, maximum 100).", type=int),
]

# -- vehicle types ------------------------------------------------------------------------
NEW_TYPE = {
    "name": "Tempo",
    "category": "three_wheeler",
    "default_capacity_kg": "500.00",
    "base_fare": "60.00",
    "per_km_rate": "12.00",
    "per_min_rate": "1.50",
    "min_fare": "80.00",
}

FARE_NOTE = """
**The fare card.** A trip costs `(base_fare + distance_km x per_km_rate + minutes x per_min_rate)`, and never less than
`min_fare`. **`min_fare` must be above 0** - a vehicle type with `min_fare: 0` is treated as "no fare card yet": trips
cannot be booked against it (`FARE_NOT_CONFIGURED`), and while it is `active` it also makes `POST /trips/estimate` fail for
*all* your vehicle types. Set it to the smallest charge you want to make, or mark the type `inactive` until it is priced.
"""

document(
    VehicleTypeViewSet,
    list=doc(
        id="listVehicleTypes",
        tag="Vehicle types",
        summary="List vehicle types",
        description="Your company's vehicle types, newest first, 20 per page. Filter with `category` and `status`.",
        auth=COMPANY,
        params=[
            query_param("category", "Only this category.", enum=[c for c, _ in VehicleCategory.choices]),
            query_param("status", "Only this status.", enum=[c for c, _ in VehicleTypeStatus.choices]),
            *PAGING,
        ],
        responses={200: ok(VehicleTypeSerializer, ex("vehicle_type.list", "A vehicle type in the list", item=0))},
    ),
    create=doc(
        id="createVehicleType",
        tag="Vehicle types",
        summary="Create a vehicle type",
        description="""
Adds a kind of vehicle you offer (Bike, Tempo, Mini truck ...) together with its fare card. Customers pick a vehicle type
when booking; drivers are matched only to trips for the vehicle type of the vehicle they are on.

`name` must be unique among your vehicle types.
""",
        auth=COMPANY,
        request=VehicleTypeSerializer,
        request_examples=[raw_ex("A three-wheeler with a fare card", NEW_TYPE, request=True)],
        responses={201: ok(VehicleTypeSerializer, ex("vehicle_type.create", "Created"))},
        notes=FARE_NOTE,
    ),
    retrieve=doc(
        id="getVehicleType",
        tag="Vehicle types",
        summary="Get a vehicle type",
        description="One vehicle type with its fare card.",
        auth=COMPANY,
        params=[TYPE_ID],
        by_id=True,
        responses={200: ok(VehicleTypeSerializer, ex("vehicle_type.create", "A vehicle type", statuses=[200]))},
    ),
    update=doc(
        id="replaceVehicleType",
        tag="Vehicle types",
        summary="Replace a vehicle type",
        description="Full update: send every required field. To change just one or two fields use `PATCH` instead.",
        auth=COMPANY,
        params=[TYPE_ID],
        by_id=True,
        request=VehicleTypeSerializer,
        request_examples=[raw_ex("Full replacement", NEW_TYPE, request=True)],
        responses={200: ok(VehicleTypeSerializer, ex("vehicle_type.create", "Updated", statuses=[200]))},
        notes=FARE_NOTE,
    ),
    partial_update=doc(
        id="updateVehicleType",
        tag="Vehicle types",
        summary="Update a vehicle type",
        description="""
Changes only the fields you send. Typical uses: adjust the fare card, or set `status` to `inactive` to stop offering the
type (existing trips are unaffected; it disappears from estimates and can't be booked).

Fare changes apply to **new** trips only - a booked trip keeps the fare it was booked at.
""",
        auth=COMPANY,
        params=[TYPE_ID],
        by_id=True,
        request=VehicleTypeSerializer,
        request_examples=[raw_ex("Raise the per-km rate", {"per_km_rate": "13.50"}, request=True)],
        responses={200: ok(VehicleTypeSerializer, ex("vehicle_type.create", "Updated", statuses=[200]))},
        notes=FARE_NOTE,
    ),
    destroy=doc(
        id="deleteVehicleType",
        tag="Vehicle types",
        summary="Delete a vehicle type",
        description="""
Removes a vehicle type. Refused with `VEHICLE_TYPE_IN_USE` while any of your vehicles still has this type - re-type or
remove those vehicles first. If you only want to stop selling it, `PATCH` its `status` to `inactive` instead.
""",
        auth=COMPANY,
        params=[TYPE_ID],
        by_id=True,
        responses={204: ok(None, description="Deleted. No body.")},
        errors=["VEHICLE_TYPE_IN_USE"],
    ),
)

# -- vehicles ----------------------------------------------------------------------------------
NEW_VEHICLE = {"vehicle_type_id": "725d6bae-1bdc-4a7a-ad03-8ef8ae0c6f13", "registration_number": "KA01AB1234"}

document(
    VehicleViewSet,
    list=doc(
        id="listVehicles",
        tag="Vehicles",
        summary="List vehicles",
        description="Your fleet, newest first, 20 per page. `search` matches part of a registration number.",
        auth=COMPANY,
        params=[
            query_param("category", "Only vehicles whose vehicle type is in this category.", enum=[c for c, _ in VehicleCategory.choices]),
            query_param("status", "Only vehicles in this status.", enum=[c for c, _ in VehicleStatus.choices]),
            query_param("search", "Part of a registration number, e.g. `KA01`. Case-insensitive."),
            *PAGING,
        ],
        responses={200: ok(VehicleListSerializer, ex("vehicle.list", "A vehicle in the list", item=0))},
    ),
    create=doc(
        id="createVehicle",
        tag="Vehicles",
        summary="Add a vehicle",
        description="""
Adds a vehicle to the fleet. Only `vehicle_type_id` and `registration_number` are required:
`capacity_kg` defaults to the vehicle type's `default_capacity_kg`.

The registration number is upper-cased and must use only letters, digits and hyphens (no spaces), and be unique among
your vehicles. The vehicle type must be `active` and one of yours.

Drivers can then choose the vehicle when they go on duty (`POST /driver/duty/start`), if their licence covers its category.
""",
        auth=COMPANY,
        request=VehicleSerializer,
        request_examples=[raw_ex("Minimum", NEW_VEHICLE, request=True), raw_ex("With capacity and photo", {**NEW_VEHICLE, "capacity_kg": "35.50", "photo_url": "https://api.example.com/media/.../vehicles/photo.jpg"}, request=True)],
        responses={201: ok(VehicleSerializer, ex("vehicle.create", "Added"))},
    ),
    retrieve=doc(
        id="getVehicle",
        tag="Vehicles",
        summary="Get a vehicle",
        description="One vehicle with its vehicle type and all its documents.",
        auth=COMPANY,
        params=[VEHICLE_ID],
        by_id=True,
        responses={200: ok(VehicleDetailSerializer, ex("vehicle.detail", "A vehicle with a document"))},
    ),
    update=doc(
        id="replaceVehicle",
        tag="Vehicles",
        summary="Replace a vehicle",
        description="Full update: send every required field. To change one field use `PATCH`.",
        auth=COMPANY,
        params=[VEHICLE_ID],
        by_id=True,
        request=VehicleSerializer,
        request_examples=[raw_ex("Full replacement", {**NEW_VEHICLE, "capacity_kg": "25.00"}, request=True)],
        responses={200: ok(VehicleSerializer, ex("vehicle.create", "Updated", statuses=[200]))},
    ),
    partial_update=doc(
        id="updateVehicle",
        tag="Vehicles",
        summary="Update a vehicle",
        description="Changes only the fields you send (`capacity_kg`, `photo_url`, `registration_number`, `vehicle_type_id`).",
        auth=COMPANY,
        params=[VEHICLE_ID],
        by_id=True,
        request=VehicleSerializer,
        request_examples=[raw_ex("Change the capacity", {"capacity_kg": "25.00"}, request=True)],
        responses={200: ok(VehicleSerializer, ex("vehicle.create", "Updated", statuses=[200]))},
    ),
    destroy=doc(
        id="deleteVehicle",
        tag="Vehicles",
        summary="Delete a vehicle",
        description="""
**Permanently** deletes the vehicle and its documents. Past trips are kept but no longer point at it.
Prefer `POST /vehicles/{id}/disable`, which retires the vehicle safely.
""",
        auth=COMPANY,
        params=[VEHICLE_ID],
        by_id=True,
        responses={204: ok(None, description="Deleted. No body.")},
    ),
    disable=doc(
        id="disableVehicle",
        tag="Vehicles",
        summary="Retire a vehicle",
        description="""
Takes the vehicle out of service: its status becomes `disabled` and it drops out of your fleet (it is no longer listed and
drivers can't pick it). Refused with `VEHICLE_HAS_ACTIVE_TRIP` while a trip is using it.
""",
        auth=COMPANY,
        params=[VEHICLE_ID],
        by_id=True,
        request=None,
        responses={200: ok(VehicleDetailSerializer, ex("vehicle.detail", "Retired", statuses=[200]))},
        errors=["VEHICLE_HAS_ACTIVE_TRIP"],
    ),
)

# -- vehicle documents ---------------------------------------------------------------------------
VEHICLE_PK = path_param("vehicle_pk", "The vehicle's id (UUID).")
NEW_DOC = {"document_type": "insurance", "file_url": "https://files.example.com/insurance-KA01AB1234.pdf", "expiry_date": "2027-04-09"}

document(
    VehicleDocumentViewSet,
    list=doc(
        id="listVehicleDocuments",
        tag="Vehicle documents",
        summary="List a vehicle's documents",
        description="Registration certificate, insurance, fitness and other papers attached to the vehicle, newest first.",
        auth=COMPANY,
        params=[VEHICLE_PK, *PAGING],
        by_id=True,
        responses={200: ok(VehicleDocumentSerializer, ex("vehicle_doc.list", "A document in the list", item=0))},
    ),
    create=doc(
        id="addVehicleDocument",
        tag="Vehicle documents",
        summary="Attach a document to a vehicle",
        description="""
Records a document for the vehicle. Upload the file first with `POST /uploads` (`purpose: vehicle_document`) and pass the
URL it returns as `file_url`; or use any `https` link you host.

**`expiry_date` is required for `insurance` and `fitness`** documents (so you can be reminded before they lapse) and optional for
the rest (`rc`, `purchase`, `other`).
""",
        auth=COMPANY,
        params=[VEHICLE_PK],
        by_id=True,
        request=VehicleDocumentSerializer,
        request_examples=[raw_ex("Insurance (expiry required)", NEW_DOC, request=True), raw_ex("Registration certificate (no expiry)", {"document_type": "rc", "file_url": "https://files.example.com/rc-KA01AB1234.pdf"}, request=True)],
        responses={201: ok(VehicleDocumentSerializer, ex("vehicle_doc.create", "Attached"))},
    ),
    partial_update=doc(
        id="updateVehicleDocument",
        tag="Vehicle documents",
        summary="Update a vehicle document",
        description="Replace the file or change the expiry date (for example after renewing the insurance). Documents cannot be deleted through the API.",
        auth=COMPANY,
        params=[VEHICLE_PK, path_param("id", "The document's id (UUID).")],
        by_id=True,
        request=VehicleDocumentSerializer,
        request_examples=[raw_ex("Renewed", {"expiry_date": "2028-04-09"}, request=True)],
        responses={200: ok(VehicleDocumentSerializer, ex("vehicle_doc.create", "Updated", statuses=[200]))},
    ),
)
