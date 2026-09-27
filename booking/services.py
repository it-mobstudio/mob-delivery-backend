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
PLUS_CODE_RE = re.compile(r"^[23456789CFGHJMPQRVWX]{4,8}\+[23456789CFGHJMPQRVWX]{2,3}\b")
GOOGLE_DOWN_KEY = "places:google_down"
AVG_CITY_KMPH = 22  # for "3 min away" — a city average, not a promise
CUSTOMER_CANCELLABLE = [TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE, TripStatus.ASSIGNED,
                        TripStatus.ARRIVED_AT_PICKUP]


GOODS_TYPES = ["Construction material", "Furniture & home", "Electronics", "Groceries & food", "Documents & parcels",
               "Hardware & tools", "Textiles & garments", "Other"]


def vehicle_art(category, name=""):
    """The picture for a vehicle type (booking/static/booking/vehicles/), by
    what its name says it is, else by its category."""
    n = (name or "").lower()
    if re.search(r"lorry|trailer|container|\b(14|17|19|20|22|24|32)\s*ft", n):
        art = "lorry"
    elif re.search(r"mini|\bace\b|tempo|bolero|dost|\bvan\b|chhota", n):
        art = "pickup"
    elif re.search(r"truck|tonne|\bton\b|eicher|407|canter|\bft\b", n):
        art = "truck"  # bigger than a mini truck: "Pickup 8 ft", "Truck 1 tonne"
    elif re.search(r"pickup|pick-up|4 ?wheel", n):
        art = "pickup"
    elif re.search(r"auto|rickshaw|3 ?wheel|loader|e-?rick", n):
        art = "auto"
    elif re.search(r"bike|scoot|2 ?wheel|motor|activa", n):
        art = "scooter"
    else:
        art = {"two_wheeler": "scooter", "three_wheeler": "auto"}.get(category, "pickup")
    return f"booking/vehicles/{art}.png"


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
    # Google (Places Autocomplete + Details, Geocoding) when GOOGLE_MAPS_API_KEY
    # is set, OpenStreetMap's Photon otherwise — or whenever Google fails, so a
    # key problem degrades search instead of breaking booking. Always called
    # from here, never the browser, so the key stays on the server.

    @staticmethod
    def _cached(key, ttl, fetch):
        key = "places:" + hashlib.sha1(key.encode()).hexdigest()
        hit = cache.get(key)
        if hit is not None:
            return hit
        value = fetch()
        if value is not None:
            cache.set(key, value, ttl)
        return value

    @staticmethod
    def _use_google():
        return bool(settings.GOOGLE_MAPS_API_KEY) and not cache.get(GOOGLE_DOWN_KEY)

    @staticmethod
    def _google(api, params):
        """One Google Maps web-service call; None when it fails or the key is
        refused — and then Google is skipped for a couple of minutes, so
        every keystroke doesn't pay for a failing round trip."""
        try:
            response = requests.get(f"https://maps.googleapis.com/maps/api/{api}/json",
                                    params={**params, "key": settings.GOOGLE_MAPS_API_KEY}, timeout=6)
            data = response.json()
        except Exception:
            logger.warning("Google %s failed", api, exc_info=True)
            cache.set(GOOGLE_DOWN_KEY, 1, 120)
            return None
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            logger.warning("Google %s: %s %s", api, data.get("status"), data.get("error_message", ""))
            if data.get("status") in ("REQUEST_DENIED", "OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT", "UNKNOWN_ERROR"):
                cache.set(GOOGLE_DOWN_KEY, 1, 120)
            return None
        return data

    @classmethod
    def _photon(cls, path, params):
        def fetch():
            try:
                response = requests.get(f"https://photon.komoot.io/{path}", params=params, timeout=6,
                                        headers={"User-Agent": "mob-delivery/1.0 (booking)"})
                response.raise_for_status()
                features = response.json().get("features", [])
            except Exception:
                logger.warning("Place lookup failed", exc_info=True)
                return None
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
            return places

        return cls._cached(f"photon{path}{sorted(params.items())}", 60 * 60 * 24, fetch) or []

    @classmethod
    def search_places(cls, q, lat=None, lng=None, session=""):
        """Suggestions as the customer types. Google ones carry a `place_id`
        (coordinates come from place_details once one is picked); OSM ones
        carry lat/lng straight away."""
        if cls._use_google():
            params = {"input": q, "components": "country:in", "language": "en"}
            if lat is not None and lng is not None:
                params.update(origin=f"{lat:.4f},{lng:.4f}", locationbias=f"circle:40000@{lat:.3f},{lng:.3f}")

            def fetch():
                data = cls._google("place/autocomplete", {**params, "sessiontoken": session} if session else params)
                if data is None:
                    return None
                out = []
                for p in data.get("predictions", []):
                    f = p.get("structured_formatting", {})
                    out.append({
                        "place_id": p["place_id"], "title": f.get("main_text") or p.get("description", ""),
                        "subtitle": (f.get("secondary_text") or "").removesuffix(", India"),
                        "match": [[m["offset"], m["length"]] for m in f.get("main_text_matched_substrings", [])],
                        "distance_m": p.get("distance_meters"), "types": p.get("types", [])[:3],
                    })
                return out

            key = f"gac{q.lower()}{params.get('origin', '')}"
            places = cls._cached(key, 60 * 10, fetch)
            if places is not None:
                return places
        params = {"q": q, "limit": 8, "lang": "en"}
        if lat is not None and lng is not None:
            params.update(lat=round(lat, 3), lon=round(lng, 3))
        return cls._photon("api/", params)

    @classmethod
    def place_details(cls, place_id, session=""):
        """Coordinates and full address for a Google suggestion (ends the
        autocomplete session, which is how Google bills it)."""
        if not settings.GOOGLE_MAPS_API_KEY or not re.fullmatch(r"[A-Za-z0-9_-]{10,300}", place_id or ""):
            raise DomainError("PLACE_NOT_FOUND", "That place couldn't be found. Search again.", status_code=404)

        def fetch():
            params = {"place_id": place_id, "fields": "name,formatted_address,geometry/location", "language": "en"}
            data = cls._google("place/details", {**params, "sessiontoken": session} if session else params)
            if data is None or not data.get("result"):
                return None
            r = data["result"]
            address = (r.get("formatted_address") or "").removesuffix(", India")
            name = r.get("name") or address.split(",")[0]
            subtitle = address[len(name) + 2:] if address.startswith(name + ", ") else address
            full = address if address.startswith(name) else f"{name}, {address}"
            loc = r["geometry"]["location"]
            return {"place_id": place_id, "title": name, "subtitle": subtitle, "lat": loc["lat"], "lng": loc["lng"],
                    "address": full[:255]}

        place = cls._cached(f"gpd{place_id}", 60 * 60 * 24 * 7, fetch)
        if place is None:
            raise DomainError("PLACE_NOT_FOUND", "That place couldn't be found. Search again.", status_code=404)
        return place

    @classmethod
    def reverse(cls, lat, lng):
        place = cls._google_reverse(lat, lng) if cls._use_google() else None
        if place is None:
            places = cls._photon("reverse", {"lat": round(lat, 5), "lon": round(lng, 5), "limit": 1})
            place = places[0] if places else None
        if place is None:
            return {"title": "Pinned location", "subtitle": f"{lat:.5f}, {lng:.5f}", "lat": lat, "lng": lng,
                    "address": f"Pinned location ({lat:.5f}, {lng:.5f})"}
        # The pin is where the customer put it, not where the address's centre is.
        return {**place, "lat": lat, "lng": lng}

    @classmethod
    def _google_reverse(cls, lat, lng):
        def fetch():
            data = cls._google("geocode", {"latlng": f"{lat:.5f},{lng:.5f}", "language": "en"})
            if data is None:
                return None
            results = [r for r in data.get("results", []) if "plus_code" not in r.get("types", [])]
            if not results:
                return None
            parts = [x.strip() for x in results[0]["formatted_address"].removesuffix(", India").split(",")]
            parts = [x for x in parts if not PLUS_CODE_RE.match(x)] or parts
            head = 2 if len(parts) > 2 and (parts[0][:1].isdigit() or len(parts[0]) < 6) else 1
            title, subtitle = ", ".join(parts[:head]), ", ".join(parts[head:])
            return {"title": title, "subtitle": subtitle, "address": ", ".join(parts)[:255]}

        return cls._cached(f"grev{lat:.5f},{lng:.5f}", 60 * 60 * 24, fetch)

    # -- the customer's own places ----------------------------------------------------

    @staticmethod
    def recent_places(customer, limit=6):
        """Where this customer has sent things from and to, newest first, one
        entry per address — the "Recent" list under the search box."""
        seen, out = set(), []
        rows = customer.trips.order_by("-created_at").values_list(
            "drop_address", "drop_lat", "drop_lng", "pickup_address", "pickup_lat", "pickup_lng")[:40]
        for d_addr, d_lat, d_lng, p_addr, p_lat, p_lng in rows:
            for addr, lat, lng in ((d_addr, d_lat, d_lng), (p_addr, p_lat, p_lng)):
                key = (addr or "").strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                title, _, rest = addr.partition(", ")
                out.append({"title": title, "subtitle": rest, "address": addr, "lat": float(lat), "lng": float(lng)})
                if len(out) >= limit:
                    return out
        return out

    @staticmethod
    def save_place(customer, data, place=None):
        from .models import SavedPlace

        kind = data.get("kind") if data.get("kind") in SavedPlace.Kind.values else SavedPlace.Kind.OTHER
        try:
            lat, lng = float(data["lat"]), float(data["lng"])
        except (KeyError, TypeError, ValueError):
            raise DomainError("INVALID_PLACE", "Pick the place on the map first.", status_code=400)
        address = str(data.get("address") or "").strip()[:255]
        if not address:
            raise DomainError("INVALID_PLACE", "Pick the place on the map first.", status_code=400)
        phone = str(data.get("contact_phone") or "").strip()
        if phone:
            phone = normalise_phone(phone)
        if place is None and kind in (SavedPlace.Kind.HOME, SavedPlace.Kind.WORK):
            # One Home and one Work: saving another replaces it.
            place = customer.saved_places.filter(kind=kind).first()
        if place is None:
            if customer.saved_places.count() >= 20:
                raise DomainError("TOO_MANY_PLACES", "You can save up to 20 places. Remove one first.", status_code=400)
            place = SavedPlace(company=customer.company, customer=customer)
        place.kind = kind
        place.label = str(data.get("label") or "").strip()[:40] if kind == SavedPlace.Kind.OTHER else ""
        place.address, place.lat, place.lng = address, Decimal(str(round(lat, 6))), Decimal(str(round(lng, 6)))
        place.details = str(data.get("details") or "").strip()[:120]
        place.contact_name = str(data.get("contact_name") or "").strip()[:150]
        place.contact_phone = phone
        place.save()
        return place

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
    def catalogue(cls):
        """The vehicles on offer, for the home screen — no route needed."""
        from django.templatetags.static import static

        company = cls.company()
        if company is None:
            return []
        return [{"vehicle_type_id": str(vt.id), "name": vt.name, "category": vt.category,
                 "capacity_kg": float(vt.default_capacity_kg), "from_fare": float(max(vt.min_fare, vt.base_fare)),
                 "per_km": float(vt.per_km_rate), "image": vt.icon_image_url or static(vehicle_art(vt.category, vt.name))}
                for vt in VehicleType.objects.filter(company=company, status=VehicleTypeStatus.ACTIVE).order_by("default_capacity_kg")]

    @classmethod
    def options(cls, pickup, drop):
        """Every bookable vehicle type with its fare for this trip and how far
        the nearest free driver is — the "choose a vehicle" list — plus the
        road route to draw: {"options": [...], "route": [[lat, lng], ...]}."""
        from django.templatetags.static import static

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
                "image": vt.icon_image_url or static(vehicle_art(vt.category, vt.name)),
                "fare": float(fare["total_fare"]), "distance_m": route["distance_meters"],
                "duration_s": route["duration_seconds"],
                "eta_min": None if nearest is None or nearest > settings.DRIVER_MATCH_RADIUS_KM else max(2, round(nearest / AVG_CITY_KMPH * 60) + 1),
            })
        drawn = routes.get("auto") or next(iter(routes.values()), None)
        return {"options": options, "route": decode_route(drawn["polyline"], drawn.get("polyline_precision")) if drawn else []}

    # -- booking -------------------------------------------------------------------

    @classmethod
    def book(cls, customer, *, vehicle_type_id, pickup, drop, receiver_name="", receiver_phone="", notes="", goods=""):
        company = cls.company()
        vt = VehicleType.objects.filter(company=company, pk=vehicle_type_id, status=VehicleTypeStatus.ACTIVE).first()
        if vt is None:
            raise DomainError("VEHICLE_TYPE_NOT_FOUND", "That vehicle isn't available. Pick another.", status_code=400)
        receiver_phone = normalise_phone(receiver_phone) if receiver_phone else customer.phone_number
        pickup, drop = with_details(pickup), with_details(drop)
        if goods:
            notes = f"Goods: {goods[:40]}" + (f" · {notes}" if notes else "")
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


def with_details(point):
    """"Flat 402, 4th floor" typed by the customer goes in front of the
    searched address, where the driver reads it first."""
    details = (point.get("details") or "").strip()
    if details and not point["address"].lower().startswith(details.lower()):
        return {**point, "address": f"{details}, {point['address']}"[:255]}
    return point


def decode_route(polyline, precision=None):
    from core.polyline import decode

    try:
        return [[round(a, 5), round(b, 5)] for a, b in decode(polyline, precision or 6)]
    except Exception:
        return []


assert set(CUSTOMER_CANCELLABLE) <= set(CANCELLABLE_STATUSES)
