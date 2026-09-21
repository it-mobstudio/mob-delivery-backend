"""Placeholder goods for local testing: item pictures and a sample invoice PDF,
generated with Pillow so `manage.py book_test_trip --items N --invoice` works
offline. Written through the configured storage (local media/ in dev), and the
URLs handed back are whatever that storage returns — relative `/media/...`
locally, which the API resolves against the host the app reached us on.
"""

import io

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageDraw, ImageFont

# (name, quantity, unit, unit price) — a builder's-merchant order, since that's
# what this platform delivers.
SAMPLE_ITEMS = [
    ("Cement bag 50 kg", 4, "bags", "380.00"),
    ("TMT bar 12 mm", 20, "pcs", "95.00"),
    ("Wall putty 20 kg", 2, "bags", "1250.00"),
    ("Paint bucket 20 L", 1, "pcs", "4200.00"),
    ("Plywood 8x4 ft", 6, "sheets", "1450.00"),
]

_COLOURS = [(41, 115, 240), (22, 163, 106), (216, 130, 18), (148, 90, 210), (217, 77, 61)]


def _font(size):
    return ImageFont.load_default(size=size)


def _store(name, data):
    if default_storage.exists(name):
        default_storage.delete(name)  # re-runs refresh the file rather than piling up copies
    return default_storage.url(default_storage.save(name, ContentFile(data)))


def item_image_url(index, name):
    """A coloured tile with the item's initials — enough to see the picture slot work."""
    image = Image.new("RGB", (320, 320), _COLOURS[index % len(_COLOURS)])
    draw = ImageDraw.Draw(image)
    initials = "".join(word[0] for word in name.split()[:2]).upper()
    draw.text((160, 160), initials, fill="white", font=_font(120), anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return _store(f"dev-samples/item-{index}.jpg", buffer.getvalue())


def sample_items(count):
    """`count` item dicts shaped like the booking API's `items`, each with a picture."""
    items = []
    for index in range(count):
        name, quantity, unit, price = SAMPLE_ITEMS[index % len(SAMPLE_ITEMS)]
        items.append(
            {
                "name": name if index < len(SAMPLE_ITEMS) else f"{name} #{index + 1}",
                "quantity": quantity,
                "unit": unit,
                "sku": f"SKU-{1000 + index}",
                "unit_price": price,
                "image_url": item_image_url(index, name),
            }
        )
    return items


def invoice_pdf_url(invoice_number, items):
    """A one-page invoice as a real PDF, so Download / Share / WhatsApp have
    something genuine to fetch."""
    page = Image.new("RGB", (595, 842), "white")
    draw = ImageDraw.Draw(page)
    draw.text((40, 40), "TAX INVOICE", fill=(16, 42, 67), font=_font(28))
    draw.text((40, 84), f"Invoice no: {invoice_number}", fill=(102, 120, 138), font=_font(15))
    draw.text((40, 106), "MOB Delivery — sample invoice for testing", fill=(102, 120, 138), font=_font(15))
    draw.line((40, 140, 555, 140), fill=(231, 236, 241), width=2)

    y, total = 160, 0
    for item in items:
        line = float(item["unit_price"]) * item["quantity"]
        total += line
        draw.text((40, y), f"{item['quantity']} x {item['name']}", fill=(16, 42, 67), font=_font(16))
        draw.text((555, y), f"Rs {line:,.2f}", fill=(16, 42, 67), font=_font(16), anchor="ra")
        y += 30
    draw.line((40, y + 6, 555, y + 6), fill=(231, 236, 241), width=2)
    draw.text((40, y + 22), "Total", fill=(16, 42, 67), font=_font(20))
    draw.text((555, y + 22), f"Rs {total:,.2f}", fill=(16, 42, 67), font=_font(20), anchor="ra")

    buffer = io.BytesIO()
    page.save(buffer, "PDF", resolution=72.0)
    return _store(f"dev-samples/{invoice_number}.pdf", buffer.getvalue())
