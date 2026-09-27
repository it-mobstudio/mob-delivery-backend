import json
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from django.core.exceptions import ValidationError

from core.choices import CancelledBy, PaymentMode, PaymentStatus, TripStatus, UploadPurpose
from core.constants import VOICE_NOTE_MAX_SECONDS
from core.uploads import UploadService
from core.exceptions import DomainError
from core.polyline import decode as decode_polyline
from drivers.models import Driver, VehicleType
from trips.matching import MatchingService
from trips.models import Trip
from trips.services import CANCELLABLE_STATUSES, TripService

from ..access import console_view, scoped
from .common import csv_response, day_start, page, paginate, parse_date
from .common import without as _without

TABS = [
    ("all", "All", None),
    ("unassigned", "Waiting for driver", [TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE]),
    ("active", "On the road", [TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS]),
    ("assigned", "Assigned", [TripStatus.ASSIGNED]),
    ("arrived_at_pickup", "At pickup", [TripStatus.ARRIVED_AT_PICKUP]),
    ("in_progress", "In transit", [TripStatus.IN_PROGRESS]),
    ("completed", "Delivered", [TripStatus.COMPLETED]),
    ("cancelled", "Cancelled", [TripStatus.CANCELLED]),
]
SORTS = {"new": "-created_at", "old": "created_at", "fare": "-total_fare", "fare_low": "total_fare"}
REASONS = ["Customer cancelled", "Customer not reachable", "Wrong address", "Goods not ready at pickup",
           "Duplicate order", "Vehicle breakdown", "Other"]


def _filtered(request, base):
    g = request.GET
    qs = base
    q = (g.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(order_number__icontains=q) | Q(reference_id__icontains=q) | Q(invoice_number__icontains=q)
            | Q(pickup_contact_name__icontains=q) | Q(pickup_contact_phone__icontains=q)
            | Q(drop_contact_name__icontains=q) | Q(drop_contact_phone__icontains=q)
            | Q(pickup_address__icontains=q) | Q(drop_address__icontains=q)
            | Q(driver__full_name__icontains=q) | Q(driver__phone_number__icontains=q)
            | Q(vehicle__registration_number__icontains=q)
        )
    start, end = parse_date(g.get("from")), parse_date(g.get("to"))
    if start:
        qs = qs.filter(created_at__gte=day_start(start))
    if end:
        qs = qs.filter(created_at__lt=day_start(end + timedelta(days=1)))
    if g.get("vehicle_type"):
        qs = qs.filter(vehicle_type_id=g["vehicle_type"])
    if g.get("payment") in PaymentMode.values:
        qs = qs.filter(payment_mode=g["payment"])
    if g.get("paid") in PaymentStatus.values:
        qs = qs.filter(payment_status=g["paid"])
    if g.get("driver"):
        qs = qs.filter(driver_id=g["driver"])
    if g.get("photos") == "has":
        qs = qs.filter(~Q(pickup_photo_url="") | ~Q(delivery_photo_url="") | ~Q(items__proof_image_url=""))
    if g.get("photos") == "missing":
        qs = qs.filter((~Q(pickup_photo="none") & Q(pickup_photo_url="") & Q(started_at__isnull=False))
                       | (~Q(delivery_photo="none") & Q(delivery_photo_url="") & Q(status=TripStatus.COMPLETED)))
    if g.get("issues"):
        qs = qs.filter(items__status="not_delivered")
    return qs.distinct()


@console_view()
def orders(request):
    base = scoped(request, Trip.objects.all())
    filtered = _filtered(request, base)
    counts = filtered.aggregate(**{
        key: Count("id", filter=Q(status__in=statuses)) if statuses else Count("id") for key, _, statuses in TABS
    })
    tab = request.GET.get("status") if request.GET.get("status") in dict((k, 1) for k, _, _ in TABS) else "all"
    statuses = dict((k, s) for k, _, s in TABS)[tab]
    rows = filtered.filter(status__in=statuses) if statuses else filtered
    rows = rows.select_related("driver", "vehicle_type", "vehicle", "company").annotate(
        n_items=Count("items", distinct=True)).order_by(SORTS.get(request.GET.get("sort"), "-created_at"))

    if request.GET.get("export") == "csv":
        return csv_response("orders", [
            "Order", "Status", "Booked", "Company", "Reference", "Customer", "Customer phone", "Drop", "Pickup contact",
            "Pickup", "Driver", "Driver phone", "Vehicle", "Vehicle type", "Distance km", "Fare", "Bonus", "Driver earning",
            "Payment", "Payment status", "Delivered", "Cancelled", "Cancellation reason",
        ], ([t.order_number, t.get_status_display(), t.created_at, t.company.name, t.reference_id, t.drop_contact_name,
             t.drop_contact_phone, t.drop_address, t.pickup_contact_name, t.pickup_address,
             t.driver.full_name if t.driver else "", t.driver.phone_number if t.driver else "",
             t.vehicle.registration_number if t.vehicle else "", t.vehicle_type.name,
             round(t.distance_meters / 1000, 2) if t.distance_meters else "", t.total_fare, t.bonus_fare, t.driver_earning,
             t.get_payment_mode_display(), t.payment_status, t.completed_at, t.cancelled_at, t.cancellation_reason]
            for t in rows.iterator()))

    active_filters = [(label, _without(request, key)) for key, label in [
        ("q", f"“{request.GET.get('q')}”"), ("from", f"From {request.GET.get('from')}"), ("to", f"To {request.GET.get('to')}"),
        ("vehicle_type", "Vehicle type"), ("payment", f"Payment: {request.GET.get('payment')}"), ("paid", f"{request.GET.get('paid')}"),
        ("driver", "One driver"), ("photos", f"Photos: {request.GET.get('photos')}"), ("issues", "Items not delivered"),
    ] if request.GET.get(key)]
    return page(request, "console/orders/list.html", "dispatch" if tab == "unassigned" else "orders",
                page_obj=paginate(request, rows), tabs=[(k, l, counts[k]) for k, l, _ in TABS], tab=tab,
                vehicle_types=scoped(request, VehicleType.objects.all()).order_by("name"),
                active_filters=active_filters, search_q=request.GET.get("q", ""),
                assignable_statuses=[TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE, TripStatus.ASSIGNED],
                cancellable_statuses=CANCELLABLE_STATUSES)


@console_view()
def order(request, pk):
    trip = get_object_or_404(scoped(request, Trip.objects.select_related("driver", "vehicle", "vehicle_type", "company")), pk=pk)
    items = list(trip.items.all())
    photos = [(f"Pickup · whole order", trip.pickup_photo_url, "pickup"),
              (f"Delivery · whole order", trip.delivery_photo_url, "delivery")]
    for item in items:
        photos += [(f"Pickup · {item.name}", item.pickup_photo_url, "pickup"),
                   (f"Delivery · {item.name}", item.delivery_photo_url, "delivery"),
                   (f"Checked · {item.name}", item.proof_image_url, "check")]
    photos = [{"caption": c, "url": u, "kind": k} for c, u, k in photos if u]

    route = []
    if trip.route_polyline:
        try:
            route = [[round(lat, 6), round(lng, 6)] for lat, lng in decode_polyline(trip.route_polyline, trip.polyline_precision)]
        except Exception:  # a malformed polyline shouldn't break the page
            route = []
    driver_pos = None
    if trip.driver and trip.driver.last_known_lat is not None and trip.status in (
            TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS):
        driver_pos = [float(trip.driver.last_known_lat), float(trip.driver.last_known_lng)]
    map_data = {"pickup": [float(trip.pickup_lat), float(trip.pickup_lng)], "drop": [float(trip.drop_lat), float(trip.drop_lng)],
                "route": route, "driver": driver_pos}

    steps = [
        ("Booked", trip.created_at, "file-plus"), ("Driver assigned", trip.assigned_at, "user-check"),
        ("Reached pickup", trip.arrived_at_pickup_at, "map-pin"), ("Picked up", trip.started_at, "package-check"),
    ]
    if trip.payment_mode == PaymentMode.COD:
        steps.append(("Payment collected", trip.cod_collected_at, "qr-code"))
    steps.append(("Delivered", trip.completed_at, "circle-check-big"))
    if trip.cancelled_at:
        steps.append(("Cancelled", trip.cancelled_at, "circle-x"))

    assignable = []
    if trip.status in (TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE, TripStatus.ASSIGNED):
        assignable = [(d, v, round(dist, 1) if dist is not None else None)
                      for d, v, dist in MatchingService.assignable_drivers(trip) if d.id != trip.driver_id][:25]

    return page(request, "console/orders/detail.html", "orders", trip=trip, items=items, photos=photos,
                map_json=json.dumps(map_data), steps=steps, assignable=assignable, reasons=REASONS,
                can_cancel=trip.status in CANCELLABLE_STATUSES,
                issues=sum(1 for i in items if i.status == "not_delivered"))


def _trip_for_action(request, pk):
    return get_object_or_404(scoped(request, Trip.objects.all()), pk=pk)


@console_view(edit=True)
@require_POST
def order_cancel(request, pk):
    trip = _trip_for_action(request, pk)
    reason = (request.POST.get("reason") or "").strip()
    if reason == "Other":
        reason = (request.POST.get("other") or "").strip()
    if not reason:
        messages.error(request, "Say why the order is being cancelled.")
        return redirect("console:order", pk=pk)
    try:
        TripService.cancel_trip(trip, reason, CancelledBy.COMPANY)
        messages.success(request, f"Order {trip.order_number} cancelled.")
    except DomainError as exc:
        messages.error(request, exc.detail)
    return redirect("console:order", pk=pk)


@console_view(edit=True)
@require_POST
def order_retry(request, pk):
    trip = _trip_for_action(request, pk)
    try:
        TripService.retry_assignment(trip)
        if trip.status == TripStatus.ASSIGNED:
            messages.success(request, f"Assigned to {trip.driver.full_name}.")
        else:
            messages.warning(request, "Still no driver available nearby. Assign one by hand, or try again shortly.")
    except DomainError as exc:
        messages.error(request, exc.detail)
    return redirect("console:order", pk=pk)


@console_view(edit=True)
@require_POST
def order_assign(request, pk):
    trip = _trip_for_action(request, pk)
    try:
        TripService.assign_to_driver(trip, request.POST.get("driver"))
        messages.success(request, f"Assigned to {trip.driver.full_name}.")
    except DomainError as exc:
        messages.error(request, exc.detail)
    return redirect("console:order", pk=pk)


@console_view(edit=True)
@require_POST
def order_voice(request, pk):
    """Save (or remove) the dispatcher's voice note for the driver — a WAV
    recorded in the browser, at most 30 s."""
    trip = _trip_for_action(request, pk)
    if request.POST.get("remove"):
        trip.voice_note_url, trip.voice_note_seconds = "", None
        trip.save(update_fields=["voice_note_url", "voice_note_seconds", "updated_at"])
        messages.success(request, "Voice note removed.")
        return redirect("console:order", pk=pk)
    audio = request.FILES.get("audio")
    try:
        seconds = max(1, min(VOICE_NOTE_MAX_SECONDS, round(float(request.POST.get("seconds") or 0))))
    except ValueError:
        seconds = None
    if not audio:
        messages.error(request, "Record something first.")
        return redirect("console:order", pk=pk)
    if audio.size > 3 * 1024 * 1024:
        messages.error(request, "That recording is too long — keep it under 30 seconds.")
        return redirect("console:order", pk=pk)
    try:
        trip.voice_note_url = UploadService.store(audio, UploadPurpose.TRIP_VOICE_NOTE, trip.company_id)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("console:order", pk=pk)
    trip.voice_note_seconds = seconds
    trip.save(update_fields=["voice_note_url", "voice_note_seconds", "updated_at"])
    messages.success(request, "Voice note saved — the driver can play it in the app.")
    return redirect("console:order", pk=pk)


@console_view(edit=True)
@require_POST
def order_notes(request, pk):
    trip = _trip_for_action(request, pk)
    trip.notes = (request.POST.get("notes") or "").strip()[:500]
    trip.save(update_fields=["notes", "updated_at"])
    messages.success(request, "Note saved.")
    return redirect("console:order", pk=pk)


def drivers_for_filter(request):
    return scoped(request, Driver.objects.all()).order_by("full_name").only("id", "full_name", "phone_number")
