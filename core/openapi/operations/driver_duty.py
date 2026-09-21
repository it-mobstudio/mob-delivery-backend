"""Going on and off duty, and GPS pings."""

from core.openapi.dsl import DRIVER, doc, document, ex, ok, raw_ex
from core.serializers import MessageSerializer
from drivers.serializers import (
    DriverAvailableVehiclesSerializer,
    DriverDutyOnSerializer,
    DriverLocationSerializer,
    DriverMeSerializer,
)
from drivers.views import DriverDutyEndView, DriverDutyStartView, DriverLocationView, DriverVehicleListView

TAG = "Driver duty & location"

document(
    DriverVehicleListView,
    get=doc(
        id="driverListVehicles",
        tag=TAG,
        summary="Vehicles I can take on duty",
        description="""
The vehicle picker: active vehicles of the driver's company whose category their **verified licence covers** and which no other driver is using right now.
The vehicle the driver is on duty with already is flagged `is_current: true` so it can be pre-selected. An unverified driver gets an empty list.
""",
        auth=DRIVER,
        responses={200: ok(DriverAvailableVehiclesSerializer, ex("driver_vehicles.ready", "Two vehicles to choose from"))},
    ),
)

document(
    DriverDutyStartView,
    post=doc(
        id="driverStartDuty",
        tag=TAG,
        summary="Go on duty",
        description="""
Puts the driver **online** on a vehicle, so trips for that vehicle's type can be matched to them. Send the driver's first GPS fix (`lat` + `lng`, together)
in the same call so they can be matched **immediately** - otherwise they are only matched after their first location ping.

Checks, in order: the driver is verified and active (`DRIVER_NOT_ELIGIBLE`), their licence covers the vehicle's category
(`VEHICLE_CATEGORY_NOT_ALLOWED`), the vehicle is active (`VEHICLE_NOT_ACTIVE`) and nobody else is on it (`VEHICLE_IN_USE`). Switching to a different vehicle while
on a trip is refused (`DRIVER_HAS_ACTIVE_TRIP`). Returns the updated profile.
""",
        auth=DRIVER,
        request=DriverDutyOnSerializer,
        request_examples=[
            raw_ex("With the first GPS fix", {"vehicle_id": "34dbaab9-e77f-466a-93f3-3637131968ea", "lat": "12.971600", "lng": "77.594600"}, request=True),
            raw_ex("Without a location", {"vehicle_id": "34dbaab9-e77f-466a-93f3-3637131968ea"}, request=True),
        ],
        responses={200: ok(DriverMeSerializer, ex("duty.start", "Online"))},
        errors=["DRIVER_NOT_ELIGIBLE", "VEHICLE_CATEGORY_NOT_ALLOWED", "VEHICLE_NOT_ACTIVE", "VEHICLE_IN_USE", "DRIVER_HAS_ACTIVE_TRIP"],
    ),
)

document(
    DriverDutyEndView,
    post=doc(
        id="driverEndDuty",
        tag=TAG,
        summary="Go off duty",
        description="Takes the driver **offline** so no new trips are matched to them. Refused while they have an active trip (`DRIVER_HAS_ACTIVE_TRIP`). Returns the updated profile.",
        auth=DRIVER,
        responses={200: ok(DriverMeSerializer, ex("duty.end", "Offline"))},
        errors=["DRIVER_HAS_ACTIVE_TRIP"],
    ),
)

document(
    DriverLocationView,
    post=doc(
        id="driverSendLocation",
        tag=TAG,
        summary="Send my GPS position",
        description="""
The app calls this **every 15-30 seconds while the driver is on duty**. The latest position is what matching uses: a trip is only offered to drivers
whose last position is within 8 km of the pickup, and a stale position is not treated as gone, so keep pinging.
""",
        auth=DRIVER,
        request=DriverLocationSerializer,
        request_examples=[raw_ex("A position", {"lat": "12.971600", "lng": "77.594600"}, request=True)],
        responses={200: ok(MessageSerializer, ex("location", "Recorded"))},
    ),
)
