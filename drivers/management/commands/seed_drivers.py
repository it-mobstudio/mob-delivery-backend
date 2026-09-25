from datetime import timedelta
from decimal import Decimal
from random import uniform

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Company
from core.choices import VehicleCategory, VehicleStatus, VehicleTypeStatus, VerificationStatus
from drivers.models import Driver, Vehicle, VehicleType
from drivers.services import DriverKycService, DriverService
from trips.dev_samples import DRIVERS as DRIVER_NAMES

# A rough Bengaluru bounding box — random points inside it give seeded
# drivers a realistic spread, so trips.matching.MatchingService actually
# has distances to rank instead of every driver sitting on one coordinate.
BENGALURU_LAT_RANGE = (12.85, 13.05)
BENGALURU_LNG_RANGE = (77.45, 77.75)

BASE_PHONE_NUMBER = 9000000000


class Command(BaseCommand):
    help = (
        "Seeds a company with fully KYC-verified drivers, each on their own "
        "active vehicle, for local trip-matching testing. By default "
        "drivers are also put on duty with a random Bengaluru location so "
        "they're immediately assignable — see --offline to skip that. Safe "
        "to re-run: drivers already seeded (matched by phone number) are "
        "left alone."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-name", help="Company to seed into. Defaults to the only existing company, if there's just one."
        )
        parser.add_argument("--count", type=int, default=10, help="Number of drivers to create (default: 10).")
        parser.add_argument(
            "--vehicle-type",
            default="Bike",
            help="Vehicle type name to put drivers on; created with a default fare card if it doesn't exist (default: Bike).",
        )
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Leave seeded drivers verified but off duty, instead of the default online + located + assignable.",
        )

    def handle(self, *args, **options):
        company = self._get_company(options["company_name"])
        vehicle_type = self._get_or_create_vehicle_type(company, options["vehicle_type"])
        should_go_online = not options["offline"]

        seeded_phone_numbers = []
        for index in range(options["count"]):
            phone_number = self._seed_one(company, vehicle_type, index, should_go_online)
            if phone_number:
                seeded_phone_numbers.append(phone_number)

        skipped = options["count"] - len(seeded_phone_numbers)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(seeded_phone_numbers)} driver(s) into '{company.name}'"
                + (f" ({skipped} already existed, left as-is)." if skipped else ".")
            )
        )
        if seeded_phone_numbers:
            self.stdout.write("Phone numbers (OTP login — see POST /driver/auth/otp/request):")
            for phone_number in seeded_phone_numbers:
                self.stdout.write(f"  {phone_number}")

    def _get_company(self, company_name):
        if company_name:
            try:
                return Company.objects.get(name=company_name)
            except Company.DoesNotExist:
                raise CommandError(f"Company '{company_name}' does not exist. Create it with bootstrap_company first.")

        companies = list(Company.objects.all()[:2])
        if not companies:
            raise CommandError("No companies exist yet. Create one with bootstrap_company first.")
        if len(companies) > 1:
            raise CommandError("Multiple companies exist — pass --company-name to pick one.")
        return companies[0]

    def _get_or_create_vehicle_type(self, company, name):
        vehicle_type, created = VehicleType.objects.get_or_create(
            company=company,
            name=name,
            defaults={
                "category": VehicleCategory.TWO_WHEELER,
                "default_capacity_kg": 20,
                "status": VehicleTypeStatus.ACTIVE,
                "base_fare": 30,
                "per_km_rate": 10,
                "per_min_rate": 1,
                "min_fare": 40,
            },
        )
        if created:
            self.stdout.write(f"Created vehicle type '{vehicle_type.name}' with a default fare card.")
        return vehicle_type

    def _seed_one(self, company, vehicle_type, index, should_go_online):
        phone_number = f"+91{BASE_PHONE_NUMBER + index}"
        if Driver.objects.filter(company=company, phone_number=phone_number).exists():
            return None

        with transaction.atomic():
            driver = DriverService.create(
                company,
                full_name=DRIVER_NAMES[index % len(DRIVER_NAMES)],
                phone_number=phone_number,
                emergency_contact_name="Sunita Devi",
                emergency_contact_phone="+919999900000",
            )
            DriverKycService.verify_aadhar(driver, VerificationStatus.VERIFIED, admin_id=None)
            DriverKycService.verify_police(driver, VerificationStatus.VERIFIED, admin_id=None)
            DriverKycService.verify_dl(
                driver,
                VerificationStatus.VERIFIED,
                admin_id=None,
                expiry_date=timezone.localdate() + timedelta(days=365),
                allowed_categories=[vehicle_type.category],
            )

            vehicle = Vehicle.objects.create(
                company=company,
                vehicle_type=vehicle_type,
                registration_number=f"KA01SEED{index:04d}",
                capacity_kg=vehicle_type.default_capacity_kg,
                status=VehicleStatus.ACTIVE,
            )

            if should_go_online:
                DriverService.go_online(driver, vehicle)
                DriverService.update_location(
                    driver,
                    Decimal(str(round(uniform(*BENGALURU_LAT_RANGE), 6))),
                    Decimal(str(round(uniform(*BENGALURU_LNG_RANGE), 6))),
                )

        return phone_number
