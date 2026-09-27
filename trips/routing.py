import hashlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from core.constants import CATEGORY_COSTING, VALHALLA_POLYLINE_PRECISION
from core.exceptions import DomainError

logger = logging.getLogger(__name__)


GOOGLE_POLYLINE_PRECISION = 5
ROUTE_CACHE_SECONDS = 60 * 60 * 24


class RoutingService:
    """Distance/duration/polyline for a pickup/drop pair, from Google's
    Directions API (real roads anywhere in India) or the self-hosted Valhalla
    engine (docker-compose.yml's valhalla service) — see ROUTING_PROVIDER.
    """

    @staticmethod
    def _use_google():
        provider = (settings.ROUTING_PROVIDER or "auto").lower()
        if provider == "valhalla":
            return False
        return bool(getattr(settings, "GOOGLE_MAPS_API_KEY", ""))

    @classmethod
    def _google_route(cls, pickup_lat, pickup_lng, drop_lat, drop_lng):
        """One driving route from Google, or None if Google can't give one
        (the caller then asks Valhalla). Cached: the same pair asked again
        within a day — fares, then booking, then the driver's screen — costs
        one call."""
        o = f"{float(pickup_lat):.5f},{float(pickup_lng):.5f}"
        d = f"{float(drop_lat):.5f},{float(drop_lng):.5f}"
        key = "route:g:" + hashlib.sha1(f"{o}>{d}".encode()).hexdigest()
        hit = cache.get(key)
        if hit is not None:
            return hit
        try:
            response = requests.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={"origin": o, "destination": d, "mode": "driving", "region": "in",
                        "key": settings.GOOGLE_MAPS_API_KEY},
                timeout=settings.VALHALLA_TIMEOUT_SECONDS,
            )
            data = response.json()
            if data.get("status") != "OK":
                logger.warning("Google directions: %s %s", data.get("status"), data.get("error_message", ""))
                return None
            route = data["routes"][0]
            legs = route["legs"]
            result = {
                # The detailed line (every step's), not the simplified overview —
                # the one drawn on the map should follow the roads closely.
                "polyline": cls._join_steps(legs) or route["overview_polyline"]["points"],
                "polyline_precision": GOOGLE_POLYLINE_PRECISION,
                "distance_meters": sum(leg["distance"]["value"] for leg in legs),
                "duration_seconds": sum(leg["duration"]["value"] for leg in legs),
            }
        except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
            logger.warning("Google directions failed", exc_info=True)
            return None
        cache.set(key, result, ROUTE_CACHE_SECONDS)
        return result

    @staticmethod
    def _join_steps(legs):
        from core.polyline import decode, encode

        points = []
        for leg in legs:
            for step in leg.get("steps", []):
                part = decode(step["polyline"]["points"], GOOGLE_POLYLINE_PRECISION)
                points.extend(part[1:] if points and part and points[-1] == part[0] else part)
        return encode(points, GOOGLE_POLYLINE_PRECISION) if len(points) > 1 else ""

    @staticmethod
    def costing_for_category(category):
        return CATEGORY_COSTING.get(category, "auto")

    @classmethod
    def get_route_via(cls, points, costing="auto"):
        """One route through several stops, in order ([(lat, lng), ...]):
        total distance/time, and every leg joined into one polyline."""
        if len(points) == 2:
            (a_lat, a_lng), (b_lat, b_lng) = points
            return cls.get_route(a_lat, a_lng, b_lat, b_lng, costing=costing)
        if cls._use_google():
            legs = [cls._google_route(*a, *b) for a, b in zip(points, points[1:])]
            if all(legs):
                from core.polyline import decode, encode

                joined = []
                for leg in legs:
                    pts = decode(leg["polyline"], GOOGLE_POLYLINE_PRECISION)
                    joined.extend(pts[1:] if joined else pts)
                return {
                    "polyline": encode(joined, GOOGLE_POLYLINE_PRECISION),
                    "polyline_precision": GOOGLE_POLYLINE_PRECISION,
                    "distance_meters": sum(leg["distance_meters"] for leg in legs),
                    "duration_seconds": sum(leg["duration_seconds"] for leg in legs),
                }
        body = {"locations": [{"lat": float(lat), "lon": float(lng)} for lat, lng in points],
                "costing": costing, "units": "kilometers"}
        try:
            response = requests.post(f"{settings.VALHALLA_URL}/route", json=body, timeout=settings.VALHALLA_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            legs, summary = data["trip"]["legs"], data["trip"]["summary"]
        except (requests.RequestException, KeyError, IndexError, ValueError):
            logger.exception("Valhalla multi-stop routing failed (costing=%s)", costing)
            raise DomainError("ROUTING_UNAVAILABLE", "Could not compute a route through these stops.", status_code=503)
        from core.polyline import decode, encode

        joined = []
        for leg in legs:
            pts = decode(leg["shape"], VALHALLA_POLYLINE_PRECISION)
            joined.extend(pts[1:] if joined else pts)
        return {
            "polyline": encode(joined, VALHALLA_POLYLINE_PRECISION),
            "polyline_precision": VALHALLA_POLYLINE_PRECISION,
            "distance_meters": round(summary["length"] * 1000),
            "duration_seconds": round(summary["time"]),
        }

    @classmethod
    def get_route(cls, pickup_lat, pickup_lng, drop_lat, drop_lng, costing="auto"):
        """The road route between two points: Google when configured (and
        answering), else Valhalla's /route endpoint. Raises DomainError(503)
        if no route can be had — callers surface that as "routing
        unavailable, try again" rather than a 500.
        """
        if cls._use_google():
            route = cls._google_route(pickup_lat, pickup_lng, drop_lat, drop_lng)
            if route is not None:
                return route
        body = {
            "locations": [
                {"lat": float(pickup_lat), "lon": float(pickup_lng)},
                {"lat": float(drop_lat), "lon": float(drop_lng)},
            ],
            "costing": costing,
            "units": "kilometers",
        }

        try:
            response = requests.post(
                f"{settings.VALHALLA_URL}/route", json=body, timeout=settings.VALHALLA_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
            leg = data["trip"]["legs"][0]
            summary = data["trip"]["summary"]
        except (requests.RequestException, KeyError, IndexError, ValueError):
            logger.exception("Valhalla routing request failed (costing=%s)", costing)
            raise DomainError(
                "ROUTING_UNAVAILABLE", "Could not compute a route for this pickup/drop pair.", status_code=503
            )

        return {
            "polyline": leg["shape"],
            "polyline_precision": VALHALLA_POLYLINE_PRECISION,
            "distance_meters": round(summary["length"] * 1000),
            "duration_seconds": round(summary["time"]),
        }
