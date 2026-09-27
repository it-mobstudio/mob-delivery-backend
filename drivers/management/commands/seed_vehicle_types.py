from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from core.choices import VehicleTypeStatus
from drivers.models import VehicleType

# A typical Indian intra-city goods fleet, with fares in the range such
# services charge. Starting points to edit in the console (Fleet → Fares).
FLEET = [
    # name, category, capacity kg, base, per km, per min, minimum
    ("Bike", "two_wheeler", 20, 30, 8, "0.5", 40),
    ("Auto", "three_wheeler", 500, 60, 14, 1, 90),
    ("Tata Ace", "four_wheeler", 750, 150, 20, "1.5", 220),
    ("Pickup 8 ft", "four_wheeler", 1250, 250, 24, 2, 350),
    ("Truck 14 ft", "four_wheeler", 2500, 500, 32, "2.5", 750),
]


class Command(BaseCommand):
    help = (
        "Adds the usual goods vehicles (Bike, Auto, Tata Ace, Pickup 8 ft, Truck 14 ft) with sensible fare "
        "cards to a company, so the booking app offers the full range. Existing types (same name) are left "
        "exactly as they are. Default company: the one customer bookings use (BOOKING_COMPANY_ID)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", default=None, help="Company id (default: the booking company).")

    def handle(self, *args, **options):
        from accounts.models import Company
        from booking.services import BookingService

        company = Company.objects.filter(pk=options["company"]).first() if options["company"] else BookingService.company()
        if company is None:
            raise CommandError("No company: pass --company <id> or set BOOKING_COMPANY_ID.")
        for name, category, kg, base, per_km, per_min, minimum in FLEET:
            vt, created = VehicleType.objects.get_or_create(
                company=company, name=name,
                defaults=dict(category=category, default_capacity_kg=Decimal(kg), base_fare=Decimal(base),
                              per_km_rate=Decimal(per_km), per_min_rate=Decimal(per_min), min_fare=Decimal(minimum),
                              status=VehicleTypeStatus.ACTIVE),
            )
            self.stdout.write(f"  {'added ' if created else 'exists'}  {vt.name:<12} {vt.default_capacity_kg:>7} kg  "
                              f"₹{vt.base_fare} + ₹{vt.per_km_rate}/km + ₹{vt.per_min_rate}/min, min ₹{vt.min_fare}")
        self.stdout.write(self.style.SUCCESS(f"{company.name}: done. Drivers need a vehicle of a type to be offered its trips."))
