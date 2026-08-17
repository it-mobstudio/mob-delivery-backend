from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import Company
from core.exceptions import DomainError
from drivers.models import Driver, VerificationStatus
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import services
from .models import DamageReportStatus, ReporterType, VehicleDamageReport


class DamageReportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Damage Test Co")

        self.vehicle_type = VehicleType.objects.create(
            company=self.company,
            name="Mini Van",
            category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.own_vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA04GH3456", capacity_kg=Decimal("500")
        )
        self.other_vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA04GH7890", capacity_kg=Decimal("500")
        )

        self.driver = Driver.objects.create(
            company=self.company,
            full_name="Damage Driver",
            phone_number="+919333333333",
            emergency_contact_name="EC",
            emergency_contact_phone="+919333333334",
            aadhar_status=VerificationStatus.VERIFIED,
            police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED,
            dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
            current_vehicle_id=self.own_vehicle.id,
        )

        self.admin = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", company_id=self.company.id)
        # Give the admin actor a real class name distinct from Driver so
        # services.create_damage_report's isinstance(actor, Driver) branch
        # correctly falls through to the admin path.

    def test_driver_can_report_own_assigned_vehicle(self):
        report = services.create_damage_report(
            vehicle_id=self.own_vehicle.id,
            description="Scratch on the left door",
            actor=self.driver,
            photo_url="https://x.test/damage.jpg",
            severity="medium",
        )
        self.assertEqual(report.reporter_type, ReporterType.DRIVER)
        self.assertEqual(report.reported_by_id, self.driver.id)
        self.assertEqual(report.status, DamageReportStatus.OPEN)

    def test_driver_cannot_report_unassigned_vehicle(self):
        with self.assertRaises(DomainError) as ctx:
            services.create_damage_report(
                vehicle_id=self.other_vehicle.id,
                description="Dent on the rear bumper",
                actor=self.driver,
            )
        self.assertEqual(ctx.exception.code, "NOT_YOUR_VEHICLE")

    def test_admin_can_report_any_vehicle_regardless_of_assignment(self):
        report = services.create_damage_report(
            vehicle_id=self.other_vehicle.id,
            description="Cracked windshield noted during inspection",
            actor=self.admin,
        )
        self.assertEqual(report.reporter_type, ReporterType.ADMIN)
        self.assertEqual(report.reported_by_id, self.admin.id)

    def test_resolve_sets_fields_and_rejects_double_resolve(self):
        report = services.create_damage_report(
            vehicle_id=self.own_vehicle.id, description="Broken tail light", actor=self.driver
        )
        resolved = services.resolve_damage_report(
            report_id=report.id, resolution_note="Replaced the tail light", actor=self.admin
        )
        self.assertEqual(resolved.status, DamageReportStatus.RESOLVED)
        self.assertEqual(resolved.resolved_by, self.admin.id)
        self.assertIsNotNone(resolved.resolved_at)

        with self.assertRaises(DomainError) as ctx:
            services.resolve_damage_report(report_id=report.id, resolution_note="Again", actor=self.admin)
        self.assertEqual(ctx.exception.code, "ALREADY_RESOLVED")

    def test_company_wide_list_avoids_n_plus_1_on_vehicle_registration(self):
        for i in range(3):
            VehicleDamageReport.objects.create(
                company=self.company,
                vehicle=self.own_vehicle,
                reported_by_id=self.driver.id,
                reporter_type=ReporterType.DRIVER,
                description=f"Damage {i}",
            )

        with CaptureQueriesContext(connection) as ctx:
            reports = list(VehicleDamageReport.objects.select_related("vehicle").filter(company=self.company))
            registration_numbers = [r.vehicle.registration_number for r in reports]

        self.assertEqual(len(registration_numbers), 3)
        self.assertEqual(len(ctx.captured_queries), 1, "select_related should fold the vehicle lookup into one query")
