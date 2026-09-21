"""The prose pages of the documentation (introduction, authentication, trip
lifecycle, walkthroughs, payments, ...).

The text lives next to this module in guides/, in Markdown - the format an OpenAPI
description is written in - and is only ever the *source*: the documentation people
read is the HTML built from it (served at /api/redoc/ and /api/docs/, and written
to docs/api/index.html by `manage.py build_api_docs`). Each page becomes a section
at the top of that HTML (see core.openapi.tags)."""

from pathlib import Path

from . import errors

GUIDES_DIR = Path(__file__).parent / "guides"

# (section name in the menu, file). Order is the menu order.
GUIDE_FILES = [
    ("Introduction", "01-introduction.md"),
    ("Authentication", "02-authentication.md"),
    ("Conventions", "03-conventions.md"),
    ("Errors", "04-errors.md"),
    ("Trip lifecycle", "05-trip-lifecycle.md"),
    ("Booking a delivery", "06-booking-a-delivery.md"),
    ("Driver app walkthrough", "07-driver-app-walkthrough.md"),
    ("Payments (Razorpay)", "08-payments.md"),
    ("Items & invoices", "09-items-and-invoices.md"),
    ("Driver onboarding & wallet", "10-driver-onboarding-and-wallet.md"),
]

# {{PLACEHOLDER}} -> generated text, so a table that has to match the code is
# produced from the code instead of typed twice.
PLACEHOLDERS = {"{{ERROR_CATALOG}}": errors.catalog_markdown}


def load():
    pages = []
    for name, filename in GUIDE_FILES:
        text = (GUIDES_DIR / filename).read_text(encoding="utf-8")
        for token, produce in PLACEHOLDERS.items():
            text = text.replace(token, produce())
        pages.append((name, text.strip()))
    return pages
