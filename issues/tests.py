from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AdminUser, ApiClient, Company
from core.exceptions import DomainError
from drivers.models import Driver, VerificationStatus
from trips import services as trip_services
from vehicles.models import Vehicle, VehicleCategory, VehicleType

from . import services
from .models import IssueStatus, TripIssue


def _addr(address="Address", lat="12.900000", lng="77.600000"):
    return {"address": address, "latitude": Decimal(lat), "longitude": Decimal(lng)}


class IssueTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Issues Test Co")
        self.actor = SimpleNamespace(company_id=self.company.id)

        self.vehicle_type = VehicleType.objects.create(
            company=self.company, name="Mini Van", category=VehicleCategory.FOUR_WHEELER,
            default_capacity_kg=Decimal("500.00"),
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, vehicle_type=self.vehicle_type, registration_number="KA10EE0001",
            capacity_kg=Decimal("500"),
        )
        self.driver = Driver.objects.create(
            company=self.company, full_name="Issue Driver", phone_number="+919900000001",
            emergency_contact_name="EC", emergency_contact_phone="+919900000002",
            aadhar_status=VerificationStatus.VERIFIED, police_status=VerificationStatus.VERIFIED,
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date=date.today() + timedelta(days=300),
            dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
        )
        self.admin = AdminUser.objects.create_user(
            email="issuesadmin@test.invalid", company=self.company, password="pass12345"
        )

        result = trip_services.intake_order(
            company_id=self.company.id, order_ref="ORD-ISSUE-BASE", parent_order_ref=None,
            pickup=_addr("Pickup"), delivery=_addr("Delivery"), weight_kg=Decimal("5.00"), actor=self.actor,
        )
        trip_services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url="https://x.test/p.jpg", actor=self.actor)
        trip_services.lock_pickups(trip_id=result["trip_id"], actor=self.actor)
        self.trip = trip_services.assign_vehicle(
            trip_id=result["trip_id"], vehicle_id=self.vehicle.id, driver_id=self.driver.id, actor=self.actor
        )
        self.pickup_stop_id = result["pickup_stop_id"]


class CreateIssueServiceTests(IssueTestBase):
    def test_driver_can_create_issue(self):
        issue = services.create_issue(
            trip_id=self.trip.id, issue_type="transit", note="Traffic jam on the highway", actor=self.driver
        )
        self.assertEqual(issue.trip_id, self.trip.id)
        self.assertEqual(issue.status, IssueStatus.OPEN)
        self.assertEqual(issue.severity, "medium")

    def test_admin_can_create_issue(self):
        issue = services.create_issue(
            trip_id=self.trip.id, issue_type="traffic_penalty", note="Fined for wrong parking",
            actor=self.admin, severity="high",
        )
        self.assertEqual(issue.severity, "high")

    def test_issue_can_link_a_stop_on_the_same_trip(self):
        issue = services.create_issue(
            trip_id=self.trip.id, issue_type="pickup", note="Pickup address was wrong",
            actor=self.driver, trip_stop_id=self.pickup_stop_id,
        )
        self.assertEqual(issue.trip_stop_id, self.pickup_stop_id)

    def test_stop_from_a_different_trip_is_rejected(self):
        other = trip_services.intake_order(
            company_id=self.company.id, order_ref="ORD-ISSUE-OTHER", parent_order_ref=None,
            pickup=_addr("P2"), delivery=_addr("D2"), weight_kg=Decimal("2.00"), actor=self.actor,
        )
        with self.assertRaises(DomainError) as ctx:
            services.create_issue(
                trip_id=self.trip.id, issue_type="pickup", note="Mismatched stop",
                actor=self.driver, trip_stop_id=other["pickup_stop_id"],
            )
        self.assertEqual(ctx.exception.code, "TRIP_STOP_NOT_FOUND")


class ResolveIssueServiceTests(IssueTestBase):
    def test_resolve_sets_fields_and_rejects_double_resolve(self):
        issue = services.create_issue(
            trip_id=self.trip.id, issue_type="unloading", note="Damaged package at drop", actor=self.driver
        )
        resolved = services.resolve_issue(issue_id=issue.id, resolution_note="Compensated customer", actor=self.admin)
        self.assertEqual(resolved.status, IssueStatus.RESOLVED)
        self.assertEqual(resolved.resolved_by, self.admin.id)
        self.assertIsNotNone(resolved.resolved_at)

        with self.assertRaises(DomainError) as ctx:
            services.resolve_issue(issue_id=issue.id, resolution_note="Again", actor=self.admin)
        self.assertEqual(ctx.exception.code, "ISSUE_ALREADY_RESOLVED")


class IssuePermissionHttpTests(IssueTestBase):
    def setUp(self):
        super().setUp()
        self.api_client_principal = ApiClient(company=self.company, name="Partner Integration")
        self.api_client_principal.set_secret("dummy-secret")
        self.api_client_principal.save()

    def test_driver_create_via_http_succeeds(self):
        client = APIClient()
        client.force_authenticate(user=self.driver)
        r = client.post(f"/api/v1/trips/{self.trip.id}/issues", {
            "issue_type": "transit", "note": "Vehicle broke down briefly",
        }, format="json")
        self.assertEqual(r.status_code, 201)

    def test_admin_create_via_http_succeeds(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.post(f"/api/v1/trips/{self.trip.id}/issues", {
            "issue_type": "pickup", "note": "Reported by phone call",
        }, format="json")
        self.assertEqual(r.status_code, 201)

    def test_api_client_create_via_http_is_rejected(self):
        client = APIClient()
        client.force_authenticate(user=self.api_client_principal)
        r = client.post(f"/api/v1/trips/{self.trip.id}/issues", {
            "issue_type": "transit", "note": "Should not be allowed",
        }, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(TripIssue.objects.count(), 0)

    def test_driver_cannot_resolve_admin_can(self):
        issue = services.create_issue(
            trip_id=self.trip.id, issue_type="transit", note="Needs resolving", actor=self.driver
        )

        driver_client = APIClient()
        driver_client.force_authenticate(user=self.driver)
        r1 = driver_client.post(f"/api/v1/issues/{issue.id}/resolve", {"resolution_note": "n/a"}, format="json")
        self.assertEqual(r1.status_code, 403)

        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        r2 = admin_client.post(f"/api/v1/issues/{issue.id}/resolve", {"resolution_note": "Fixed it"}, format="json")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["status"], "resolved")
