"""The driver's trip: what to work on, and the step-by-step lifecycle actions."""

from core.choices import TripStatus
from core.openapi.dsl import DRIVER, derived_ex, doc, document, ex, ok, path_param, query_param, raw_ex
from trips.serializers import (
    ActiveTripSerializer,
    DriverTripListSerializer,
    DriverTripSerializer,
    NavigationRouteSerializer,
    TripCancelSerializer,
    TripCompleteSerializer,
    TripItemVerifySerializer,
    TripPickupPhotoSerializer,
)
from trips.views import (
    DriverActiveTripView,
    DriverTripArriveView,
    DriverTripCancelView,
    DriverTripCompleteView,
    DriverTripDetailView,
    DriverTripItemVerifyView,
    DriverTripListView,
    DriverTripNavigationView,
    DriverTripDeliveryPhotoView,
    DriverTripPickupPhotoView,
    DriverTripStartView,
)

TAG = "Driver trips"
ITEMS = "Item verification"
TRIP_ID = path_param("id", "The trip's id (UUID) - from `GET /driver/trips/active` or the trip list.")
ITEM_ID = path_param("item_id", "The item's id (UUID) - one of the trip's `items[].id`.")

document(
    DriverActiveTripView,
    get=doc(
        id="driverGetActiveTrip",
        tag=TAG,
        summary="The trip I'm working on",
        description="""
Returns `{"trip": {...}}` for the trip that is `assigned`, `arrived_at_pickup` or `in_progress` for this driver, or `{"trip": null}` when there
isn't one. **This is how the app finds out it was given a trip** - there are no push notifications yet, so call it every few seconds
while the driver is online and idle, and after every action.

The trip includes everything the screens need: addresses and contacts, the route polyline, the fare, `driver_earning`, the invoice link and the
item checklist. A driver is never assigned more than one trip at a time.
""",
        auth=DRIVER,
        responses={200: ok(ActiveTripSerializer, ex("driver_trip.active", "A trip is assigned"))},
    ),
)

document(
    DriverTripListView,
    get=doc(
        id="driverListTrips",
        tag=TAG,
        summary="My trip history",
        description="""
The driver's trips, **newest first**, 20 per page - a lighter row than the full trip (no route polyline) with the earning, timing, distance and
cancellation fields the history cards show. Open one with `GET /driver/trips/{id}`.
""",
        auth=DRIVER,
        params=[
            query_param("status", "Only these statuses, comma-separated: " + ", ".join(f"`{c}`" for c, _ in TripStatus.choices) + ". Example: `completed,cancelled`."),
            query_param("page", "Page number, starting at 1.", type=int),
            query_param("page_size", "Rows per page (default 20, maximum 100).", type=int),
        ],
        responses={200: ok(DriverTripListSerializer, ex("driver_trip.list", "A trip in the history", item=0))},
    ),
)

document(
    DriverTripDetailView,
    get=doc(
        id="driverGetTrip",
        tag=TAG,
        summary="Get one of my trips",
        description="The full trip, including finished ones. A trip that isn't this driver's is a `404`, never a glimpse of someone else's.",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        responses={200: ok(DriverTripSerializer, ex("driver_trip.detail", "A trip"))},
    ),
)

document(
    DriverTripNavigationView,
    get=doc(
        id="driverGetNavigation",
        tag=TAG,
        summary="Route to my next stop",
        description="""
The route the driver still has to drive, **from where they are now** (`lat`/`lng`) to the trip's next stop: the **pickup** until they have started the delivery,
the **drop** after. (The trip's own `route_polyline` only covers pickup -> drop; this is the "get me to the pickup" part.)
The polyline is encoded at 6 decimal places, not Google's 5.
""",
        auth=DRIVER,
        params=[
            TRIP_ID,
            query_param("lat", "The driver's current latitude, decimal degrees (-90 to 90).", type=float, required=True),
            query_param("lng", "The driver's current longitude, decimal degrees (-180 to 180).", type=float, required=True),
        ],
        by_id=True,
        responses={200: ok(NavigationRouteSerializer, ex("driver_trip.navigation", "Route to the pickup"))},
        errors=["TRIP_NOT_ACTIVE", "ROUTING_UNAVAILABLE"],
        validates=True,
    ),
)

document(
    DriverTripArriveView,
    post=doc(
        id="driverArrive",
        tag=TAG,
        summary="I've reached the pickup",
        description="Moves the trip `assigned` -> `arrived_at_pickup` and records `arrived_at_pickup_at`. No body.",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        responses={200: ok(DriverTripSerializer, ex("driver_trip.arrive", "At the pickup"))},
        errors=["INVALID_TRIP_STATUS_TRANSITION", "NOT_YOUR_TRIP"],
    ),
)

document(
    DriverTripStartView,
    post=doc(
        id="driverStart",
        tag=TAG,
        summary="I've picked up - start the delivery",
        description="Moves the trip `arrived_at_pickup` -> `in_progress` and records `started_at`. From here the trip can no longer be cancelled. No body.",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        responses={200: ok(DriverTripSerializer, ex("driver_trip.start", "On the way to the drop"))},
        errors=["PICKUP_PHOTOS_REQUIRED", "INVALID_TRIP_STATUS_TRANSITION", "NOT_YOUR_TRIP"],
    ),
)

document(
    DriverTripCompleteView,
    post=doc(
        id="driverComplete",
        tag=TAG,
        summary="Hand over and complete the delivery",
        description="""
Finishes the trip (`in_progress` -> `completed`), records `completed_at`, and **credits the driver's wallet** with their share of the fare in the same step.

**Prepaid** trip: send an empty body. **Cash on delivery**: the fare must have been paid (see *Driver payments*) and `otp` - the 4-digit code texted to the customer
- is **required**; it proves the customer received the goods. If the order asked for item verification, every item must be answered first.

The checks run in this order, so the first that fails is the one you see: `ITEMS_NOT_VERIFIED` -> `PAYMENT_NOT_COLLECTED` -> `INVALID_DELIVERY_OTP` -> status.
""",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        request=TripCompleteSerializer,
        request_examples=[
            raw_ex("Cash on delivery: the customer's code", {"otp": "3874"}, request=True),
            raw_ex("Prepaid: no body needed", {}, request=True),
        ],
        responses={200: ok(DriverTripSerializer, ex("driver_trip.complete", "Completed - the driver earned 89.30"))},
        errors=["ITEMS_NOT_VERIFIED", "DELIVERY_PHOTOS_REQUIRED", "PAYMENT_NOT_COLLECTED", "INVALID_DELIVERY_OTP", "INVALID_TRIP_STATUS_TRANSITION", "NOT_YOUR_TRIP"],
        validates=True,
    ),
)

document(
    DriverTripCancelView,
    post=doc(
        id="driverCancel",
        tag=TAG,
        summary="Cancel the trip",
        description="""
The driver gives the trip up, with a `reason` (the company sees it). Allowed while the trip is `assigned` or `arrived_at_pickup`; once the delivery is in progress it
can no longer be cancelled (`TRIP_NOT_CANCELLABLE`). The trip becomes `cancelled` with `cancelled_by: driver` and is **not** re-offered automatically - the company
can retry assignment.
""",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        request=TripCancelSerializer,
        request_examples=[raw_ex("Vehicle problem", {"reason": "Vehicle breakdown"}, request=True)],
        responses={200: ok(DriverTripSerializer, ex("driver_trip.cancel", "Cancelled by the driver"))},
        errors=["TRIP_NOT_CANCELLABLE", "NOT_YOUR_TRIP"],
    ),
)

# -- item verification ---------------------------------------------------------------------------
VERIFY_INTRO = """
Only for orders booked with `verify_items: true`. At the drop, once the delivery is `in_progress`, the driver answers for **every item**; until they all have
answers, payment and completion are refused (`ITEMS_NOT_VERIFIED`). Each answer is kept, with its time and photo, as the delivery history the company can read.
"""

document(
    DriverTripItemVerifyView,
    post=doc(
        id="driverVerifyItem",
        tag=ITEMS,
        summary="Mark an item delivered or not delivered",
        description="""
The driver's answer for **one item**: `delivered`, or `not_delivered` (which needs a `note` saying what happened). A **photo** taken with the phone's camera is
optional but encouraged as proof. Answering again replaces the earlier answer (a mis-tap can be fixed until the trip is completed); a new photo replaces the old one and
sending none keeps it.

Send as **`multipart/form-data`**. The response is the **whole trip**, so the checklist and its "all verified" state come from one source.
""",
        auth=DRIVER,
        params=[TRIP_ID, ITEM_ID],
        by_id=True,
        request={"multipart/form-data": TripItemVerifySerializer},
        responses={
            200: ok(
                DriverTripSerializer,
                ex("item.verify.delivered", "Marked delivered, with a photo"),
                ex("item.verify.not_delivered", "Marked not delivered, with a note"),
            )
        },
        errors=["VERIFICATION_NOT_REQUESTED", "TRIP_NOT_IN_PROGRESS", "ITEM_NOT_FOUND", "NOT_YOUR_TRIP", "INVALID_UPLOAD"],
        notes=VERIFY_INTRO,
    ),
    delete=doc(
        id="driverResetItem",
        tag=ITEMS,
        summary="Take an item's answer back",
        description="Puts the item back to `pending` and drops its note and photo - for an answer given by mistake. Returns the whole trip. No body.",
        auth=DRIVER,
        params=[TRIP_ID, ITEM_ID],
        by_id=True,
        responses={200: ok(DriverTripSerializer, ex("item.reset", "Back to pending"))},
        errors=["VERIFICATION_NOT_REQUESTED", "TRIP_NOT_IN_PROGRESS", "ITEM_NOT_FOUND", "NOT_YOUR_TRIP"],
        notes=VERIFY_INTRO,
    ),
)

# -- pickup photos -------------------------------------------------------------------------------
document(
    DriverTripPickupPhotoView,
    post=doc(
        id="driverAddPickupPhoto",
        tag=ITEMS,
        summary="Add a pickup photo",
        description="""
For orders booked with `pickup_photo: order` or `per_item`. Before the delivery starts (`assigned` or `arrived_at_pickup`) the driver photographs what they
are taking, **with the phone's camera** (the app never offers the gallery, and stamps each photo with the location and time): one photo of the whole package
(`order`), or one of every item (`per_item`, with `item_id`). Until all are in, `POST /driver/trips/{id}/start` answers `PICKUP_PHOTOS_REQUIRED`.
A new photo for the same slot replaces the old one.

Send as **`multipart/form-data`**. The response is the **whole trip** (`pickup_photo_url`, `items[].pickup_photo_url`).
""",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        request={"multipart/form-data": TripPickupPhotoSerializer},
        responses={200: ok(DriverTripSerializer, ex("driver_trip.pickup_photo", "The order photo added"))},
        errors=["PICKUP_PHOTO_NOT_REQUESTED", "INVALID_TRIP_STATUS_TRANSITION", "ITEM_NOT_FOUND", "NOT_YOUR_TRIP", "INVALID_UPLOAD"],
    ),
)

document(
    DriverTripDeliveryPhotoView,
    post=doc(
        id="driverAddDeliveryPhoto",
        tag=ITEMS,
        summary="Add a delivery photo",
        description="""
For orders booked with `delivery_photo: order` or `per_item`. At the drop, while the delivery is `in_progress`, the driver photographs what they handed over,
**with the phone's camera** (stamped with the location and time): one photo of the whole order (`order`), or one of every item (`per_item`, with `item_id`).
Until all are in, the payment QR, payment check and completion answer `DELIVERY_PHOTOS_REQUIRED`. A new photo for the same slot replaces the old one.

Send as **`multipart/form-data`**. The response is the **whole trip** (`delivery_photo_url`, `items[].delivery_photo_url`).
""",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        request={"multipart/form-data": TripPickupPhotoSerializer},
        responses={200: ok(DriverTripSerializer, ex("driver_trip.delivery_photo", "The order photo added"))},
        errors=["DELIVERY_PHOTO_NOT_REQUESTED", "INVALID_TRIP_STATUS_TRANSITION", "ITEM_NOT_FOUND", "NOT_YOUR_TRIP", "INVALID_UPLOAD"],
    ),
)
