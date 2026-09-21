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
