from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.choices import VehicleCategory, VerificationStatus
from drivers.models import Driver
from drivers.services import DriverKycService


class Command(BaseCommand):
    help = (
        "Approves a driver's documents (Aadhaar, driving licence, police verification) "
        "the way a company admin would through PATCH /drivers/{id}/kyc/*, so a driver "
        "who signed up in the app can start taking trips. For local testing — in "
        "production the company reviews the uploaded scans instead.\n\n"
        "Use --reject aadhar --note 'Blurry' to try the 'fix and re-upload' path."
    )

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="The driver's number, e.g. +919555500001.")
        parser.add_argument(
            "--categories",
            nargs="+",
            choices=[c.value for c in VehicleCategory],
            default=[VehicleCategory.TWO_WHEELER.value],
            help="Vehicle categories the licence is verified for (default: two_wheeler).",
        )
        parser.add_argument(
            "--expiry-days", type=int, default=365, help="Licence valid for this many more days (default: %(default)s)."
        )
        parser.add_argument(
            "--reject",
            choices=["aadhar", "dl", "police"],
            help="Reject this document instead of approving everything.",
        )
        parser.add_argument("--note", default="Please upload a clearer photo.", help="Rejection note shown to the driver.")

    def handle(self, *args, **options):
        try:
            driver = Driver.objects.select_related("kyc").get(phone_number=options["phone"])
        except Driver.DoesNotExist:
            raise CommandError(f"No driver with phone {options['phone']}. Sign up in the app first.")

        if options["reject"]:
            self._reject(driver, options["reject"], options["note"])
            return

        # A driver the company can't have looked at yet (nothing uploaded) is worth
        # flagging: approving them is legitimate in dev, but not what a real review does.
        if not driver.kyc.aadhar_doc_url or not driver.kyc.dl_doc_url:
            self.stdout.write(self.style.WARNING("Note: this driver hasn't uploaded Aadhaar/licence scans yet."))

        expiry = timezone.localdate() + timedelta(days=options["expiry_days"])
        DriverKycService.verify_aadhar(driver, VerificationStatus.VERIFIED, admin_id=None)
        DriverKycService.verify_police(driver, VerificationStatus.VERIFIED, admin_id=None)
        DriverKycService.verify_dl(
            driver,
            VerificationStatus.VERIFIED,
            admin_id=None,
            expiry_date=expiry,
            allowed_categories=options["categories"],
        )
        driver = Driver.objects.select_related("kyc").get(pk=driver.pk)
        self.stdout.write(
            self.style.SUCCESS(
                f"Approved {driver.full_name or driver.phone_number}: licence valid to {expiry} for "
                f"{', '.join(options['categories'])}. Onboarding status: {driver.onboarding_status}."
            )
        )
        self.stdout.write("In the app, pull down to refresh (or reopen it) and the dashboard unlocks.")

    def _reject(self, driver, part, note):
        if part == "dl":
            DriverKycService.verify_dl(driver, VerificationStatus.REJECTED, admin_id=None, note=note)
        elif part == "aadhar":
            DriverKycService.verify_aadhar(driver, VerificationStatus.REJECTED, admin_id=None, note=note)
        else:
            DriverKycService.verify_police(driver, VerificationStatus.REJECTED, admin_id=None, note=note)
        driver = Driver.objects.select_related("kyc").get(pk=driver.pk)
        self.stdout.write(
            self.style.WARNING(f"Rejected {part} ({note!r}). Onboarding status: {driver.onboarding_status}.")
        )
