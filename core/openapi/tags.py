"""The documentation's menu: which sections exist, in what order, under which
heading. ReDoc turns GROUPS into the left-hand menu (group -> section ->
endpoints); Swagger UI shows the same sections in the same order.

Two kinds of section:
* GUIDES - prose pages, from core/openapi/guides/*.md (no endpoints).
* ENDPOINT_TAGS - real endpoints; each operation names one in `doc(tag=...)`.
"""

from . import guides

# (name, blurb shown above the section's endpoints)
ENDPOINT_TAGS = [
    # -- Company API ---------------------------------------------------------
    ("Company authentication", "Get the bearer token every other company call needs. **Server-to-server integrations** use an API client (`client-token`); the company's own admin panel signs in with email + password."),
    ("Trips", "Book deliveries and follow them. A trip is priced up-front, matched to the nearest eligible driver automatically, and then driven by the driver's app through its lifecycle - see the **Trip lifecycle** guide."),
    ("Vehicle types", "The categories you sell (Bike, Mini truck, ...) with their fare card. A trip is booked against one vehicle type."),
    ("Vehicles", "The company's physical fleet. Drivers pick a free vehicle when they go on duty."),
    ("Vehicle documents", "Registration, insurance and other paperwork attached to a vehicle."),
    ("Drivers", "Create and manage the company's drivers, and review the KYC documents they submit from the app."),
    ("Driver wallets", "The company's side of a driver's earnings ledger: read a balance, then record payouts, bonuses, penalties and corrections."),
    ("Uploads", "Store a file (invoice, item photo, vehicle photo, ...) and get back a URL to use in another request."),
    # -- Driver app API ------------------------------------------------------
    ("Driver sign-in", "Phone-number + SMS OTP login for drivers, and keeping the session alive. New numbers become new (unapproved) drivers automatically."),
    ("Driver profile & onboarding", "The signed-in driver's own profile, KYC submissions (Aadhaar, licence, police verification, photo) and account controls."),
    ("Driver duty & location", "Going on and off duty with a vehicle, and sending GPS pings so trips can be matched."),
    ("Driver vehicles", "The driver's own vehicles: add several, each with pictures, and take any of them on duty. The company's fleet vehicles stay available alongside."),
    ("Driver trips", "The trip the driver is working on, its history, and the step-by-step lifecycle actions: arrive, start, complete, cancel."),
    ("Driver payments", "Cash-on-delivery: the Razorpay scan-to-pay code, confirming the payment, and the delivery OTP."),
    ("Item verification", "For orders that ask the driver to confirm each item: mark delivered / not delivered, optionally with a camera photo."),
    ("Driver earnings", "The signed-in driver's wallet ledger and today's/all-time delivery stats."),
    # -- Webhooks ------------------------------------------------------------
    ("Razorpay webhook", "The one endpoint Razorpay calls (you don't). Documented so you can configure the webhook and understand what it does."),
]

TAG_GROUPS = [
    ("Start here", ["Introduction", "Authentication", "Conventions", "Errors"]),
    ("Guides", ["Trip lifecycle", "Booking a delivery", "Driver app walkthrough", "Payments (Razorpay)", "Items & invoices", "Driver onboarding & wallet"]),
    ("Company API", [
        "Company authentication", "Trips", "Vehicle types", "Vehicles", "Vehicle documents",
        "Drivers", "Driver wallets", "Uploads",
    ]),
    ("Driver app API", [
        "Driver sign-in", "Driver profile & onboarding", "Driver duty & location", "Driver vehicles", "Driver trips",
        "Driver payments", "Item verification", "Driver earnings",
    ]),
    ("Webhooks", ["Razorpay webhook"]),
]


def build_tags():
    """The OpenAPI `tags` list: guides first (flagged as prose), then endpoints."""
    tags = []
    for name, markdown in guides.load():
        tags.append({"name": name, "description": markdown, "x-traitTag": True})
    for name, blurb in ENDPOINT_TAGS:
        tags.append({"name": name, "description": blurb})
    return tags


def build_groups():
    return [{"name": name, "tags": tags} for name, tags in TAG_GROUPS]
