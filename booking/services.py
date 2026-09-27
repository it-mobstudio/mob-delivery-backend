"""Customer booking: phone sign-in, places, fares, and booking a vehicle —
all built on the same trip services the company API uses, so a customer's
order is dispatched, tracked and paid exactly like any other."""

import hashlib
import logging
import random
import re
from decimal import Decimal

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.choices import CancelledBy, PaymentMode, PickupPhotoMode, TripStatus, VehicleTypeStatus
from core.constants import OTP_LENGTH
from core.exceptions import DomainError
from core.geo import haversine_distance_km
from drivers.models import Driver, Vehicle, VehicleType
from drivers.sms import get_sms_provider
from trips.models import ACTIVE_TRIP_STATUSES, Trip
from trips.routing import RoutingService
from trips.pricing import PricingService
from trips.services import CANCELLABLE_STATUSES, TripService

from .models import Customer

logger = logging.getLogger(__name__)

OTP_TTL = 300
OTP_THROTTLE = 30
OTP_MAX_ATTEMPTS = 5
PHONE_RE = re.compile(r"^\+91[6-9]\d{9}$")
AVG_CITY_KMPH = 22  # for "3 min away" — a city average, not a promise
CUSTOMER_CANCELLABLE = [TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE, TripStatus.ASSIGNED,
                        TripStatus.ARRIVED_AT_PICKUP]


def normalise_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "91" + digits
    phone = f"+{digits}"
    if not PHONE_RE.match(phone):
        raise DomainError("INVALID_PHONE", "Enter a valid 10-digit Indian mobile number.", status_code=400)
    return phone


class BookingService:
    @staticmethod
    def company():
        """The company whose fleet serves customer bookings."""
        from accounts.models import Company
        from drivers.services import DriverService

        configured = (getattr(settings, "BOOKING_COMPANY_ID", "") or "").strip()
        if configured:
            return Company.objects.filter(pk=configured, status="active").first()
        company = DriverService.signup_company()
        if company is None and settings.DEBUG:
            # Local development: the one company that actually has a fleet.
            with_fleet = list(Company.objects.filter(status="active", pk__in=VehicleType.objects.filter(
                status=VehicleTypeStatus.ACTIVE).values("company_id"))[:2])
            company = with_fleet[0] if len(with_fleet) == 1 else None
        return company

    # -- sign-in ------------------------------------------------------------------

    @staticmethod
    def request_otp(phone):
        phone = normalise_phone(phone)
        if cache.get(f"cust_otp_throttle:{phone}"):
            raise DomainError("OTP_ALREADY_REQUESTED", "A code was sent a moment ago. Please wait before asking again.", status_code=429)
        otp = f"{random.randint(0, 10 ** OTP_LENGTH - 1):0{OTP_LENGTH}d}"
        cache.set(f"cust_otp:{phone}", {"otp": otp, "tries": 0}, OTP_TTL)
        cache.set(f"cust_otp_throttle:{phone}", 1, OTP_THROTTLE)
        get_sms_provider().send_otp(phone, otp)
        return phone, otp

    @classmethod
    def verify_otp(cls, phone, otp):
        phone = normalise_phone(phone)
        key = f"cust_otp:{phone}"
        stored = cache.get(key)
        if not stored:
            raise DomainError("INVALID_OTP", "That code has expired. Ask for a new one.", status_code=400)
        if stored["tries"] >= OTP_MAX_ATTEMPTS:
            cache.delete(key)
            raise DomainError("INVALID_OTP", "Too many wrong codes. Ask for a new one.", status_code=400)
        if (otp or "").strip() != stored["otp"]:
            stored["tries"] += 1
            cache.set(key, stored, OTP_TTL)
            raise DomainError("INVALID_OTP", "That code isn't right. Check the SMS and try again.", status_code=400)
        cache.delete(key)
        company = cls.company()
        if company is None:
            raise DomainError("BOOKING_CLOSED", "Bookings aren't open yet.", status_code=503)
        customer, _ = Customer.objects.get_or_create(company=company, phone_number=phone)
        customer.last_login_at = timezone.now()
        customer.save(update_fields=["last_login_at", "updated_at"])
        return customer

    # -- places -------------------------------------------------------------------

    @staticmethod
    def _photon(path, params):
        key = "places:" + hashlib.sha1(f"{path}{sorted(params.items())}".encode()).hexdigest()
        hit = cache.get(key)
        if hit is not None:
            return hit
        try:
            response = requests.get(f"https://photon.komoot.io/{path}", params=params, timeout=6,
                                    headers={"User-Agent": "mob-delivery/1.0 (booking)"})
            response.raise_for_status()
            features = response.json().get("features", [])
        except Exception:
            logger.warning("Place lookup failed", exc_info=True)
            return []
        places = []
        for f in features:
            p, (lng, lat) = f.get("properties", {}), f["geometry"]["coordinates"]
            if p.get("countrycode", "IN").upper() != "IN":
                continue
            title = p.get("name") or p.get("street") or p.get("district") or p.get("city") or ""
            parts = [p.get("street") if p.get("name") else None, p.get("district"), p.get("city"), p.get("state"), p.get("postcode")]
            subtitle = ", ".join(dict.fromkeys(x for x in parts if x and x != title))
            places.append({"title": title, "subtitle": subtitle, "lat": lat, "lng": lng,
                           "address": ", ".join(x for x in [title, subtitle] if x)[:255]})
        cache.set(key, places, 60 * 60 * 24)
        return places

    @classmethod
    def search_places(cls, q, lat=None, lng=None):
        params = {"q": q, "limit": 8, "lang": "en"}
        if lat is not None and lng is not None:
            params.update(lat=round(lat, 3), lon=round(lng, 3))
        return cls._photon("api/", params)

    @classmethod
    def reverse(cls, lat, lng):
        places = cls._photon("reverse", {"lat": round(lat, 5), "lon": round(lng, 5), "limit": 1})
        if places:
            return places[0]
        return {"title": "Pinned location", "subtitle": f"{lat:.5f}, {lng:.5f}", "lat": lat, "lng": lng,
                "address": f"Pinned location ({lat:.5f}, {lng:.5f})"}

    # -- drivers & fares ----------------------------------------------------------

    @staticmethod
    def _online_drivers(company):
        busy = Trip.objects.filter(company=company, status__in=ACTIVE_TRIP_STATUSES, driver_id__isnull=False).values("driver_id")
        return (Driver.objects.select_related("kyc")
                .filter(company=company, is_online=True, last_known_lat__isnull=False, current_vehicle_id__isnull=False)
                .exclude(id__in=busy))

    @classmethod
    def nearby(cls, lat, lng, radius_km=6):
        """Free drivers near a point, for the map — no names, and each dot
        nudged a little so nobody's exact position is given away."""
        company = cls.company()
        if company is None:
            return []
        vehicles = dict(Vehicle.objects.filter(company=company).values_list("id", "vehicle_type__category"))
        out = []
        for d in cls._online_drivers(company)[:500]:
            dist = haversine_distance_km(lat, lng, d.last_known_lat, d.last_known_lng)
            if dist <= radius_km and d.is_eligible_for_assignment:
                seed = int(hashlib.md5(str(d.pk).encode()).hexdigest()[:6], 16)
                out.append({"lat": float(d.last_known_lat) + ((seed % 100) - 50) / 60000,
                            "lng": float(d.last_known_lng) + (((seed // 100) % 100) - 50) / 60000,
                            "category": vehicles.get(d.current_vehicle_id)})
        return out

    @classmethod
    def options(cls, pickup, drop):
        """Every bookable vehicle type with its fare for this trip and how far
        the nearest free driver is — the "choose a ride" list."""
        company = cls.company()
        if company is None:
            raise DomainError("BOOKING_CLOSED", "Bookings aren't open yet.", status_code=503)
        types = list(VehicleType.objects.filter(company=company, status=VehicleTypeStatus.ACTIVE).order_by("default_capacity_kg"))
        routes, options = {}, []
        drivers = [d for d in cls._online_drivers(company) if d.is_eligible_for_assignment]
        vehicle_type_of = dict(Vehicle.objects.filter(company=company).values_list("id", "vehicle_type_id"))
        for vt in types:
            costing = RoutingService.costing_for_category(vt.category)
            if costing not in routes:  # one route per kind of road user, not per type
                routes[costing] = RoutingService.get_route(pickup["lat"], pickup["lng"], drop["lat"], drop["lng"], costing=costing)
            route = routes[costing]
            fare = PricingService.calculate_fare(vt, route["distance_meters"], route["duration_seconds"], pickup["lat"], pickup["lng"])
            near = [haversine_distance_km(pickup["lat"], pickup["lng"], d.last_known_lat, d.last_known_lng)
                    for d in drivers if vehicle_type_of.get(d.current_vehicle_id) == vt.id]
            nearest = min(near) if near else None
            options.append({
                "vehicle_type_id": str(vt.id), "name": vt.name, "category": vt.category,
                "capacity_kg": float(vt.default_capacity_kg), "icon_url": vt.icon_image_url or "",
                "fare": float(fare["total_fare"]), "distance_m": route["distance_meters"],
                "duration_s": route["duration_seconds"],
                "eta_min": None if nearest is None or nearest > settings.DRIVER_MATCH_RADIUS_KM else max(2, round(nearest / AVG_CITY_KMPH * 60) + 1),
            })
        return options

    # -- booking -------------------------------------------------------------------

    @classmethod
    def book(cls, customer, *, vehicle_type_id, pickup, drop, receiver_name="", receiver_phone="", notes=""):
        company = cls.company()
        vt = VehicleType.objects.filter(company=company, pk=vehicle_type_id, status=VehicleTypeStatus.ACTIVE).first()
        if vt is None:
            raise DomainError("VEHICLE_TYPE_NOT_FOUND", "That vehicle isn't available. Pick another.", status_code=400)
        receiver_phone = normalise_phone(receiver_phone) if receiver_phone else customer.phone_number
        trip = TripService.create_trip(
            company=company, vehicle_type=vt,
            pickup={"address": pickup["address"][:255], "lat": Decimal(str(round(pickup["lat"], 6))),
                    "lng": Decimal(str(round(pickup["lng"], 6))), "contact_name": customer.full_name or "Customer",
                    "contact_phone": customer.phone_number},
            drop={"address": drop["address"][:255], "lat": Decimal(str(round(drop["lat"], 6))),
                  "lng": Decimal(str(round(drop["lng"], 6))),
                  "contact_name": (receiver_name or customer.full_name or "Receiver")[:150], "contact_phone": receiver_phone},
            payment_mode=PaymentMode.COD, notes=(notes or "")[:500],
            pickup_photo=PickupPhotoMode.ORDER, delivery_photo=PickupPhotoMode.ORDER,
        )
        trip.customer = customer
        trip.save(update_fields=["customer", "updated_at"])
        return trip

    @staticmethod
    def cancel(customer, trip, reason):
        if trip.customer_id != customer.pk:
            raise DomainError("NOT_FOUND", "Booking not found.", status_code=404)
        if trip.status not in CUSTOMER_CANCELLABLE:
            raise DomainError("TRIP_NOT_CANCELLABLE", "The driver has already picked up your goods — it can't be cancelled now.", status_code=409)
        return TripService.cancel_trip(trip, reason or "Cancelled by customer", CancelledBy.CUSTOMER)


assert set(CUSTOMER_CANCELLABLE) <= set(CANCELLABLE_STATUSES)
