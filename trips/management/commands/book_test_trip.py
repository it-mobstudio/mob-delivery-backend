import argparse
import random
import time
from decimal import Decimal
from math import cos, radians, sin

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.choices import PaymentMode, TripStatus
from core.exceptions import DomainError
from drivers.models import Driver, Vehicle
from trips.models import ACTIVE_TRIP_STATUSES, Trip
from trips import dev_samples
from trips.services import TripService


DEFAULT_DRIVER = "+919000000000"


def _point(lat, lng, address, contact_name, contact_phone):
    return {
        "address": address,
        "lat": Decimal(str(round(lat, 6))),
        "lng": Decimal(str(round(lng, 6))),
        "contact_name": contact_name,
        "contact_phone": contact_phone,
    }


class Command(BaseCommand):
    help = (
        "Books a RANDOM test order exactly as the company's system would (POST /trips) and "
        "reports who got it — for walking through the driver app's whole delivery flow "
        "without building a booking client first.\n\n"
        "Every run is different: a random pick of the shop's products (real names and pictures, "
        "random quantities) that the driver must VERIFY at the drop, a random drop 2-8 km away "
        "in any direction, a random shop, customer, note and payment mode (mostly cash on "
        "delivery: payment QR then the customer's OTP), plus a real invoice PDF. Fix any part "
        "with --items N (0 for none), --mode cod|prepaid, --distance-km, --note, --no-invoice, "
        "--pickup-photo / --delivery-photo; --seed N repeats a run exactly; --count N books "
        "several.\n\n"
        "--customer 98XXXXXXXX also puts the order in that customer's web app (at the site root, /): their "
        "bookings, live tracking and the delivery OTP.\n\n"
        "The pickup is placed at the driver's own last reported location (so they're the "
        "nearest driver and get it). The driver must be ON DUTY: open the app and tap 'Start "
        "duty' first. Without --phone, any on-duty driver is used if +919000000000 isn't."
    )

    def add_arguments(self, parser):
        parser.add_argument("--phone", default=None,
                            help="Driver to place the trip next to (default: +919000000000, else any driver on duty).")
        parser.add_argument("--mode", choices=["cod", "prepaid"], default=None,
                            help="cod shows the payment QR + delivery OTP steps (default: random, mostly cod).")
        parser.add_argument("--pickup-address", help="Free text shown in the app.")
        parser.add_argument("--pickup-lat", type=float, help="Default: the driver's last reported latitude.")
        parser.add_argument("--pickup-lng", type=float)
        parser.add_argument("--drop-address", help="Free text shown in the app.")
        parser.add_argument("--drop-lat", type=float, help="Default: --distance-km north-east of the pickup.")
        parser.add_argument("--drop-lng", type=float)
        parser.add_argument("--distance-km", type=float, default=None,
                            help="Pickup→drop distance when --drop-lat/lng are omitted (default: random 2-8 km, random direction).")
        parser.add_argument("--customer-name", default=None, help="Default: a realistic random name.")
        parser.add_argument("--customer-phone", default="+919888800002", help="The delivery OTP is texted here on a COD trip.")
        parser.add_argument(
            "--customer", default=None, metavar="PHONE",
            help="A customer of the booking web app (at the site root, /) to book it for: it shows in their bookings with live "
                 "tracking and the delivery OTP, and they're the receiver. Created if new.",
        )
        parser.add_argument(
            "--items", type=int, default=None, metavar="N",
            help=f"How many different products to put on the order, each with a random quantity "
                 f"(default: random 1-{len(dev_samples.CATALOGUE)}; 0 for none; past {len(dev_samples.CATALOGUE)} they repeat).",
        )
        parser.add_argument("--count", type=int, default=1, metavar="N", help="Book N random orders (default: 1). "
                            "A driver holds one trip at a time, so the rest go to other drivers on duty or wait.")
        parser.add_argument("--seed", type=int, default=None, help="Make the random choices repeatable.")
        parser.add_argument(
            "--verify-items", action=argparse.BooleanOptionalAction, default=None,
            help="Make the driver verify every item at the drop (default: on whenever there are items).",
        )
        parser.add_argument(
            "--invoice", action=argparse.BooleanOptionalAction, default=True,
            help="Attach an invoice PDF with Download / WhatsApp / Share (default: on).",
        )
        parser.add_argument(
            "--invoice-url", default=None,
            help="Use this invoice link instead of the sample one (only with an invoice).",
        )
        parser.add_argument(
            "--bonus", type=Decimal, default=None, metavar="AMOUNT",
            help="An extra flat amount for the driver on top of the fare (e.g. --bonus 100), for trying out "
                 "the new-order screen's bonus chip. Default: none.",
        )
        parser.add_argument(
            "--pickup-photo", choices=["none", "order", "per_item", "both"], default=None,
            help="Camera photos the driver must take at the pickup: 'order' (one of the whole order), 'per_item' "
                 "(one per item) or 'both' (default: both when there are items, else order).",
        )
        parser.add_argument(
            "--delivery-otp", action=argparse.BooleanOptionalAction, default=True,
            help="Prepaid trips: the customer's delivery OTP is still needed to complete (default: on; COD always needs it).",
        )
        parser.add_argument(
            "--note", default=None,
            help="A note for the driver on the order (default: a random sample note; pass '' for none).",
        )
        parser.add_argument(
            "--delivery-photo", choices=["none", "order", "per_item", "both"], default=None,
            help="The same at the drop, before payment / completion (default: both when there are items, else order).",
        )
        parser.add_argument(
            "--voice-note", action=argparse.BooleanOptionalAction, default=True,
            help="Attach a short spoken note from the dispatcher, which the driver plays in the app (default: on).",
        )

    def handle(self, *args, **options):
        if options["count"] < 1:
            raise CommandError("--count must be at least 1.")
        if options["items"] is not None and options["items"] < 0:
            raise CommandError("--items can't be negative.")
        rng = random.Random(options["seed"])
        for n in range(options["count"]):
            if options["count"] > 1:
                self.stdout.write(self.style.MIGRATE_HEADING(f"— order {n + 1} of {options['count']} —"))
            self._book_one(options, rng)

    def _book_one(self, options, rng):
        driver = self._driver(options["phone"])
        vehicle = self._vehicle(driver)

        pickup_lat, pickup_lng = self._pickup(driver, options)
        drop_lat, drop_lng = self._drop(pickup_lat, pickup_lng, options, rng)
        customer = self._customer(options["customer"], driver.company) if options["customer"] else None

        # Real-looking people and street addresses (from OpenStreetMap for these
        # exact coordinates). The phone numbers stay the fixed test ones (or the
        # web customer's own), so an OTP text can never reach a stranger.
        pickup = _point(
            pickup_lat,
            pickup_lng,
            options["pickup_address"] or dev_samples.realistic_address(pickup_lat, pickup_lng, "shop", rng),
            rng.choice(dev_samples.SHOPS),
            "+919888800001",
        )
        drop = _point(
            drop_lat,
            drop_lng,
            options["drop_address"] or dev_samples.realistic_address(drop_lat, drop_lng, "home", rng),
            options["customer_name"] or (customer.full_name if customer and customer.full_name else rng.choice(dev_samples.CUSTOMERS)),
            customer.phone_number if customer else options["customer_phone"],
        )

        reference = f"SO-{int(time.time() * 1000) % 10_000_000:07d}"
        item_count = options["items"]
        items = dev_samples.random_items(item_count, rng) if item_count != 0 else None
        item_count = len(items or [])
        verify_items = item_count > 0 if options["verify_items"] is None else options["verify_items"]
        if verify_items and item_count == 0:
            raise CommandError("--verify-items needs at least one item: pass --items N (N ≥ 1).")
        if item_count:
            pickup_photo = options["pickup_photo"] or rng.choice(["order", "per_item", "both"])
            delivery_photo = options["delivery_photo"] or rng.choice(["order", "per_item", "both"])
        else:
            pickup_photo = options["pickup_photo"] or "order"
            delivery_photo = options["delivery_photo"] or "order"
        if delivery_photo in ("per_item", "both") and item_count == 0:
            raise CommandError("--delivery-photo per_item needs at least one item: pass --items N (N ≥ 1).")
        if pickup_photo in ("per_item", "both") and item_count == 0:
            raise CommandError("--pickup-photo per_item needs at least one item: pass --items N (N ≥ 1).")
        invoice_number = dev_samples.INVOICE_NUMBER if options["invoice"] else ""
        invoice_url = (options["invoice_url"] or dev_samples.INVOICE_URL) if options["invoice"] else ""
        mode = options["mode"] or rng.choices(["cod", "prepaid"], weights=[3, 1])[0]
        note = options["note"] if options["note"] is not None else rng.choice(dev_samples.NOTES)

        try:
            trip = TripService.create_trip(
                company=driver.company,
                vehicle_type=vehicle.vehicle_type,
                pickup=pickup,
                drop=drop,
                payment_mode=PaymentMode.COD if mode == "cod" else PaymentMode.PREPAID,
                reference_id=reference,
                invoice_url=invoice_url,
                invoice_number=invoice_number,
                verify_items=verify_items,
                items=items,
                bonus_fare=options["bonus"],
                pickup_photo=pickup_photo,
                delivery_photo=delivery_photo,
                notes=note,
                delivery_otp=options["delivery_otp"],
            )
        except DomainError as exc:
            if exc.code == "ROUTING_UNAVAILABLE":
                raise CommandError(
                    "The routing engine isn't reachable (VALHALLA_URL), so no route could be drawn.\n"
                    "  • Quick local stand-in that works anywhere:  python scripts/dev_valhalla_stub.py\n"
                    "  • Real thing (Bengaluru map only):           docker compose up -d valhalla"
                )
            raise CommandError(f"{exc.code}: {exc.detail}")

        if customer:
            trip.customer = customer
            trip.save(update_fields=["customer", "updated_at"])
        if options["voice_note"]:
            self._attach_voice_note(trip)
        self._report(trip, driver, options)
        if customer:
            self.stdout.write(f"  Customer web app: /trips/{trip.id}/  (sign in as {customer.phone_number})")

    # -- inputs ------------------------------------------------------------------

    def _driver(self, phone):
        if phone is None:
            default = Driver.objects.select_related("company").filter(phone_number=DEFAULT_DRIVER).first()
            busy = default is not None and Trip.objects.filter(driver=default, status__in=ACTIVE_TRIP_STATUSES).exists()
            if default is None or not default.is_online or busy:
                other = self._any_driver_on_duty()
                if other is not None:
                    why = "is on another trip" if busy else "isn't on duty"
                    self.stdout.write(f"{DEFAULT_DRIVER} {why}: using {other.full_name} ({other.phone_number}) — on duty and free.")
                    return other
            phone = DEFAULT_DRIVER
        try:
            driver = Driver.objects.select_related("company").get(phone_number=phone)
        except Driver.DoesNotExist:
            raise CommandError(f"No driver with phone {phone}. (seed_drivers creates +919000000000 … 09.)")
        if not driver.is_online:
            raise CommandError(
                f"{driver.full_name} is offline, so nobody would be assigned this trip.\n"
                "Open the driver app, tap 'Start duty' (allow location, pick a vehicle), then run this again."
            )
        return driver

    @staticmethod
    def _any_driver_on_duty():
        busy = Trip.objects.filter(status__in=ACTIVE_TRIP_STATUSES, driver_id__isnull=False).values("driver_id")
        return (Driver.objects.select_related("company")
                .filter(is_online=True, current_vehicle_id__isnull=False, last_known_lat__isnull=False)
                .exclude(id__in=busy).order_by("?").first())

    @staticmethod
    def _customer(phone, company):
        from booking.models import Customer
        from booking.services import normalise_phone

        try:
            phone = normalise_phone(phone)
        except DomainError as exc:
            raise CommandError(f"--customer: {exc.detail}")
        customer, _ = Customer.objects.get_or_create(company=company, phone_number=phone)
        return customer

    def _vehicle(self, driver):
        vehicle = Vehicle.objects.select_related("vehicle_type").filter(pk=driver.current_vehicle_id).first()
        if vehicle is None:
            raise CommandError(f"{driver.full_name} is on duty but has no vehicle selected.")
        return vehicle

    def _pickup(self, driver, options):
        if options["pickup_lat"] is not None and options["pickup_lng"] is not None:
            return options["pickup_lat"], options["pickup_lng"]
        if driver.last_known_lat is None or driver.last_known_lng is None:
            raise CommandError(
                f"{driver.full_name} hasn't reported a location yet. Give the app location access, "
                "or pass --pickup-lat/--pickup-lng."
            )
        return float(driver.last_known_lat), float(driver.last_known_lng)

    def _drop(self, pickup_lat, pickup_lng, options, rng):
        if options["drop_lat"] is not None and options["drop_lng"] is not None:
            return options["drop_lat"], options["drop_lng"]
        # A random bearing, converting km to degrees (a degree of longitude
        # shrinks with latitude).
        km = options["distance_km"] if options["distance_km"] is not None else rng.uniform(2, 8)
        bearing = radians(rng.uniform(0, 360))
        return (pickup_lat + km * cos(bearing) / 111.0,
                pickup_lng + km * sin(bearing) / (111.0 * cos(radians(pickup_lat))))

    def _attach_voice_note(self, trip):
        """A real recording (trips/dev_assets/voice_note.m4a), stored the way a
        dispatcher's upload from the console would be."""
        from pathlib import Path

        from django.core.files import File

        from core.choices import UploadPurpose
        from core.uploads import UploadService

        path = Path(__file__).resolve().parents[2] / "dev_assets" / "voice_note.m4a"
        with path.open("rb") as fh:
            trip.voice_note_url = UploadService.store(File(fh, name="voice_note.m4a"), UploadPurpose.TRIP_VOICE_NOTE, trip.company_id)
        trip.voice_note_seconds = 10
        trip.save(update_fields=["voice_note_url", "voice_note_seconds", "updated_at"])

    # -- output ------------------------------------------------------------------

    def _report(self, trip, driver, options):
        w = self.stdout.write
        w(f"Order {trip.order_number}  (trip {trip.id})")
        w(f"  Pickup: {trip.pickup_contact_name} — {trip.pickup_address}")
        w(f"  Drop:   {trip.drop_contact_name} — {trip.drop_address}")
        asks = [
            f"pickup photo: {trip.pickup_photo}",
            f"delivery photo: {trip.delivery_photo}",
            f"delivery OTP: {'yes' if trip.delivery_otp or trip.payment_mode == PaymentMode.COD else 'no'}",
            f"item check: {'yes' if trip.verify_items else 'no'}",
            f"voice note: {str(trip.voice_note_seconds) + ' s' if trip.voice_note_url else 'no'}",
        ]
        w("  The app will ask for → " + " · ".join(asks))
        bonus = f" + ₹{trip.bonus_fare} bonus" if trip.bonus_fare else ""
        w(f"  {trip.distance_meters / 1000:.1f} km · ₹{trip.total_fare}{bonus} · {trip.get_payment_mode_display()} · status: {trip.status}")
        items = list(trip.items.all())
        if items:
            w(f"  {len(items)} item(s)" + (" — driver must verify each one at the drop:" if trip.verify_items else ":"))
            for item in items:
                w(f"    • {item.name} × {item.quantity}{(' ' + item.unit) if item.unit else ''}")
        if trip.invoice_url:
            w(f"  invoice {trip.invoice_number}: {trip.invoice_url}")

        if trip.status == TripStatus.ASSIGNED and trip.driver_id == driver.id:
            self.stdout.write(self.style.SUCCESS(f"Assigned to {driver.full_name}. It appears in the app within ~5 seconds."))
            self._checklist(trip)
        elif trip.status == TripStatus.ASSIGNED:
            self.stdout.write(self.style.WARNING(
                f"Assigned to {trip.driver.full_name}, not {driver.full_name} — another online driver was nearer. "
                "Take them offline, or pass --pickup-lat/--pickup-lng at this driver's own location."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                "No driver was available: they must be online, KYC-verified, on a vehicle of the right type, "
                f"not already on a trip, and within {self._radius()} km of the pickup."
            ))

    def _checklist(self, trip):
        """What to try in the app, in order, so the run-through covers everything this order carries."""
        w = self.stdout.write
        step = 0

        def line(text):
            nonlocal step
            step += 1
            w(f"    {step}. {text}")

        w("  Try, in the app:")
        if trip.voice_note_url:
            line("Open the order and play the dispatcher's voice note.")
        line("Swipe 'Reached pickup'.")
        if trip.pickup_photo != "none":
            line({"order": "Swipe 'Pickup order' — it asks for a camera photo of the whole order first.",
                  "per_item": "Swipe 'Pickup order' — it asks for a camera photo of every item first.",
                  "both": "Swipe 'Pickup order' — it asks for a photo of the whole order AND one of every item first."}[trip.pickup_photo])
        else:
            line("Swipe 'Pickup order'.")
        line("Swipe 'Deliver order' — it walks you through what the drop needs:")
        if trip.verify_items:
            w("         • the item checklist: Delivered (photo optional) or Problem + a reason for each item")
        if trip.delivery_photo != "none":
            w(f"         • delivery photos ({trip.get_delivery_photo_display().lower()})")
        if trip.payment_mode == PaymentMode.COD:
            w("         • collect the payment (QR), then the customer's OTP")
            self._payment_note()
        elif trip.delivery_otp:
            w("         • the customer's 4-digit OTP (shown on screen in dev) — no payment step")
        else:
            w("         • confirm the handover")
        if trip.invoice_url:
            line("On the order details, Download / WhatsApp / Share the invoice.")
        w(f"  Everything the driver sent (photos, notes, voice note) shows in the console: /admin/orders/{trip.id}/")

    def _payment_note(self):
        """The payment step comes right after the item checks, so say now whether it will work."""
        w = self.stdout.write
        provider = settings.PAYMENT_PROVIDER
        base = settings.RAZORPAY_API_BASE
        if provider == "razorpay" and not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
            w(self.style.WARNING(
                "  ⚠ Payment isn't set up: PAYMENT_PROVIDER is 'razorpay' but there are no Razorpay keys, so the QR step\n"
                "    will say \"Online payments aren't set up on this server yet\". Pick one, then restart the server:\n"
                "      • the local stand-in:  python scripts/dev_razorpay_stub.py  and in .env\n"
                "          RAZORPAY_KEY_ID=rzp_test_stub  RAZORPAY_KEY_SECRET=stub_secret\n"
                "          RAZORPAY_WEBHOOK_SECRET=stub_webhook_secret  RAZORPAY_API_BASE=http://127.0.0.1:8003/v1\n"
                "      • no verification at all:  PAYMENT_PROVIDER=upi_static  (the driver's \"Payment received\" tap is trusted)\n"
                "    Details: the app README, \"Razorpay locally\"."
            ))
        elif provider == "razorpay" and ("127.0.0.1" in base or "localhost" in base):
            w("  Payment: Razorpay stand-in. With the QR showing, \"pay\" it:  curl -X POST http://127.0.0.1:8003/simulate/<qr id>")
            w("           (http://127.0.0.1:8003/ lists the codes it has issued; the app moves on by itself once one is paid.)")
        elif provider == "razorpay":
            w("  Payment: Razorpay. Pay the QR from Razorpay's test tools (Test mode) - the app moves on by itself once it's paid.")
        elif provider == "upi_static":
            w("  Payment: 'upi_static' (dev only) - nothing verifies the payment; the driver's \"Payment received\" tap is trusted.")

    @staticmethod
    def _radius():
        from django.conf import settings

        return settings.DRIVER_MATCH_RADIUS_KM
