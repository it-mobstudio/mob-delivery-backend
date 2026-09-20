from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0


def haversine_distance_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km between two lat/lng points. Good enough
    for nearest-driver ranking within a single city — no need for a real
    routing call just to rank candidates before picking one to route to.
    """
    lat1, lng1, lat2, lng2 = (radians(float(v)) for v in (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))
