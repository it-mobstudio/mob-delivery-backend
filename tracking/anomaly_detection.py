"""Pure geometry helpers for anomaly detection — no DB access, no side
effects. Called by tasks.py, which handles orchestration (querying active
trips/pings, creating alerts, broadcasting) so the math stays testable in
isolation.
"""

import math

from core.geo import haversine_distance_m
from trips.models import StopStatus


def bearing_degrees(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = (math.radians(float(v)) for v in (lat1, lng1, lat2, lng2))
    dlng = lng2 - lng1
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angular_difference(a, b):
    diff = abs(a - b) % 360
    return min(diff, 360 - diff)


def target_stop_for(trip):
    """The next stop the vehicle is headed toward — first incomplete stop by
    sequence order.
    """
    return (
        trip.stops.filter(status__in=[StopStatus.PENDING, StopStatus.ARRIVED]).order_by("sequence_no").first()
    )


def is_stationary(pings, radius_m):
    """`pings` need at least 2 points; every ping must fall within `radius_m`
    of the first to count as stationary (a single outlier disqualifies it).
    """
    pings = list(pings)
    if len(pings) < 2:
        return False
    origin = pings[0]
    return all(
        haversine_distance_m(origin.latitude, origin.longitude, p.latitude, p.longitude) <= radius_m
        for p in pings[1:]
    )


def is_wrong_direction(pings, target_lat, target_lng, threshold_degrees, min_consecutive):
    """`pings` must be chronologically ordered oldest→newest and contain at
    least `min_consecutive + 1` points. Flags only if EVERY one of the last
    `min_consecutive` travel bearings deviates from the bearing-to-target by
    more than `threshold_degrees` — a single noisy GPS blip won't trip it.
    """
    pings = list(pings)
    if len(pings) < min_consecutive + 1:
        return False

    recent = pings[-(min_consecutive + 1):]
    deviations = []
    for prev, cur in zip(recent, recent[1:]):
        travel_bearing = bearing_degrees(prev.latitude, prev.longitude, cur.latitude, cur.longitude)
        target_bearing = bearing_degrees(cur.latitude, cur.longitude, target_lat, target_lng)
        deviations.append(angular_difference(travel_bearing, target_bearing))

    return all(d > threshold_degrees for d in deviations)
