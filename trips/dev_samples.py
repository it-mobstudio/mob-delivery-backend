"""Sample goods for local testing (`manage.py book_test_trip`): real products with
their real pictures, and a real invoice PDF - all public links, so the driver app
shows exactly what an order from the shop looks like. Nothing is stored locally.
"""

import random
from dataclasses import dataclass


def _picture(path):
    """The shop's product picture, resized to 220 px by images.weserv.nl the way the shop itself serves it."""
    from urllib.parse import quote

    source = quote(f"https://cdn.madoverbuildings.com/products/images/{path}", safe="")
    return f"https://images.weserv.nl/?url={source}&w=220&q=75&output=webp&fit=inside&h=220"


@dataclass(frozen=True)
class Product:
    name: str
    sku: str  # the shop's product code (also the picture's file name)
    unit: str
    quantity: tuple  # (least, most) - a demo order picks a random amount in between
    image_url: str


CATALOGUE = [
    Product("Ultra tech Cement", "560QWI101", "bags", (5, 40), _picture("UltraTech/products/images/UltraTech/560QWI101.webp")),
    Product("Dr. Fixit Water proofing", "564QWI108", "pcs", (1, 12), _picture("Dr.Fixit/564QWI108.webp")),
    Product("Sika SBR Polymer Latex SBR 20kg (Pack of 1)", "564QWI151", "pack", (1, 8), _picture("564QWI151.webp")),
    Product("Atomberg Ceiling Sleek Fan Renesa Halo smart Fan", "576QWI101", "pcs", (1, 4), _picture("Atomberg/576QWI101.webp")),
]

# An invoice for one of the shop's own orders.
INVOICE_URL = (
    "https://cdn.madoverbuildings.com/public/static/pdfs/order2/OD20260921007406/"
    "D-0926-MUMB-340-7406-01_20260921080602821649_aea173.pdf"
)
INVOICE_NUMBER = "D-0926-MUMB-340-7406-01"


def sample_items(count, rng=random):
    """`count` item dicts shaped like the booking API's `items`: the shop's products in
    order (cycling, with a "#2"... suffix, past the fourth), each with its own picture
    and a random quantity."""
    items = []
    for index in range(count):
        product = CATALOGUE[index % len(CATALOGUE)]
        round_number = index // len(CATALOGUE)
        items.append(
            {
                "name": product.name if round_number == 0 else f"{product.name} #{round_number + 1}",
                "quantity": rng.randint(*product.quantity),
                "unit": product.unit,
                "sku": product.sku,
                "image_url": product.image_url,
            }
        )
    return items


# -- people and places ------------------------------------------------------------
# So a test order reads like a real one: a real shop name at the pickup, a real
# person at the drop, and street addresses built from what OpenStreetMap knows
# about the actual coordinates (falling back to believable local ones offline).

SHOPS = [
    "Backyard Decor", "Sharma Hardware & Sanitary", "Gupta Building Materials",
    "Shree Balaji Tiles & Marbles", "Krishna Paints & Hardware", "Royal Sanitaryware",
    "Om Sai Traders", "New India Plywood House", "Jai Mata Di Cement Store",
]
CUSTOMERS = [
    "Rahul Mehta", "Priya Sharma", "Ankit Verma", "Neha Gupta", "Rohit Singh",
    "Sneha Iyer", "Amit Kumar", "Kavya Reddy", "Vikram Chauhan", "Pooja Nair",
]
DRIVERS = [
    "Ramesh Kumar", "Suresh Yadav", "Manoj Tiwari", "Rajesh Patel", "Sanjay Verma",
    "Deepak Singh", "Anil Sharma", "Vijay Mishra", "Mukesh Chauhan", "Arun Nair",
]
_SOCIETIES = ["Prestige Residency", "Sunshine Apartments", "Green Valley Homes", "Shanti Kunj", "Silver Oak Enclave"]
_STREETS = ["Main Road", "Station Road", "Market Road", "Ring Road", "Nehru Marg"]
_FALLBACK_AREAS = [
    # (lat, lng, city, [localities], PIN)
    (12.9716, 77.5946, "Bengaluru", ["Indiranagar", "Koramangala", "Whitefield", "Jayanagar", "HSR Layout"], "5600"),
    (26.8467, 80.9462, "Lucknow", ["Gomti Nagar", "Hazratganj", "Aliganj", "Indira Nagar", "Alambagh"], "2260"),
    (28.6139, 77.2090, "New Delhi", ["Lajpat Nagar", "Karol Bagh", "Dwarka", "Saket", "Rohini"], "1100"),
    (19.0760, 72.8777, "Mumbai", ["Andheri East", "Bandra West", "Powai", "Dadar", "Malad"], "4000"),
    (17.3850, 78.4867, "Hyderabad", ["Banjara Hills", "Madhapur", "Kukatpally", "Begumpet"], "5000"),
]


def _nearest_fallback(lat, lng):
    return min(_FALLBACK_AREAS, key=lambda a: (a[0] - lat) ** 2 + (a[1] - lng) ** 2)


def _reverse_geocode(lat, lng):
    """The OpenStreetMap address parts for a point, or None (offline, slow,
    rate-limited). Dev tooling only - never used by the API itself."""
    import requests

    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 18, "addressdetails": 1},
            headers={"User-Agent": "mob-delivery-dev/1.0 (book_test_trip)"},
            timeout=5,
        )
        response.raise_for_status()
        return response.json().get("address") or None
    except Exception:
        return None


def realistic_address(lat, lng, kind, rng=random):
    """A believable street address at (lat, lng): `kind` "shop" gives a shop
    unit, "home" a flat in a housing society."""
    parts = _reverse_geocode(lat, lng) or {}
    fallback = _nearest_fallback(lat, lng)
    locality = (
        parts.get("neighbourhood") or parts.get("suburb") or parts.get("quarter")
        or parts.get("village") or parts.get("county") or rng.choice(fallback[3])
    )
    road = parts.get("road") or f"{locality} {rng.choice(_STREETS)}"
    city = parts.get("city") or parts.get("town") or parts.get("state_district") or fallback[2]
    pin = parts.get("postcode") or f"{fallback[4]}{rng.randint(10, 99)}"
    if kind == "shop":
        head = f"Shop No. {rng.randint(3, 48)}, {road}"
    else:
        head = f"Flat {rng.randint(1, 12)}0{rng.randint(1, 4)}, {rng.choice(_SOCIETIES)}, {road}"
    seen, out = set(), []
    for piece in [head, locality, f"{city} {pin}"]:
        if piece and piece.lower() not in seen:
            seen.add(piece.lower())
            out.append(piece)
    return ", ".join(out)[:255]
