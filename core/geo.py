"""Shared geometry helpers — no DB access, no side effects. Used by both
tracking.anomaly_detection (stationary/wrong-direction alerts) and
trips.services (delivery geofence check), which is why this lives in core
rather than either domain app.
"""

import math

EARTH_RADIUS_M = 6371000


def haversine_distance_m(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = (math.radians(float(v)) for v in (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
