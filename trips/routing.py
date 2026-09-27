import logging

import requests
from django.conf import settings

from core.constants import CATEGORY_COSTING, VALHALLA_POLYLINE_PRECISION
from core.exceptions import DomainError

logger = logging.getLogger(__name__)


class RoutingService:
    """Thin client for the self-hosted Valhalla routing engine (see
    docker-compose.yml's valhalla service) — distance/duration/polyline for
    a pickup/drop pair.
    """

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
        """Calls Valhalla's /route endpoint. Raises DomainError(503) if
        Valhalla is unreachable or returns something unparseable — callers
        surface that as "routing unavailable, try again" rather than a 500.
        """
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
