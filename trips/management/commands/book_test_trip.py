import argparse
import time
from decimal import Decimal
from math import cos, radians

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.choices import PaymentMode, TripStatus
from core.exceptions import DomainError
from drivers.models import Driver, Vehicle
from trips import dev_samples
from trips.services import TripService


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
        "Books a test trip exactly as the company's system would (POST /trips) and "
        "reports who got it — for walking through the driver app's whole delivery flow "
        "without building a booking client first.\n\n"
        "By default the order is the FULL flow: the shop's four sample products (real names "
        "and pictures, random quantities) that the driver must VERIFY one by one at the drop "
        "(delivered / problem + reason, camera photo optional), a real invoice PDF (download / "
        "WhatsApp / share), cash on delivery (payment QR then the customer's OTP). Use "
        "--items N (0 for none), --no-verify-items, --no-invoice, --invoice-url URL or "
        "--mode prepaid to change it.\n\n"
        "The pickup is placed at the driver's own last reported location (so they're the "
        "nearest driver and get it) with the drop ~4 km away. The driver must already be ON "
        "DUTY: open the app and tap 'Start duty' first."
    )

    def add_arguments(self, parser):
        parser.add_argument("--phone", default="+919000000000", help="Driver to place the trip next to (default: %(default)s).")
        parser.add_argument("--mode", choices=["cod", "prepaid"], default="cod", help="cod shows the payment QR + delivery OTP steps (default).")
        parser.add_argument("--pickup-address", help="Free text shown in the app.")
        parser.add_argument("--pickup-lat", type=float, help="Default: the driver's last reported latitude.")
        parser.add_argument("--pickup-lng", type=float)
        parser.add_argument("--drop-address", help="Free text shown in the app.")
        parser.add_argument("--drop-lat", type=float, help="Default: --distance-km north-east of the pickup.")
        parser.add_argument("--drop-lng", type=float)
        parser.add_argument("--distance-km", type=float, default=4.0, help="Pickup→drop distance when --drop-lat/lng are omitted (default: %(default)s).")
        parser.add_argument("--customer-name", default="Asha (test customer)")
        parser.add_argument("--customer-phone", default="+919888800002", help="The delivery OTP is texted here on a COD trip.")
        parser.add_argument(
            "--items", type=int, default=len(dev_samples.CATALOGUE), metavar="N",
            help="How many of the sample products to put on the order, each with a random quantity "
                 "(default: %(default)s, the whole catalogue; 0 for none; past 4 they repeat).",
        )
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

    def handle(self, *args, **options):
        driver = self._driver(options["phone"])
        vehicle = self._vehicle(driver)

        pickup_lat, pickup_lng = self._pickup(driver, options)
        drop_lat, drop_lng = self._drop(pickup_lat, pickup_lng, options)

        pickup = _point(
            pickup_lat,
            pickup_lng,
            options["pickup_address"] or f"Test pickup ({pickup_lat:.4f}, {pickup_lng:.4f})",
            "Test shop",
            "+919888800001",
        )
        drop = _point(
            drop_lat,
            drop_lng,
            options["drop_address"] or f"Test drop ({drop_lat:.4f}, {drop_lng:.4f})",
            options["customer_name"],
            options["customer_phone"],
        )

        reference = f"TEST-{int(time.time())}"
        item_count = options["items"]
        if item_count < 0:
            raise CommandError("--items can't be negative.")
        verify_items = item_count > 0 if options["verify_items"] is None else options["verify_items"]
        if verify_items and item_count == 0:
            raise CommandError("--verify-items needs at least one item: pass --items N (N ≥ 1).")
        items = dev_samples.sample_items(item_count) if item_count else None
        invoice_number = dev_samples.INVOICE_NUMBER if options["invoice"] else ""
        invoice_url = (options["invoice_url"] or dev_samples.INVOICE_URL) if options["invoice"] else ""

        try:
            trip = TripService.create_trip(
                company=driver.company,
                vehicle_type=vehicle.vehicle_type,
                pickup=pickup,
                drop=drop,
                payment_mode=PaymentMode.COD if options["mode"] == "cod" else PaymentMode.PREPAID,
                reference_id=reference,
                invoice_url=invoice_url,
                invoice_number=invoice_number,
                verify_items=verify_items,
                items=items,
            )
        except DomainError as exc:
            if exc.code == "ROUTING_UNAVAILABLE":
                raise CommandError(
                    "The routing engine isn't reachable (VALHALLA_URL), so no route could be drawn.\n"
                    "  • Quick local stand-in that works anywhere:  python scripts/dev_valhalla_stub.py\n"
                    "  • Real thing (Bengaluru map only):           docker compose up -d valhalla"
                )
            raise CommandError(f"{exc.code}: {exc.detail}")

        self._report(trip, driver, options)

    # -- inputs ------------------------------------------------------------------

    def _driver(self, phone):
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

    def _drop(self, pickup_lat, pickup_lng, options):
        if options["drop_lat"] is not None and options["drop_lng"] is not None:
            return options["drop_lat"], options["drop_lng"]
        # Due north-east: distance/√2 in each direction, converting km to degrees
        # (a degree of longitude shrinks with latitude).
        each = options["distance_km"] / (2**0.5)
        return pickup_lat + each / 111.0, pickup_lng + each / (111.0 * cos(radians(pickup_lat)))

    # -- output ------------------------------------------------------------------

    def _report(self, trip, driver, options):
        w = self.stdout.write
        w(f"Trip {trip.id}")
        w(f"  {trip.pickup_address}  →  {trip.drop_address}")
        w(f"  {trip.distance_meters / 1000:.1f} km · ₹{trip.total_fare} · {trip.get_payment_mode_display()} · status: {trip.status}")
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
        line("Arrive at the pickup, then Start (the map draws the route).")
        if trip.verify_items:
            line("At the drop, open the item checklist: tick each item Delivered (camera photo optional) "
                 "or Problem + a reason.")
            line("Before every item has an answer, payment and completion stay locked — tap them early to see that.")
        if trip.invoice_url:
            line("On the trip card, Download / WhatsApp / Share the invoice.")
        if trip.payment_mode == PaymentMode.COD:
            line("Collect the payment (QR), then enter the customer's OTP and Complete.")
            self._payment_note()
        else:
            line("Complete (prepaid: nothing to collect).")
        if trip.verify_items:
            w(f"  Afterwards the recorded delivery history (status, time, photo, note per item) is on "
              f"GET /api/v1/trips/{trip.id} with a company token.")

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
