"""Shared Google Maps helpers — no models, just HTTP + cache. Used by trips
(trip-detail route, order-intake pincode fallback) and available to any
other module that needs a driving route or a pincode's coordinates. Both
functions are no-ops (return nulls) when GOOGLE_MAPS_API_KEY is unset —
same "disabled, not broken" pattern as Firebase push / Azure storage — so
this module is safe to import even before Maps is configured.
"""

from decimal import Decimal

import requests
from django.conf import settings
from django.core.cache import cache

ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
DIRECTIONS_API_URL = "https://maps.googleapis.com/maps/api/directions/json"
GEOCODING_API_URL = "https://maps.googleapis.com/maps/api/geocode/json"

REQUEST_TIMEOUT_SECONDS = 10
ROUTE_SUCCESS_CACHE_SECONDS = 21600  # 6 hours
ROUTE_FAILURE_CACHE_SECONDS = 300  # 5 minutes — a transient API issue shouldn't get cached for hours

EMPTY_ROUTE = {"encoded_polyline": None, "distance_text": None, "duration_text": None}


def _format_duration(duration):
    # Routes API returns durations as e.g. "1234s".
    if not duration:
        return None
    try:
        seconds = int(str(duration).rstrip("s"))
    except ValueError:
        return None
    return f"{round(seconds / 60)} min"


def _fetch_route_from_routes_api(origin_lat, origin_lng, dest_lat, dest_lng):
    body = {
        "origin": {"location": {"latLng": {"latitude": float(origin_lat), "longitude": float(origin_lng)}}},
        "destination": {"location": {"latLng": {"latitude": float(dest_lat), "longitude": float(dest_lng)}}},
        "travelMode": "DRIVE",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.polyline.encodedPolyline,routes.distanceMeters,routes.duration",
    }
    response = requests.post(ROUTES_API_URL, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    routes = response.json().get("routes") or []
    if not routes:
        return None
    route = routes[0]
    polyline = (route.get("polyline") or {}).get("encodedPolyline")
    if not polyline:
        return None
    distance_m = route.get("distanceMeters")
    return {
        "encoded_polyline": polyline,
        "distance_text": f"{distance_m / 1000:.1f} km" if distance_m is not None else None,
        "duration_text": _format_duration(route.get("duration")),
    }


def _fetch_route_from_directions_api(origin_lat, origin_lng, dest_lat, dest_lng):
    params = {
        "origin": f"{origin_lat},{origin_lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "key": settings.GOOGLE_MAPS_API_KEY,
    }
    response = requests.get(DIRECTIONS_API_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "OK" or not data.get("routes"):
        return None
    route = data["routes"][0]
    leg = (route.get("legs") or [{}])[0]
    return {
        "encoded_polyline": (route.get("overview_polyline") or {}).get("points"),
        "distance_text": (leg.get("distance") or {}).get("text"),
        "duration_text": (leg.get("duration") or {}).get("text"),
    }


def fetch_route_polyline(origin_lat, origin_lng, dest_lat, dest_lng):
    """Returns {encoded_polyline, distance_text, duration_text}, cached.

    Tries the newer Routes API first if GOOGLE_ROUTES_API_ENABLED (not every
    Google Cloud project has it turned on yet), then falls back to the
    older, more universally-enabled Directions API. Every field is None if
    no API key is configured or both tiers fail — callers should treat that
    as "route unavailable", not an error.
    """
    if not settings.GOOGLE_MAPS_API_KEY:
        return dict(EMPTY_ROUTE)

    cache_key = f"route:{origin_lat}:{origin_lng}:{dest_lat}:{dest_lng}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = dict(EMPTY_ROUTE)
    try:
        if settings.GOOGLE_ROUTES_API_ENABLED:
            fetched = _fetch_route_from_routes_api(origin_lat, origin_lng, dest_lat, dest_lng)
            if fetched:
                result = fetched
        if not result["encoded_polyline"]:
            fetched = _fetch_route_from_directions_api(origin_lat, origin_lng, dest_lat, dest_lng)
            if fetched:
                result = fetched
    except requests.RequestException:
        pass  # transient failure — cached briefly below, retried on next call

    cache.set(
        cache_key, result, ROUTE_SUCCESS_CACHE_SECONDS if result["encoded_polyline"] else ROUTE_FAILURE_CACHE_SECONDS
    )
    return result


def resolve_pincode_coordinates(pincode):
    """Resolves a pincode to (latitude, longitude) via Google's Geocoding
    API — we don't maintain a pincode dataset of our own. Cached
    indefinitely (pincodes don't move). Returns (None, None) if no API key
    is configured, the pincode is blank, or the lookup fails/finds nothing.
    """
    if not pincode or not settings.GOOGLE_MAPS_API_KEY:
        return None, None

    cache_key = f"geocode:{pincode}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        response = requests.get(
            GEOCODING_API_URL,
            params={"address": pincode, "key": settings.GOOGLE_MAPS_API_KEY},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "OK" or not data.get("results"):
            return None, None
        location = data["results"][0]["geometry"]["location"]
        lat, lng = location.get("lat"), location.get("lng")
        if lat is None or lng is None:
            return None, None
    except requests.RequestException:
        return None, None

    result = (Decimal(str(round(lat, 6))), Decimal(str(round(lng, 6))))
    cache.set(cache_key, result, timeout=None)
    return result
