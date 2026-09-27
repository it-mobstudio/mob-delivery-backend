import json
from functools import wraps

from django.conf import settings
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.choices import TripStatus
from core.exceptions import DomainError
from core.polyline import decode as decode_polyline
from trips.models import Trip

from .models import Customer
from .services import CUSTOMER_CANCELLABLE, BookingService

SESSION_KEY = "booking_customer"
ACTIVE = [TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE, TripStatus.ASSIGNED,
          TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS]


def current_customer(request):
    pk = request.session.get(SESSION_KEY)
    return Customer.objects.filter(pk=pk).first() if pk else None


def customer_required(api=False):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            customer = current_customer(request)
            if customer is None:
                if api:
                    return JsonResponse({"error": "Please sign in again."}, status=401)
                return redirect(f"{reverse('booking:login')}?next={request.get_full_path()}")
            request.customer = customer
            return view(request, *args, **kwargs)

        return wrapped

    return decorate


def _error(exc):
    return JsonResponse({"error": str(exc.detail), "code": exc.code}, status=exc.status_code)


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


# -- sign-in --------------------------------------------------------------------------------


def login(request):
    if current_customer(request):
        return redirect(request.GET.get("next") or "booking:home")
    return render(request, "booking/login.html", {"next": request.GET.get("next", "")})


@require_POST
def api_otp_request(request):
    try:
        phone, otp = BookingService.request_otp(_body(request).get("phone"))
    except DomainError as exc:
        return _error(exc)
    data = {"phone": phone, "message": f"We sent a code to {phone}."}
    if settings.DRIVER_OTP_DEBUG_RESPONSE:
        data["debug_otp"] = otp  # non-production servers only
    return JsonResponse(data)


@require_POST
def api_otp_verify(request):
    body = _body(request)
    try:
        customer = BookingService.verify_otp(body.get("phone"), body.get("otp"))
    except DomainError as exc:
        return _error(exc)
    request.session.cycle_key()
    request.session[SESSION_KEY] = str(customer.pk)
    return JsonResponse({"new": not customer.full_name, "name": customer.full_name})


@require_POST
def logout(request):
    request.session.pop(SESSION_KEY, None)
    return redirect("booking:login")


# -- pages ----------------------------------------------------------------------------------


@customer_required()
def home(request):
    active = request.customer.trips.filter(status__in=ACTIVE).order_by("-created_at").first()
    return render(request, "booking/home.html", {"customer": request.customer, "active": active, "tab": "book"})


@customer_required()
def trips(request):
    rows = request.customer.trips.select_related("vehicle_type", "driver").order_by("-created_at")
    tab = request.GET.get("tab", "all")
    if tab == "active":
        rows = rows.filter(status__in=ACTIVE)
    elif tab in ("completed", "cancelled"):
        rows = rows.filter(status=tab)
    spent = sum(t.total_fare or 0 for t in request.customer.trips.filter(status=TripStatus.COMPLETED))
    page = Paginator(rows, 15).get_page(request.GET.get("page"))
    return render(request, "booking/trips.html", {"customer": request.customer, "page_obj": page, "filter": tab,
                                                  "spent": spent, "tab": "trips"})


@customer_required()
def trip(request, pk):
    t = get_object_or_404(request.customer.trips.select_related("vehicle_type", "driver", "vehicle"), pk=pk)
    return render(request, "booking/trip.html", {"customer": request.customer, "t": t, "tab": "trips",
                                                 "state_json": json.dumps(_trip_state(t))})


@customer_required()
def profile(request):
    c = request.customer
    if request.method == "POST":
        c.full_name = (request.POST.get("full_name") or "").strip()[:150]
        c.email = (request.POST.get("email") or "").strip()[:254]
        c.save(update_fields=["full_name", "email", "updated_at"])
        return redirect(request.POST.get("next") or "booking:profile")
    stats = {"trips": c.trips.count(), "done": c.trips.filter(status=TripStatus.COMPLETED).count()}
    return render(request, "booking/profile.html", {"customer": c, "stats": stats, "tab": "profile"})


# -- JSON used by the map screens ----------------------------------------------------------


@require_GET
@customer_required(api=True)
def api_places(request):
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"places": []})
    lat, lng = _float(request.GET.get("lat")), _float(request.GET.get("lng"))
    return JsonResponse({"places": BookingService.search_places(q, lat, lng)})


@require_GET
@customer_required(api=True)
def api_reverse(request):
    lat, lng = _float(request.GET.get("lat")), _float(request.GET.get("lng"))
    if lat is None or lng is None:
        return JsonResponse({"error": "lat and lng are needed"}, status=400)
    return JsonResponse(BookingService.reverse(lat, lng))


@require_GET
@customer_required(api=True)
def api_nearby(request):
    lat, lng = _float(request.GET.get("lat")), _float(request.GET.get("lng"))
    if lat is None or lng is None:
        return JsonResponse({"drivers": []})
    return JsonResponse({"drivers": BookingService.nearby(lat, lng)})


@require_POST
@customer_required(api=True)
def api_options(request):
    body = _body(request)
    try:
        options = BookingService.options(_point(body.get("pickup")), _point(body.get("drop")))
    except DomainError as exc:
        return _error(exc)
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"error": "Choose where to pick up and where to deliver."}, status=400)
    return JsonResponse({"options": options})


@require_POST
@customer_required(api=True)
def api_book(request):
    body = _body(request)
    try:
        t = BookingService.book(
            request.customer, vehicle_type_id=body.get("vehicle_type_id"), pickup=_point(body.get("pickup")),
            drop=_point(body.get("drop")), receiver_name=(body.get("receiver_name") or "").strip(),
            receiver_phone=(body.get("receiver_phone") or "").strip(), notes=(body.get("notes") or "").strip(),
        )
    except DomainError as exc:
        return _error(exc)
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"error": "Choose where to pick up and where to deliver."}, status=400)
    return JsonResponse({"id": str(t.pk), "url": reverse("booking:trip", args=[t.pk])}, status=201)


@require_GET
@customer_required(api=True)
def api_trip(request, pk):
    t = get_object_or_404(request.customer.trips.select_related("vehicle_type", "driver", "vehicle"), pk=pk)
    return JsonResponse(_trip_state(t))


@require_POST
@customer_required(api=True)
def api_cancel(request, pk):
    t = get_object_or_404(request.customer.trips.all(), pk=pk)
    try:
        BookingService.cancel(request.customer, t, (_body(request).get("reason") or "").strip())
    except DomainError as exc:
        return _error(exc)
    t.refresh_from_db()
    return JsonResponse(_trip_state(t))


# -- helpers ------------------------------------------------------------------------------


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point(p):
    return {"lat": float(p["lat"]), "lng": float(p["lng"]), "address": str(p.get("address") or "")[:255]}


def _trip_state(t):
    """Everything the tracking screen shows, in one poll."""
    driver = t.driver
    route = []
    if t.route_polyline:
        try:
            route = [[round(a, 5), round(b, 5)] for a, b in decode_polyline(t.route_polyline, t.polyline_precision)]
        except Exception:
            route = []
    live = t.status in (TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS)
    return {
        "id": str(t.pk), "number": t.order_number, "status": t.status, "status_label": t.get_status_display(),
        "created": t.created_at.isoformat(),
        "pickup": {"lat": float(t.pickup_lat), "lng": float(t.pickup_lng), "address": t.pickup_address},
        "drop": {"lat": float(t.drop_lat), "lng": float(t.drop_lng), "address": t.drop_address,
                 "name": t.drop_contact_name, "phone": t.drop_contact_phone},
        "route": route, "distance_m": t.distance_meters, "duration_s": t.duration_seconds,
        "vehicle_type": t.vehicle_type.name, "category": t.vehicle_type.category,
        "fare": float(t.total_fare or 0), "paid": t.payment_status == "paid", "payment_mode": t.payment_mode,
        "breakdown": {"base": float(t.base_fare or 0), "distance": float(t.distance_fare or 0), "time": float(t.time_fare or 0)},
        "driver": None if not driver else {
            "name": driver.full_name, "phone": driver.phone_number if live else None,
            "photo": driver.profile_photo_url or None,
            "vehicle": t.vehicle.registration_number if t.vehicle else "",
            "lat": float(driver.last_known_lat) if live and driver.last_known_lat is not None else None,
            "lng": float(driver.last_known_lng) if live and driver.last_known_lng is not None else None,
        },
        "steps": [
            {"label": "Booked", "at": t.created_at.isoformat()},
            {"label": "Driver assigned", "at": t.assigned_at.isoformat() if t.assigned_at else None},
            {"label": "Driver at pickup", "at": t.arrived_at_pickup_at.isoformat() if t.arrived_at_pickup_at else None},
            {"label": "Picked up", "at": t.started_at.isoformat() if t.started_at else None},
            {"label": "Delivered", "at": t.completed_at.isoformat() if t.completed_at else None},
        ],
        "cancelled": {"by": t.cancelled_by, "reason": t.cancellation_reason, "at": t.cancelled_at.isoformat()} if t.cancelled_at else None,
        "can_cancel": t.status in CUSTOMER_CANCELLABLE,
        "photos": [u for u in (t.pickup_photo_url, t.delivery_photo_url) if u],
    }
