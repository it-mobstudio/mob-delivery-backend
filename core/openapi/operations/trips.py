"""Company-side trips: estimate, book, list, read, assign, cancel."""

from core.choices import TripStatus
from core.openapi.dsl import (
    COMPANY,
    derived_ex,
    doc,
    document,
    ex,
    ok,
    path_param,
    query_param,
    raw_ex,
)
from trips.serializers import (
    TripCancelSerializer,
    TripCreateSerializer,
    TripEstimateRequestSerializer,
    TripEstimatesSerializer,
    TripListSerializer,
    TripSerializer,
)
from trips.views import TripEstimateView, TripViewSet

TAG = "Trips"
TRIP_ID = path_param("id", "The trip's id (UUID) - the `id` returned when it was booked.")

document(
    TripEstimateView,
    post=doc(
        id="estimateTrip",
        tag=TAG,
        summary="Get a price for every vehicle type",
        description="""
Prices a delivery **without booking it**. Give a pickup and a drop and you get back one option per
active vehicle type in your fleet - route distance, driving time, and the fare broken into
`base_fare`, `distance_fare` and `time_fare` - so you can show a "choose your vehicle" screen.

Then book the option the customer picked with `POST /trips`, passing that option's `vehicle_type_id`.
The price you were shown is the price the trip gets, as long as the same pickup and drop are used.

Nothing is stored and no driver is contacted, so it is safe to call as often as you like.
""",
        auth=COMPANY,
        request=TripEstimateRequestSerializer,
        request_examples=[
            raw_ex(
                "MG Road to Indiranagar",
                {
                    "pickup": {"address": "MG Road Metro Station, Bengaluru", "lat": "12.975000", "lng": "77.605000"},
                    "drop": {"address": "12 Indiranagar 100ft Road, Bengaluru", "lat": "12.978300", "lng": "77.640800"},
                },
                request=True,
            )
        ],
        responses={200: ok(TripEstimatesSerializer, ex("trip.estimate", "Two vehicle types"))},
        errors=["ROUTING_UNAVAILABLE", "FARE_NOT_CONFIGURED"],
        notes="Fares in an estimate are JSON **numbers** (`85.0`); fares on a trip are decimal **strings** (`\"85.00\"`). See *Conventions*.",
    ),
)

document(
    TripViewSet,
    list=doc(
        id="listTrips",
        tag=TAG,
        summary="List your trips",
        description="""
Every trip your company has booked, **newest first**, 20 per page (`page`, `page_size`).
Filter by `status`, `vehicle_type` and `driver`; filters combine (all must match).

Each row is a compact summary. Fetch a single trip with `GET /trips/{id}` for the route, the full fare
breakdown, timestamps and item list.
""",
        auth=COMPANY,
        params=[
            query_param("status", "Only trips in this status.", enum=[c for c, _ in TripStatus.choices]),
            query_param("vehicle_type", "Only trips booked against this vehicle type (UUID).", type=str),
            query_param("driver", "Only trips assigned to this driver (UUID).", type=str),
            query_param("page", "Page number, starting at 1.", type=int),
            query_param("page_size", "Rows per page (default 20, maximum 100).", type=int),
        ],
        responses={200: ok(TripListSerializer, ex("trip.list", "A trip in the list", item=0))},
    ),
    create=doc(
        id="createTrip",
        tag=TAG,
        summary="Book a delivery",
        description="""
Books a delivery and **immediately tries to assign the nearest available driver**.

What happens, in order:

1. The route is calculated and the fare is fixed (this is the price the trip keeps).
2. The trip is saved and - if you sent `items` - the item list with it.
3. The nearest driver who is online, verified, on a vehicle of the type you asked for, not already on a trip and within 8 km of the pickup is assigned.

The response is `201` with the full trip either way. **Look at `status`:**

- `assigned` - a driver has the trip (see `driver` and `vehicle`).
- `no_driver_available` - nobody qualified right now. The trip exists; call `POST /trips/{id}/assign` to try again, or cancel it.

**Cash on delivery:** send `payment_mode: "cod"` **and** the drop's `contact_phone` - the customer's delivery
OTP is texted to it. **Prepaid** trips are marked paid at once and the driver collects nothing.

**Items and invoice** are optional. Add `items` (and `verify_items: true`) when the driver must confirm each item at the
drop; add `invoice_url` so the driver can download the invoice and share it on WhatsApp. See *Items & invoices*.

If your request times out, list your trips before retrying - booking twice creates two deliveries.
""",
        auth=COMPANY,
        request=TripCreateSerializer,
        request_examples=[
            ex("trip.create.request", "COD with items + invoice", request=True),
            raw_ex(
                "Prepaid (the minimum)",
                {
                    "vehicle_type_id": "725d6bae-1bdc-4a7a-ad03-8ef8ae0c6f13",
                    "payment_mode": "prepaid",
                    "pickup": {"address": "MG Road Metro Station, Bengaluru", "lat": "12.975000", "lng": "77.605000"},
                    "drop": {"address": "12 Indiranagar 100ft Road, Bengaluru", "lat": "12.978300", "lng": "77.640800"},
                },
                request=True,
            ),
        ],
        responses={201: ok(TripSerializer, ex("trip.create", "Booked and assigned to a driver"))},
        errors=["VEHICLE_TYPE_NOT_FOUND", "ROUTING_UNAVAILABLE", "FARE_NOT_CONFIGURED"],
    ),
    retrieve=doc(
        id="getTrip",
        tag=TAG,
        summary="Get a trip",
        description="""
The full trip: route (as an encoded polyline), fare breakdown, payment state, driver and vehicle, item list with the
driver's answers, and a timestamp for every stage it has been through.

Poll this to follow a delivery - see *Trip lifecycle* for what each `status` means and which timestamps fill in when.
""",
        auth=COMPANY,
        params=[TRIP_ID],
        by_id=True,
        responses={200: ok(TripSerializer, ex("trip.detail_assigned", "A trip with a driver assigned"))},
    ),
    assign=doc(
        id="assignTrip",
        tag=TAG,
        summary="Try to assign a driver again",
        description="""
Retries driver matching for a trip that is in `no_driver_available` - typically after a few minutes, when a driver may
have come online near the pickup. Uses the same rules as booking.

The response is the trip: `assigned` if someone was found, or still `no_driver_available`. It is **not an error**
for nobody to be found. Only a trip in `no_driver_available` can be retried.
""",
        auth=COMPANY,
        params=[TRIP_ID],
        by_id=True,
        request=None,
        responses={200: ok(TripSerializer, ex("trip.detail_assigned", "A driver was found"))},
        errors=["TRIP_NOT_RETRYABLE"],
    ),
    cancel=doc(
        id="cancelTrip",
        tag=TAG,
        summary="Cancel a trip",
        description="""
Cancels a trip, recording your `reason`. The assigned driver (if any) sees it disappear from their app.

A trip can be cancelled **until the driver has started the delivery** - in the statuses `requested`,
`no_driver_available`, `assigned` and `arrived_at_pickup`. Once it is `in_progress`, `completed` or already
`cancelled`, the call is refused with `TRIP_NOT_CANCELLABLE`.
""",
        auth=COMPANY,
        params=[TRIP_ID],
        by_id=True,
        request=TripCancelSerializer,
        request_examples=[raw_ex("Customer changed their mind", {"reason": "Customer cancelled the order"}, request=True)],
        responses={
            200: ok(
                TripSerializer,
                derived_ex(
                    "trip.detail_assigned",
                    "Cancelled by the company",
                    {
                        "status": "cancelled",
                        "cancelled_by": "company",
                        "cancellation_reason": "Customer cancelled the order",
                        "cancelled_at": "2026-09-21T09:52:11.204118Z",
                    },
                ),
            )
        },
        errors=["TRIP_NOT_CANCELLABLE"],
    ),
)
