import secrets
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import AdminRole, AdminUser, ApiClient, Company
from damage_reports.models import DamageReportStatus, DamageSeverity, ReporterType, VehicleDamageReport
from drivers.models import Driver, DriverAccountStatus, VerificationStatus
from issues import services as issue_services
from issues.models import IssueSeverity, IssueType, TripIssue
from sos.models import SosAlert, SosStatus
from tenant_settings.services import seed_default_settings
from tracking.models import DriverShift, LocationSource, PauseReason, ShiftStatus, TripLocationPing, TripPause
from trips import services as trip_services
from trips.models import Trip, TripPhoto, TripPhotoType, TripStatus, TripStop
from vehicles.models import (
    DimensionUnit,
    Vehicle,
    VehicleCategory,
    VehicleDocument,
    VehicleDocumentType,
    VehicleStatus,
    VehicleType,
    VehicleTypeStatus,
)

COMPANY_NAME = "Mad Over Buildings"
ADMIN_EMAIL = "ops@madoverbuildings.com"
API_CLIENT_NAME = "Django E-commerce Integration"

# A real, lightweight placeholder-image service — these URLs actually
# resolve to a photo in a browser, which matters for a live demo ("enough
# to actually look at"), unlike a dead example.com link.
PHOTO_BASE = "https://picsum.photos/seed"


def _photo(seed, w=800, h=600):
    return f"{PHOTO_BASE}/{seed}/{w}/{h}"


def _backdate(queryset, dt):
    """created_at/updated_at are auto_now_add/auto_now — a plain .create()
    call can never backdate them (Django overwrites both at save time no
    matter what you pass in). A queryset-level .update() goes straight to
    SQL and bypasses that, which is the only way to spread demo timestamps
    across the last several days instead of everything landing at "now".
    """
    queryset.update(created_at=dt, updated_at=dt)


class Command(BaseCommand):
    help = "Seeds realistic demo data (one company, drivers, vehicles, trips, issues, ...) for the Admin Panel demo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Delete the previously-seeded demo company (and everything under it) first.",
        )
        parser.add_argument("--force", action="store_true", help="Allow running even when DEBUG=False.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed demo data with DEBUG=False — this looks like a non-dev environment. "
                "Pass --force if you really mean it."
            )

        if options["reset"]:
            self._reset()

        if Company.all_objects.filter(name=COMPANY_NAME).exists():
            self.stdout.write(self.style.WARNING(
                f"'{COMPANY_NAME}' already exists — demo data was already seeded. "
                "Nothing to do (admin/API-client credentials were only ever shown once, at creation). "
                "Pass --reset to wipe it and start over."
            ))
            return

        self._run()

    def _reset(self):
        existing = Company.all_objects.filter(name=COMPANY_NAME).first()
        if existing is None:
            self.stdout.write("No existing demo company found — nothing to reset.")
            return

        # Every company-scoped model CASCADEs from Company (core.BaseModel),
        # so in principle one hard delete on Company should take everything
        # with it. In practice Django's deletion collector checks each
        # on_delete=PROTECT relation (Trip.vehicle/driver, DriverShift.
        # vehicle/driver, SosAlert.driver, Vehicle.vehicle_type) as it's
        # encountered, before necessarily having collected that same row via
        # its own Company->CASCADE path yet — so a single cascading delete
        # from Company raises ProtectedError even though every one of these
        # rows would themselves be deleted a moment later. Deleting in
        # explicit dependency order (things that PROTECT a relation, before
        # the thing they protect) sidesteps that. .hard_delete() (not
        # .delete(), which is soft-delete-overridden — see
        # core.models.SoftDeleteQuerySet) is what actually removes rows.
        from damage_reports.models import VehicleDamageReport
        from drivers.models import Driver
        from issues.models import TripIssue
        from sos.models import SosAlert
        from tracking.models import DriverShift, TripLocationPing
        from trips.models import Trip
        from vehicles.models import Vehicle, VehicleType

        company = existing
        TripLocationPing.objects.filter(company_id=company.id).delete()  # plain Model, real delete either way
        SosAlert.all_objects.filter(company=company).hard_delete()
        DriverShift.all_objects.filter(company=company).hard_delete()
        VehicleDamageReport.all_objects.filter(company=company).hard_delete()
        TripIssue.all_objects.filter(company=company).hard_delete()
        Trip.all_objects.filter(company=company).hard_delete()  # cascades TripStop/TripPhoto/TripPause/etc
        Vehicle.all_objects.filter(company=company).hard_delete()  # cascades VehicleDocument/expiry alerts
        Driver.all_objects.filter(company=company).hard_delete()
        VehicleType.all_objects.filter(company=company).hard_delete()
        Company.all_objects.filter(pk=company.pk).hard_delete()  # cascades AdminUser/ApiClient/TenantSetting/...

        self.stdout.write(self.style.WARNING(f"Deleted demo company '{COMPANY_NAME}' and everything under it."))

    def _run(self):
        self.stdout.write("Seeding demo data for 'Mad Over Buildings'...\n")

        company = self._seed_company_and_admin()
        self.actor = SimpleNamespace(company_id=company.id)

        vehicle_types = self._seed_vehicle_types(company)
        vehicles = self._seed_vehicles(company, vehicle_types)
        drivers = self._seed_drivers(company)
        self._seed_shifts(company, drivers, vehicles)
        self._seed_trips(company, vehicles, drivers)
        self._seed_extra_issues()
        self._seed_damage_reports(company, vehicles, drivers)
        self._seed_sos(company, drivers)

        self.stdout.write(self.style.SUCCESS("\nDemo data seeded successfully."))
        self.stdout.write(
            "\nNote: the 'fixed morning start point' feature (Vehicle Start Points) was removed from "
            "this codebase earlier — section 11 of the seed spec is intentionally skipped, not forgotten."
        )

    # ------------------------------------------------------------------
    # 1. Company + Admin + API client
    # ------------------------------------------------------------------

    def _seed_company_and_admin(self):
        company = Company.objects.create(name=COMPANY_NAME)
        seed_default_settings(company)  # section 10 — the six defaults established earlier

        admin_password = secrets.token_urlsafe(12)
        self._admin = AdminUser.objects.create_user(
            email=ADMIN_EMAIL, company=company, password=admin_password,
            first_name="Ops", last_name="Admin", role=AdminRole.OWNER, is_staff=True,
        )

        raw_secret = secrets.token_urlsafe(32)
        api_client = ApiClient(company=company, name=API_CLIENT_NAME)
        api_client.set_secret(raw_secret)
        api_client.save()

        self.stdout.write(self.style.SUCCESS(f"Company: {company.name} ({company.id})"))
        self.stdout.write(self.style.SUCCESS(f"Admin login — email: {self._admin.email}  password: {admin_password}"))
        self.stdout.write(self.style.SUCCESS(
            f"API client '{API_CLIENT_NAME}' — client_id: {api_client.client_id}  secret: {raw_secret}"
        ))
        self.stdout.write(self.style.WARNING("(both shown once — store them now)\n"))
        return company

    # ------------------------------------------------------------------
    # 2. Vehicle types
    # ------------------------------------------------------------------

    def _seed_vehicle_types(self, company):
        specs = [
            ("Tata Ace", VehicleCategory.FOUR_WHEELER, "750.00", ("7", "4", "5", DimensionUnit.FEET)),
            ("3 Wheeler", VehicleCategory.THREE_WHEELER, "500.00", ("5.5", "4.5", "5", DimensionUnit.FEET)),
            ("2 Wheeler", VehicleCategory.TWO_WHEELER, "20.00", ("40", "40", "40", DimensionUnit.CM)),
            ("Eicher 14ft", VehicleCategory.FOUR_WHEELER, "2000.00", ("14", "6", "6", DimensionUnit.FEET)),
        ]
        types = {}
        for name, category, capacity, (length, width, height, unit) in specs:
            types[name] = VehicleType.objects.create(
                company=company, name=name, category=category, default_capacity_kg=Decimal(capacity),
                icon_image_url=_photo(f"vehicle-type-{name.lower().replace(' ', '-')}", 400, 400),
                status=VehicleTypeStatus.ACTIVE,
                storage_length=Decimal(length), storage_width=Decimal(width), storage_height=Decimal(height),
                storage_unit=unit,
            )
        self.stdout.write(f"Vehicle types: {', '.join(types)}")
        return types

    # ------------------------------------------------------------------
    # 3. Vehicles + documents
    # ------------------------------------------------------------------

    def _seed_vehicles(self, company, vehicle_types):
        today = timezone.localdate()
        specs = [
            ("tata_ace_1", "KA-01-AB-1234", "Tata Ace", VehicleStatus.ACTIVE),
            ("tata_ace_2", "KA-01-AB-1235", "Tata Ace", VehicleStatus.ACTIVE),
            ("three_wheeler_1", "KA-02-CD-5678", "3 Wheeler", VehicleStatus.ACTIVE),
            ("three_wheeler_2", "KA-02-CD-5679", "3 Wheeler", VehicleStatus.MAINTENANCE),
            ("two_wheeler_1", "KA-03-EF-9012", "2 Wheeler", VehicleStatus.ACTIVE),
            ("two_wheeler_2", "KA-03-EF-9013", "2 Wheeler", VehicleStatus.ACTIVE),
            # Discontinued — status=disabled but NOT soft-deleted, so it
            # still shows up in the vehicle list to demo the "disabled"
            # state itself. Vehicle has no dedicated discontinuation-reason
            # field, so that context only lives here in the seed script.
            ("eicher_1", "KA-04-GH-3456", "Eicher 14ft", VehicleStatus.DISABLED),
        ]
        vehicles = {}
        for key, reg, type_name, status in specs:
            vt = vehicle_types[type_name]
            vehicles[key] = Vehicle.objects.create(
                company=company, vehicle_type=vt, registration_number=reg, capacity_kg=vt.default_capacity_kg,
                photo_url=_photo(f"vehicle-{reg.lower()}"), status=status,
            )

        def _doc(vehicle, doc_type, expiry_date=None):
            VehicleDocument.objects.create(
                company=company, vehicle=vehicle, document_type=doc_type,
                file_url=_photo(f"vehicledoc-{vehicle.registration_number.lower()}-{doc_type}"),
                expiry_date=expiry_date,
            )

        _doc(vehicles["tata_ace_1"], VehicleDocumentType.INSURANCE, today + timedelta(days=10))  # approaching expiry
        _doc(vehicles["tata_ace_1"], VehicleDocumentType.FITNESS, today + timedelta(days=200))
        _doc(vehicles["three_wheeler_1"], VehicleDocumentType.INSURANCE, today - timedelta(days=15))  # already expired
        _doc(vehicles["three_wheeler_1"], VehicleDocumentType.FITNESS, today + timedelta(days=100))
        _doc(vehicles["two_wheeler_1"], VehicleDocumentType.RC)
        _doc(vehicles["eicher_1"], VehicleDocumentType.PURCHASE)

        self.stdout.write(f"Vehicles: {len(vehicles)} (1 maintenance, 1 disabled)")
        return vehicles

    # ------------------------------------------------------------------
    # 4. Drivers
    # ------------------------------------------------------------------

    def _seed_drivers(self, company):
        today = timezone.localdate()

        def _doc_urls(prefix):
            return {
                "aadhar_doc_url": _photo(f"aadhar-{prefix}"),
                "dl_doc_url": _photo(f"dl-{prefix}"),
                "police_doc_url": _photo(f"police-{prefix}"),
            }

        drivers = {}

        # 4 fully verified, eligible.
        verified_specs = [
            ("ravi", "Ravi Kumar", "+919845011223", [VehicleCategory.FOUR_WHEELER]),
            ("suresh", "Suresh Reddy", "+919845011224", [VehicleCategory.THREE_WHEELER]),
            ("manjunath", "Manjunath Gowda", "+919845011225", [VehicleCategory.TWO_WHEELER]),
            ("anitha", "Anitha Sharma", "+919845011226", [VehicleCategory.FOUR_WHEELER, VehicleCategory.THREE_WHEELER]),
        ]
        for key, name, phone, categories in verified_specs:
            drivers[key] = Driver.objects.create(
                company=company, full_name=name, phone_number=phone,
                emergency_contact_name=f"{name.split()[0]}'s Family", emergency_contact_phone="+919845099" + phone[-3:],
                **_doc_urls(key),
                aadhar_status=VerificationStatus.VERIFIED, aadhar_verified_at=timezone.now(),
                dl_status=VerificationStatus.VERIFIED, dl_verified_at=timezone.now(),
                dl_expiry_date=today + timedelta(days=540), dl_allowed_categories=categories,
                police_status=VerificationStatus.VERIFIED, police_verified_at=timezone.now(),
                account_status=DriverAccountStatus.ACTIVE,
            )

        # 8th "fully verified, eligible, currently unassigned" driver —
        # demonstrates the same eligible state a fifth time, deliberately
        # kept idle so the drivers list has a clearly-available row.
        drivers["kiran"] = Driver.objects.create(
            company=company, full_name="Kiran Babu", phone_number="+919845011230",
            emergency_contact_name="Kiran's Family", emergency_contact_phone="+919845099230",
            **_doc_urls("kiran"),
            aadhar_status=VerificationStatus.VERIFIED, aadhar_verified_at=timezone.now(),
            dl_status=VerificationStatus.VERIFIED, dl_verified_at=timezone.now(),
            dl_expiry_date=today + timedelta(days=400), dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
            police_status=VerificationStatus.VERIFIED, police_verified_at=timezone.now(),
            account_status=DriverAccountStatus.ACTIVE,
        )

        # 1 pending — freshly onboarded, docs uploaded, nothing reviewed yet.
        drivers["deepak"] = Driver.objects.create(
            company=company, full_name="Deepak Nair", phone_number="+919845011227",
            emergency_contact_name="Deepak's Family", emergency_contact_phone="+919845099227",
            **_doc_urls("deepak"),
        )

        # 1 with Aadhar rejected — DL/police verified, Aadhar bounced back.
        drivers["prakash"] = Driver.objects.create(
            company=company, full_name="Prakash Yadav", phone_number="+919845011228",
            emergency_contact_name="Prakash's Family", emergency_contact_phone="+919845099228",
            **_doc_urls("prakash"),
            aadhar_status=VerificationStatus.REJECTED,
            aadhar_rejection_note="Document image unclear, please re-upload.", aadhar_verified_at=timezone.now(),
            dl_status=VerificationStatus.VERIFIED, dl_verified_at=timezone.now(),
            dl_expiry_date=today + timedelta(days=300), dl_allowed_categories=[VehicleCategory.THREE_WHEELER],
            police_status=VerificationStatus.VERIFIED, police_verified_at=timezone.now(),
        )

        # 1 with an expired DL — locked, exactly as the daily Celery task
        # would have left it (seeded directly rather than waiting for that
        # task to run against demo data).
        drivers["vijay"] = Driver.objects.create(
            company=company, full_name="Vijay Kumar", phone_number="+919845011229",
            emergency_contact_name="Vijay's Family", emergency_contact_phone="+919845099229",
            **_doc_urls("vijay"),
            aadhar_status=VerificationStatus.VERIFIED, aadhar_verified_at=timezone.now(),
            dl_status=VerificationStatus.VERIFIED, dl_verified_at=timezone.now(),
            dl_expiry_date=today - timedelta(days=20), dl_allowed_categories=[VehicleCategory.FOUR_WHEELER],
            police_status=VerificationStatus.VERIFIED, police_verified_at=timezone.now(),
            account_status=DriverAccountStatus.LOCKED_DL_EXPIRED,
        )

        self.stdout.write(f"Drivers: {len(drivers)} (4 verified, 1 pending, 1 rejected-doc, 1 DL-locked, 1 idle-verified)")
        return drivers

    # ------------------------------------------------------------------
    # 5. Driver shifts
    # ------------------------------------------------------------------

    def _seed_shifts(self, company, drivers, vehicles):
        now = timezone.now()

        DriverShift.objects.create(
            company=company, driver=drivers["suresh"], vehicle=vehicles["three_wheeler_1"],
            shift_date=timezone.localdate(), start_odometer=Decimal("15000.00"),
            started_at=now - timedelta(hours=3), status=ShiftStatus.ACTIVE,
        )

        ended_specs = [
            (drivers["manjunath"], vehicles["two_wheeler_2"], 2, "8000.00", "8045.00", 45, 360),
            (drivers["anitha"], vehicles["tata_ace_2"], 3, "22000.00", "22180.00", 180, 480),
        ]
        for driver, vehicle, days_ago, start_odo, end_odo, total_km, total_minutes in ended_specs:
            started_at = now - timedelta(days=days_ago, hours=9)
            ended_at = started_at + timedelta(minutes=total_minutes)
            shift = DriverShift.objects.create(
                company=company, driver=driver, vehicle=vehicle, shift_date=(now - timedelta(days=days_ago)).date(),
                start_odometer=Decimal(start_odo), end_odometer=Decimal(end_odo),
                started_at=started_at, ended_at=ended_at, status=ShiftStatus.ENDED,
                total_km=Decimal(str(total_km)), total_working_minutes=total_minutes,
                cleanliness_photo_url=_photo(f"shift-clean-{driver.phone_number[-4:]}"),
                charging_plugged_photo_url=_photo(f"shift-charge-{driver.phone_number[-4:]}"),
            )
            _backdate(DriverShift.objects.filter(pk=shift.pk), started_at)

        self.stdout.write("Driver shifts: 1 active, 2 ended")

    # ------------------------------------------------------------------
    # 6. Trips
    # ------------------------------------------------------------------

    def _assign_directly(self, trip, vehicle, driver, status, started_at):
        """Sets vehicle/driver/status/started_at without going through
        trips.services.assign_vehicle — that service pushes a notification
        via Celery .delay(), which needs a live broker this seed script
        can't assume exists. Same effect on the rows that matter, minus the
        side effect.
        """
        trip.vehicle = vehicle
        trip.driver = driver
        trip.status = status
        trip.started_at = started_at
        trip.save(update_fields=["vehicle", "driver", "status", "started_at"])
        vehicle.current_driver_id = driver.id
        vehicle.save(update_fields=["current_driver_id"])
        driver.current_vehicle_id = vehicle.id
        driver.save(update_fields=["current_vehicle_id"])

    def _seed_trips(self, company, vehicles, drivers):
        actor = self.actor
        now = timezone.now()

        # -- The multi-vendor staggered-acceptance trip — the core differentiator. --
        parent_ref = "DEMO-PARENT-HSR-1"
        delivery = {
            "address": "HSR Layout, Sector 2, Bangalore",
            "latitude": Decimal("12.912100"), "longitude": Decimal("77.644600"),
        }
        vendor_pickups = [
            ("DEMO-PARENT-HSR-1-V1", "Vendor 1 · Koramangala", "12.935242", "77.624600", "3.50", True),
            ("DEMO-PARENT-HSR-1-V2", "Vendor 2 · Indiranagar", "12.978400", "77.640800", "5.20", True),
            ("DEMO-PARENT-HSR-1-V3", "Vendor 3 · Domlur", "12.961000", "77.638700", "2.80", False),
        ]
        staggered_trip_id = None
        for order_ref, vendor_label, lat, lng, weight, completed in vendor_pickups:
            result = trip_services.intake_order(
                company_id=company.id, order_ref=order_ref, parent_order_ref=parent_ref,
                pickup={"address": vendor_label, "latitude": Decimal(lat), "longitude": Decimal(lng)},
                delivery=delivery, weight_kg=Decimal(weight), actor=actor,
            )
            staggered_trip_id = result["trip_id"]
            if completed:
                trip_services.complete_stop(
                    stop_id=result["pickup_stop_id"], proof_photo_url=_photo(f"pickup-{order_ref.lower()}"), actor=actor
                )
        staggered_started = now - timedelta(hours=1, minutes=45)
        _backdate(Trip.objects.filter(pk=staggered_trip_id), staggered_started)
        _backdate(TripStop.objects.filter(trip_id=staggered_trip_id), staggered_started)
        _backdate(TripPhoto.objects.filter(trip_stop__trip_id=staggered_trip_id), staggered_started + timedelta(minutes=20))

        # -- pickups_locked / in_transit, single pickup/drop, with a live-ish route. --
        result = trip_services.intake_order(
            company_id=company.id, order_ref="DEMO-INTRANSIT-1", parent_order_ref=None,
            pickup={"address": "Corporate Park, Marathahalli", "latitude": Decimal("12.959100"), "longitude": Decimal("77.697400")},
            delivery={"address": "Prestige Tech Park, Sarjapur Road", "latitude": Decimal("12.928000"), "longitude": Decimal("77.687000")},
            weight_kg=Decimal("45.00"), actor=actor,
        )
        trip_services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url=_photo("pickup-intransit-1"), actor=actor)
        trip_services.lock_pickups(trip_id=result["trip_id"], actor=actor)
        in_transit_trip = Trip.objects.get(pk=result["trip_id"])
        in_transit_started = now - timedelta(minutes=35)
        self._assign_directly(in_transit_trip, vehicles["tata_ace_1"], drivers["ravi"], TripStatus.IN_TRANSIT, in_transit_started)

        route = [
            (Decimal("12.959100"), Decimal("77.697400"), Decimal("0")),
            (Decimal("12.952000"), Decimal("77.690000"), Decimal("1.900")),
            (Decimal("12.945000"), Decimal("77.683000"), Decimal("1.750")),
            (Decimal("12.938000"), Decimal("77.679000"), Decimal("1.400")),
            (Decimal("12.932000"), Decimal("77.685000"), Decimal("1.650")),
        ]
        for i, (lat, lng, dist) in enumerate(route):
            TripLocationPing.objects.create(
                company_id=company.id, trip=in_transit_trip, vehicle_id=vehicles["tata_ace_1"].id,
                latitude=lat, longitude=lng, speed_kmph=Decimal("27.50"), source=LocationSource.DRIVER_PHONE,
                recorded_at=in_transit_started + timedelta(minutes=i * 7), distance_from_previous_km=dist,
            )
        _backdate(Trip.objects.filter(pk=in_transit_trip.pk), in_transit_started)
        _backdate(TripStop.objects.filter(trip=in_transit_trip), in_transit_started)
        _backdate(TripPhoto.objects.filter(trip_stop__trip=in_transit_trip), in_transit_started + timedelta(minutes=5))

        # -- 3 delivered trips, spread across the last few days. --
        delivered_specs = [
            ("DEMO-DELIVERED-1", "Koramangala 5th Block", "12.935242", "77.624600",
             "Jayanagar 4th Block", "12.925700", "77.583100", "12.50",
             vehicles["three_wheeler_1"], drivers["suresh"], 1),
            ("DEMO-DELIVERED-2", "Indiranagar 100ft Road", "12.978400", "77.640800",
             "Domlur Bridge", "12.961000", "77.638700", "3.20",
             vehicles["two_wheeler_2"], drivers["manjunath"], 3),
            ("DEMO-DELIVERED-3", "Whitefield ITPL Main Road", "12.969800", "77.749800",
             "Marathahalli Bridge", "12.959100", "77.697400", "310.00",
             vehicles["tata_ace_2"], drivers["anitha"], 4),
        ]
        delivered_trips = []
        for order_ref, p_addr, p_lat, p_lng, d_addr, d_lat, d_lng, weight, vehicle, driver, days_ago in delivered_specs:
            result = trip_services.intake_order(
                company_id=company.id, order_ref=order_ref, parent_order_ref=None,
                pickup={"address": p_addr, "latitude": Decimal(p_lat), "longitude": Decimal(p_lng)},
                delivery={"address": d_addr, "latitude": Decimal(d_lat), "longitude": Decimal(d_lng)},
                weight_kg=Decimal(weight), actor=actor,
            )
            trip_services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url=_photo(f"pickup-{order_ref.lower()}"), actor=actor)
            trip_services.lock_pickups(trip_id=result["trip_id"], actor=actor)
            trip = Trip.objects.get(pk=result["trip_id"])
            started_at = now - timedelta(days=days_ago, hours=4)
            self._assign_directly(trip, vehicle, driver, TripStatus.IN_TRANSIT, started_at)

            trip_services.add_trip_stop_photo(
                stop_id=result["pickup_stop_id"], photo_type=TripPhotoType.LOADED_VEHICLE,
                photo_url=_photo(f"loaded-{order_ref.lower()}"), actor=actor,
            )
            trip_services.complete_stop(stop_id=result["drop_stop_id"], proof_photo_url=_photo(f"delivered-{order_ref.lower()}"), actor=actor)
            trip.refresh_from_db()

            # complete_stop() stamps completed_at as the real "now" — backdate
            # it too, not just created_at/updated_at, or every "delivered"
            # trip would show today's date regardless of days_ago.
            completed_at = started_at + timedelta(hours=2, minutes=30)
            Trip.objects.filter(pk=trip.pk).update(
                created_at=started_at, updated_at=completed_at, completed_at=completed_at
            )
            _backdate(TripStop.objects.filter(trip=trip), started_at)
            _backdate(TripPhoto.objects.filter(trip_stop__trip=trip), started_at + timedelta(hours=1))
            trip.refresh_from_db()
            delivered_trips.append((trip, result))

        # A couple of lunch/recharge pauses on two of the delivered trips.
        for trip, reason in [(delivered_trips[0][0], PauseReason.LUNCH), (delivered_trips[2][0], PauseReason.RECHARGE)]:
            pause_start = trip.started_at + timedelta(hours=1)
            pause = TripPause.objects.create(
                company=company, trip=trip, reason=reason,
                started_at=pause_start, ended_at=pause_start + timedelta(minutes=25),
            )
            _backdate(TripPause.objects.filter(pk=pause.pk), pause_start)

        # -- 1 cancelled trip — goods picked up, not delivered, protection-rule TripIssue attached. --
        result = trip_services.intake_order(
            company_id=company.id, order_ref="DEMO-CANCELLED-1", parent_order_ref=None,
            pickup={"address": "BTM Layout 2nd Stage", "latitude": Decimal("12.916600"), "longitude": Decimal("77.610000")},
            delivery={"address": "Silk Board Junction", "latitude": Decimal("12.917300"), "longitude": Decimal("77.622800")},
            weight_kg=Decimal("18.00"), actor=actor,
        )
        trip_services.complete_stop(stop_id=result["pickup_stop_id"], proof_photo_url=_photo("pickup-cancelled-1"), actor=actor)
        trip_services.lock_pickups(trip_id=result["trip_id"], actor=actor)
        cancelled_trip = Trip.objects.get(pk=result["trip_id"])
        cancelled_started = now - timedelta(hours=5)
        self._assign_directly(cancelled_trip, vehicles["eicher_1"], drivers["kiran"], TripStatus.IN_TRANSIT, cancelled_started)
        trip_services.cancel_trip(
            trip_id=cancelled_trip.id,
            reason="Vehicle broke down en route — goods handed to another vendor for redelivery.",
            actor=actor,
        )
        cancelled_trip.refresh_from_db()

        _backdate(Trip.objects.filter(pk=cancelled_trip.pk), cancelled_started)
        _backdate(TripStop.objects.filter(trip=cancelled_trip), cancelled_started)
        _backdate(TripPhoto.objects.filter(trip_stop__trip=cancelled_trip), cancelled_started + timedelta(minutes=15))
        _backdate(TripIssue.objects.filter(trip=cancelled_trip), cancelled_started + timedelta(hours=2))

        # Stashed for _seed_extra_issues, which runs after this method.
        self._delivered_trips = delivered_trips

        self.stdout.write("Trips: 1 staggered multi-vendor, 1 in-transit, 3 delivered, 1 cancelled")

    # ------------------------------------------------------------------
    # 7. Issues (beyond the auto-created one from the cancellation above)
    # ------------------------------------------------------------------

    def _seed_extra_issues(self):
        actor = self.actor
        now = timezone.now()

        pickup_trip, pickup_result = self._delivered_trips[0]
        pickup_issue = issue_services.create_issue(
            trip_id=pickup_trip.id, issue_type=IssueType.PICKUP,
            note="Vendor's shop was hard to locate — called for directions before pickup.",
            actor=actor, trip_stop_id=pickup_result["pickup_stop_id"], severity=IssueSeverity.LOW,
        )
        _backdate(TripIssue.objects.filter(pk=pickup_issue.pk), now - timedelta(days=1, hours=3))

        penalty_trip, _ = self._delivered_trips[2]
        penalty_issue = issue_services.create_issue(
            trip_id=penalty_trip.id, issue_type=IssueType.TRAFFIC_PENALTY,
            note="Fined for a brief no-parking stop near the customer's gate.",
            actor=actor, severity=IssueSeverity.MEDIUM,
            penalty_amount=Decimal("500.00"), penalty_challan_number="KA-CH-00231",
        )
        _backdate(TripIssue.objects.filter(pk=penalty_issue.pk), now - timedelta(days=4, hours=2))

        unloading_trip, _ = self._delivered_trips[1]
        unloading_issue = issue_services.create_issue(
            trip_id=unloading_trip.id, issue_type=IssueType.UNLOADING,
            note="One box arrived with a crushed corner.", actor=actor, severity=IssueSeverity.MEDIUM,
        )
        resolved_issue = issue_services.resolve_issue(
            issue_id=unloading_issue.id,
            resolution_note="Customer compensated ₹200 via the vendor; goods otherwise accepted.",
            actor=self._admin,
        )
        _backdate(TripIssue.objects.filter(pk=resolved_issue.pk), now - timedelta(days=3, hours=1))

        transit_trip, _ = self._delivered_trips[0]
        transit_issue = issue_services.create_issue(
            trip_id=transit_trip.id, issue_type=IssueType.TRANSIT,
            note="Minor traffic delay due to road work near Silk Board.", actor=actor, severity=IssueSeverity.LOW,
        )
        _backdate(TripIssue.objects.filter(pk=transit_issue.pk), now - timedelta(days=1, hours=2))

        self.stdout.write("Issues: 1 pickup, 1 traffic penalty, 1 unloading (resolved), 1 transit, + 1 auto (cancellation)")

    # ------------------------------------------------------------------
    # 8. Damage reports
    # ------------------------------------------------------------------

    def _seed_damage_reports(self, company, vehicles, drivers):
        now = timezone.now()
        admin = self._admin

        open_report = VehicleDamageReport.objects.create(
            company=company, vehicle=vehicles["tata_ace_1"], reported_by_id=drivers["ravi"].id,
            reporter_type=ReporterType.DRIVER, description="Small dent on the rear bumper, likely from tight parking.",
            photo_url=_photo("damage-tata-ace-1"), severity=DamageSeverity.MEDIUM, status=DamageReportStatus.OPEN,
        )
        _backdate(VehicleDamageReport.objects.filter(pk=open_report.pk), now - timedelta(hours=8))

        resolved_report = VehicleDamageReport.objects.create(
            company=company, vehicle=vehicles["three_wheeler_2"], reported_by_id=admin.id,
            reporter_type=ReporterType.ADMIN, description="Side mirror crack noticed during routine check.",
            photo_url=_photo("damage-three-wheeler-2"), severity=DamageSeverity.LOW, status=DamageReportStatus.RESOLVED,
            resolution_note="Replaced during scheduled maintenance.", resolved_by=admin.id,
            resolved_at=now - timedelta(days=2),
        )
        _backdate(VehicleDamageReport.objects.filter(pk=resolved_report.pk), now - timedelta(days=3))

        self.stdout.write("Damage reports: 1 open, 1 resolved")

    # ------------------------------------------------------------------
    # 9. SOS (already resolved — see note on why nothing is left active)
    # ------------------------------------------------------------------

    def _seed_sos(self, company, drivers):
        now = timezone.now()
        admin = self._admin
        driver = drivers["manjunath"]
        triggered_at = now - timedelta(days=2, hours=1)

        alert = SosAlert.objects.create(
            company=company, driver=driver, latitude=Decimal("12.978400"), longitude=Decimal("77.640800"),
            triggered_at=triggered_at, status=SosStatus.RESOLVED,
            acknowledged_by=admin.id, acknowledged_at=triggered_at + timedelta(minutes=2),
            resolved_by=admin.id, resolved_at=triggered_at + timedelta(minutes=9),
            resolution_note="False alarm — driver confirmed safe.",
        )
        _backdate(SosAlert.objects.filter(pk=alert.pk), triggered_at)

        self.stdout.write(
            "SOS: 1 already-resolved alert (deliberately not active — an 'emergency in progress' banner "
            "on first login would be a bad first impression; demo the live banner manually instead)"
        )
