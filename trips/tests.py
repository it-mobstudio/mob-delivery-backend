from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.choices import PaymentMode, PaymentStatus, TripStatus
from core.exceptions import DomainError
from core.testing import FAKE_ROUTE, LOCMEM_CACHES, DriverTestMixin
from trips.routing import RoutingService
from trips.services import TripService

BASE = "/api/v1/driver/trips"


class TripApiTestCase(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver()
        self.vehicle = self.make_vehicle()
        self.client = self.driver_client(self.driver)

    def url(self, trip, suffix=""):
        return f"{BASE}/{trip.id}{suffix}"

    def status_of(self, trip):
        trip.refresh_from_db()
        return trip.status


@LOCMEM_CACHES
class DriverActiveTripTests(TripApiTestCase):
    def test_no_active_trip(self):
        self.assertEqual(self.client.get(f"{BASE}/active").json(), {"trip": None})

    def test_active_trip_carries_everything_the_map_screen_needs(self):
        trip = self.make_trip(self.driver, self.vehicle)

        body = self.client.get(f"{BASE}/active").json()["trip"]

        self.assertEqual(body["id"], str(trip.id))
        self.assertEqual(body["status"], "assigned")
        self.assertEqual(body["route_polyline"], FAKE_ROUTE["polyline"])
        self.assertEqual(body["polyline_precision"], 6)
        self.assertEqual(body["pickup_lat"], "12.975000")
        self.assertEqual(body["drop_contact_phone"], "+919888800002")
        self.assertEqual(body["payment_mode"], "cod")
        self.assertEqual(body["total_fare"], "85.00")

    def test_finished_trips_are_not_active(self):
        self.make_trip(self.driver, self.vehicle, status=TripStatus.COMPLETED)
        self.make_trip(self.driver, self.vehicle, status=TripStatus.CANCELLED)
        self.assertEqual(self.client.get(f"{BASE}/active").json(), {"trip": None})

    def test_booking_assigns_the_nearest_online_driver_who_then_sees_it(self):
        # Full path from the company's booking call to the driver's screen.
        self.client.post(
            "/api/v1/driver/duty/start",
            {"vehicle_id": str(self.vehicle.id), "lat": "12.9760", "lng": "77.6060"},
            format="json",
        )

        with patch.object(RoutingService, "get_route", return_value=FAKE_ROUTE):
            trip = TripService.create_trip(
                company=self.company,
                vehicle_type=self.vehicle_type,
                pickup={"address": "MG Road", "lat": 12.975, "lng": 77.605},
                drop={"address": "Indiranagar", "lat": 12.9783, "lng": 77.6408, "contact_phone": "+919888800002"},
                payment_mode=PaymentMode.COD,
            )

        self.assertEqual(trip.status, TripStatus.ASSIGNED)
        active = self.client.get(f"{BASE}/active").json()["trip"]
        self.assertEqual(active["id"], str(trip.id))
        self.assertEqual(active["driver"]["id"], str(self.driver.id))


@LOCMEM_CACHES
class DriverTripLifecycleTests(TripApiTestCase):
    def test_full_cod_lifecycle_with_qr_payment_and_delivery_otp(self):
        trip = self.make_trip(self.driver, self.vehicle)

        self.assertEqual(self.client.post(self.url(trip, "/arrive")).json()["status"], "arrived_at_pickup")
        self.assertEqual(self.client.post(self.url(trip, "/start")).json()["status"], "in_progress")

        qr = self.client.get(self.url(trip, "/payment/qr")).json()
        self.assertTrue(qr["qr_payload"].startswith("upi://pay?"))
        self.assertIn("am=85.00", qr["qr_payload"])
        self.assertEqual(qr["amount"], 85.0)  # a raw Decimal in a plain dict renders as a JSON number

        # Completing before payment is collected is refused.
        early = self.client.post(self.url(trip, "/complete"), {"otp": "123456"}, format="json")
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.json()["error"]["code"], "PAYMENT_NOT_COLLECTED")

        collected = self.client.post(self.url(trip, "/payment/collect")).json()
        otp = collected["otp"]  # only present because DRIVER_OTP_DEBUG_RESPONSE is on
        self.assertRegex(otp, r"^\d{6}$")

        wrong = self.client.post(self.url(trip, "/complete"), {"otp": "000000" if otp != "000000" else "111111"}, format="json")
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json()["error"]["code"], "INVALID_DELIVERY_OTP")
        self.assertEqual(self.status_of(trip), TripStatus.IN_PROGRESS)

        done = self.client.post(self.url(trip, "/complete"), {"otp": otp}, format="json")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["status"], "completed")
        self.assertEqual(done.json()["payment_status"], "paid")
        self.assertIsNotNone(done.json()["cod_collected_at"])

        # The OTP is single-use, and the finished trip is no longer "active".
        replay = self.client.post(self.url(trip, "/complete"), {"otp": otp}, format="json")
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["error"]["code"], "INVALID_DELIVERY_OTP")
        self.assertEqual(self.client.get(f"{BASE}/active").json(), {"trip": None})

    def test_prepaid_trip_completes_without_an_otp_and_has_no_qr(self):
        trip = self.make_trip(
            self.driver, self.vehicle, payment_mode=PaymentMode.PREPAID, payment_status=PaymentStatus.PAID
        )
        self.client.post(self.url(trip, "/arrive"))
        self.client.post(self.url(trip, "/start"))

        qr = self.client.get(self.url(trip, "/payment/qr"))
        self.assertEqual(qr.status_code, 409)
        self.assertEqual(qr.json()["error"]["code"], "NOT_COD_TRIP")

        self.assertEqual(self.client.post(self.url(trip, "/complete"), {}, format="json").json()["status"], "completed")

    def test_steps_must_happen_in_order(self):
        trip = self.make_trip(self.driver, self.vehicle)

        skip = self.client.post(self.url(trip, "/start"))  # haven't arrived yet
        self.assertEqual(skip.status_code, 409)
        self.assertEqual(skip.json()["error"]["code"], "INVALID_TRIP_STATUS_TRANSITION")

        collect = self.client.post(self.url(trip, "/payment/collect"))  # not in progress
        self.assertEqual(collect.status_code, 409)
        self.assertEqual(collect.json()["error"]["code"], "TRIP_NOT_IN_PROGRESS")

    def test_payment_can_only_be_collected_once(self):
        trip = self.make_trip(self.driver, self.vehicle, status=TripStatus.IN_PROGRESS)
        self.assertEqual(self.client.post(self.url(trip, "/payment/collect")).status_code, 200)

        again = self.client.post(self.url(trip, "/payment/collect"))
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["error"]["code"], "ALREADY_PAID")

    def test_malformed_otp_is_a_validation_error(self):
        trip = self.make_trip(self.driver, self.vehicle, status=TripStatus.IN_PROGRESS)
        self.client.post(self.url(trip, "/payment/collect"))
        response = self.client.post(self.url(trip, "/complete"), {"otp": "12ab"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_driver_can_cancel_before_pickup_but_not_once_underway(self):
        assigned = self.make_trip(self.driver, self.vehicle)
        cancelled = self.client.post(self.url(assigned, "/cancel"), {"reason": "Vehicle breakdown"}, format="json")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(cancelled.json()["cancelled_by"], "driver")
        self.assertEqual(cancelled.json()["cancellation_reason"], "Vehicle breakdown")

        underway = self.make_trip(self.driver, self.vehicle, status=TripStatus.IN_PROGRESS)
        blocked = self.client.post(self.url(underway, "/cancel"), {"reason": "Changed my mind"}, format="json")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["code"], "TRIP_NOT_CANCELLABLE")

    def test_cancel_requires_a_reason(self):
        trip = self.make_trip(self.driver, self.vehicle)
        self.assertEqual(self.client.post(self.url(trip, "/cancel"), {}, format="json").status_code, 400)

    def test_a_driver_cannot_touch_someone_elses_trip(self):
        other = self.make_driver()
        trip = self.make_trip(other, self.make_vehicle())
        for method, suffix in (("post", "/arrive"), ("post", "/cancel"), ("get", ""), ("get", "/payment/qr")):
            response = getattr(self.client, method)(self.url(trip, suffix), {"reason": "x"}, format="json")
            self.assertEqual(response.status_code, 404, suffix)
        self.assertEqual(self.status_of(trip), TripStatus.ASSIGNED)


@LOCMEM_CACHES
class DeliveryOtpResendTests(TripApiTestCase):
    def in_progress_cod_trip(self):
        return self.make_trip(self.driver, self.vehicle, status=TripStatus.IN_PROGRESS)

    def test_resend_replaces_the_expired_otp_so_a_paid_trip_can_still_finish(self):
        trip = self.in_progress_cod_trip()
        first = self.client.post(self.url(trip, "/payment/collect")).json()["otp"]

        # Time passes: the first OTP expires and the resend throttle lapses.
        cache.delete(TripService._delivery_otp_cache_key(trip.id))
        cache.delete(TripService._delivery_otp_throttle_key(trip.id))
        stuck = self.client.post(self.url(trip, "/complete"), {"otp": first}, format="json")
        self.assertEqual(stuck.status_code, 400)

        resent = self.client.post(self.url(trip, "/delivery-otp/resend"))
        self.assertEqual(resent.status_code, 200)
        self.assertIn("+919888800002", resent.json()["message"])

        done = self.client.post(self.url(trip, "/complete"), {"otp": resent.json()["otp"]}, format="json")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["status"], "completed")

    def test_resend_invalidates_the_previous_otp(self):
        trip = self.in_progress_cod_trip()
        first = self.client.post(self.url(trip, "/payment/collect")).json()["otp"]
        cache.delete(TripService._delivery_otp_throttle_key(trip.id))

        second = self.client.post(self.url(trip, "/delivery-otp/resend")).json()["otp"]

        if first != second:  # 1-in-a-million collision would make this vacuous, not wrong
            stale = self.client.post(self.url(trip, "/complete"), {"otp": first}, format="json")
            self.assertEqual(stale.status_code, 400)

    def test_resend_is_throttled(self):
        trip = self.in_progress_cod_trip()
        self.client.post(self.url(trip, "/payment/collect"))  # this send starts the throttle

        response = self.client.post(self.url(trip, "/delivery-otp/resend"))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "OTP_ALREADY_REQUESTED")

    def test_resend_needs_the_payment_collected_first(self):
        trip = self.in_progress_cod_trip()
        response = self.client.post(self.url(trip, "/delivery-otp/resend"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PAYMENT_NOT_COLLECTED")

    def test_resend_is_only_for_cod_trips_in_progress(self):
        prepaid = self.make_trip(
            self.driver, self.vehicle, status=TripStatus.IN_PROGRESS,
            payment_mode=PaymentMode.PREPAID, payment_status=PaymentStatus.PAID,
        )
        self.assertEqual(self.client.post(self.url(prepaid, "/delivery-otp/resend")).json()["error"]["code"], "NOT_COD_TRIP")

        assigned = self.make_trip(self.driver, self.vehicle)
        self.assertEqual(
            self.client.post(self.url(assigned, "/delivery-otp/resend")).json()["error"]["code"], "TRIP_NOT_IN_PROGRESS"
        )


@LOCMEM_CACHES
class DriverTripHistoryTests(TripApiTestCase):
    def test_history_lists_only_my_trips_newest_first_and_is_paginated(self):
        older = self.make_trip(self.driver, self.vehicle, status=TripStatus.COMPLETED)
        newer = self.make_trip(self.driver, self.vehicle, status=TripStatus.CANCELLED, cancellation_reason="No answer")
        self.make_trip(self.make_driver(), self.make_vehicle())  # someone else's

        body = self.client.get(BASE).json()

        self.assertEqual(body["count"], 2)
        self.assertEqual([row["id"] for row in body["results"]], [str(newer.id), str(older.id)])
        self.assertEqual(body["results"][0]["cancellation_reason"], "No answer")
        self.assertNotIn("route_polyline", body["results"][0])  # keep list rows light

    def test_status_filter_accepts_several_values(self):
        self.make_trip(self.driver, self.vehicle, status=TripStatus.COMPLETED)
        self.make_trip(self.driver, self.vehicle, status=TripStatus.CANCELLED)
        self.make_trip(self.driver, self.vehicle, status=TripStatus.ASSIGNED)

        self.assertEqual(self.client.get(BASE, {"status": "completed"}).json()["count"], 1)
        self.assertEqual(self.client.get(BASE, {"status": "completed,cancelled"}).json()["count"], 2)
        self.assertEqual(self.client.get(BASE).json()["count"], 3)

    def test_detail_returns_the_full_trip(self):
        trip = self.make_trip(self.driver, self.vehicle, status=TripStatus.COMPLETED)
        body = self.client.get(self.url(trip)).json()
        self.assertEqual(body["id"], str(trip.id))
        self.assertEqual(body["route_polyline"], FAKE_ROUTE["polyline"])

    def test_requires_a_driver_token(self):
        self.assertEqual(APIClient().get(BASE).status_code, 401)


@LOCMEM_CACHES
class DriverNavigationTests(TripApiTestCase):
    def navigate(self, trip, lat="12.9716", lng="77.5946"):
        return self.client.get(self.url(trip, "/navigation"), {"lat": lat, "lng": lng})

    def test_heads_to_the_pickup_until_the_trip_starts_then_the_drop(self):
        trip = self.make_trip(self.driver, self.vehicle)

        with patch.object(RoutingService, "get_route", return_value=FAKE_ROUTE) as route:
            to_pickup = self.navigate(trip).json()
            trip.status = TripStatus.IN_PROGRESS
            trip.save()
            to_drop = self.navigate(trip).json()

        self.assertEqual(to_pickup["target"], "pickup")
        self.assertEqual(to_pickup["target_lat"], 12.975)
        self.assertEqual(to_pickup["polyline"], FAKE_ROUTE["polyline"])
        self.assertEqual(to_drop["target"], "drop")
        self.assertEqual(to_drop["target_lng"], 77.6408)
        # Routed from the driver's position, with the vehicle's costing model.
        first_call = route.call_args_list[0]
        self.assertEqual(float(first_call.args[0]), 12.9716)
        self.assertEqual(first_call.kwargs["costing"], "motorcycle")

    def test_finished_trip_has_nothing_to_navigate(self):
        trip = self.make_trip(self.driver, self.vehicle, status=TripStatus.COMPLETED)
        response = self.navigate(trip)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "TRIP_NOT_ACTIVE")

    def test_routing_outage_is_a_503_the_app_can_fall_back_from(self):
        trip = self.make_trip(self.driver, self.vehicle)
        with patch.object(
            RoutingService, "get_route", side_effect=DomainError("ROUTING_UNAVAILABLE", "down", status_code=503)
        ):
            response = self.navigate(trip)
        self.assertEqual(response.status_code, 503)

    def test_requires_a_position(self):
        trip = self.make_trip(self.driver, self.vehicle)
        self.assertEqual(self.client.get(self.url(trip, "/navigation")).status_code, 400)


@LOCMEM_CACHES
class BookTestTripCommandTests(DriverTestMixin, TestCase):
    """`manage.py book_test_trip` — the one-liner for putting an order on a
    driver's screen without writing a booking client."""

    def setUp(self):
        super().setUp()
        self.driver = self.make_driver(phone_number="+919000000000")
        self.vehicle = self.make_vehicle()

    def go_on_duty(self, driver=None, lat="26.901257", lng="81.102868", vehicle=None):
        from decimal import Decimal

        from drivers.services import DriverService

        driver = driver or self.driver
        DriverService.go_online(driver, vehicle or self.vehicle, Decimal(lat), Decimal(lng))
        return driver

    def run_command(self, *args, route=FAKE_ROUTE):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with patch.object(RoutingService, "get_route", return_value=route) as get_route:
            call_command("book_test_trip", *args, stdout=out)
        return out.getvalue(), get_route

    def test_books_next_to_the_driver_and_assigns_them(self):
        from trips.models import Trip

        self.go_on_duty()
        output, get_route = self.run_command()

        trip = Trip.objects.get()
        self.assertEqual(trip.driver_id, self.driver.id)
        self.assertEqual(trip.status, TripStatus.ASSIGNED)
        self.assertEqual(trip.payment_mode, PaymentMode.COD)
        self.assertEqual(trip.drop_contact_phone, "+919888800002", "COD needs a phone for the delivery OTP")
        self.assertIn("appears in the app", output)
        # Pickup is the driver's own spot, so they're the nearest driver by definition.
        pickup_lat, pickup_lng = get_route.call_args.args[:2]
        self.assertAlmostEqual(float(pickup_lat), 26.901257, places=6)
        self.assertAlmostEqual(float(pickup_lng), 81.102868, places=6)

    def test_items_and_invoice_can_be_attached_for_trying_out_the_checklist(self):
        from trips import dev_samples
        from trips.models import Trip

        self.go_on_duty()
        output, _ = self.run_command("--items", "3", "--verify-items", "--invoice")

        trip = Trip.objects.get()
        self.assertTrue(trip.verify_items)
        self.assertEqual(trip.items.count(), 3)
        self.assertEqual(
            [i.image_url for i in trip.items.all()], [p.image_url for p in dev_samples.CATALOGUE[:3]], "each product has its own picture"
        )
        self.assertEqual((trip.invoice_number, trip.invoice_url), (dev_samples.INVOICE_NUMBER, dev_samples.INVOICE_URL))
        self.assertTrue(trip.invoice_url.startswith("https://") and trip.invoice_url.endswith(".pdf"))
        self.assertIn("verify each one", output)
        self.assertIn(f"invoice {dev_samples.INVOICE_NUMBER}", output)

    def test_the_default_booking_is_the_whole_flow_with_verification_on(self):
        from trips import dev_samples
        from trips.models import Trip

        self.go_on_duty()
        output, _ = self.run_command()

        trip = Trip.objects.get()
        self.assertTrue(trip.verify_items, "verification is on by default")
        self.assertEqual(trip.payment_mode, PaymentMode.COD)
        # The shop's four products, with their names and pictures, in order.
        items = list(trip.items.all())
        self.assertEqual([i.name for i in items], [p.name for p in dev_samples.CATALOGUE])
        self.assertEqual([i.image_url for i in items], [p.image_url for p in dev_samples.CATALOGUE])
        self.assertEqual([i.sku for i in items], ["560QWI101", "564QWI108", "564QWI151", "576QWI101"])
        for item, product in zip(items, dev_samples.CATALOGUE):
            self.assertTrue(product.quantity[0] <= item.quantity <= product.quantity[1], (item.name, item.quantity))
        self.assertEqual((trip.invoice_number, trip.invoice_url), (dev_samples.INVOICE_NUMBER, dev_samples.INVOICE_URL))
        # It says what was booked and what to try, in order.
        for item in items:
            self.assertIn(item.name, output)
        self.assertIn("verify each one at the drop", output)
        self.assertIn("Try, in the app:", output)
        self.assertIn("item checklist", output)
        self.assertIn("stay locked", output, "tells you to try payment before the items are answered")
        self.assertIn(f"GET /api/v1/trips/{trip.id}", output, "where the recorded history can be read")

    def test_quantities_are_random_within_sensible_bounds(self):
        from trips import dev_samples

        seen = {p.name: set() for p in dev_samples.CATALOGUE}
        for _ in range(60):
            for item in dev_samples.sample_items(4):
                seen[item["name"]].add(item["quantity"])
        for product in dev_samples.CATALOGUE:
            low, high = product.quantity
            self.assertTrue(all(low <= q <= high for q in seen[product.name]), product.name)
            self.assertGreater(len(seen[product.name]), 1, f"{product.name}: the quantity actually varies")

    def test_more_items_than_products_repeats_them_with_a_number(self):
        from trips import dev_samples

        names = [i["name"] for i in dev_samples.sample_items(6)]
        self.assertEqual(names[4:], ["Ultra tech Cement #2", "Dr. Fixit Water proofing #2"])
        self.assertEqual(len(set(names)), 6)

    def test_another_invoice_link_can_be_supplied(self):
        from trips.models import Trip

        self.go_on_duty()
        self.run_command("--invoice-url", "https://example.com/my-invoice.pdf")
        trip = Trip.objects.get()
        self.assertEqual(trip.invoice_url, "https://example.com/my-invoice.pdf")

        Trip.objects.all().delete()
        self.driver.refresh_from_db()
        self.run_command("--no-invoice", "--invoice-url", "https://example.com/ignored.pdf")
        self.assertEqual(Trip.objects.get().invoice_url, "", "no invoice means no invoice")

    def test_each_part_can_be_switched_off(self):
        from django.core.management.base import CommandError

        from trips.models import Trip

        self.go_on_duty()
        self.run_command("--items", "0", "--no-invoice")
        plain = Trip.objects.get()
        self.assertEqual((plain.items.count(), plain.verify_items, plain.invoice_url), (0, False, ""), "a plain order")

        Trip.objects.all().delete()
        self.driver.refresh_from_db()
        self.run_command("--no-verify-items")
        unchecked = Trip.objects.get()
        self.assertEqual((unchecked.items.count(), unchecked.verify_items), (4, False), "items, but nothing to verify")

        Trip.objects.all().delete()
        self.driver.refresh_from_db()
        self.run_command("--items", "5")
        self.assertEqual(Trip.objects.get().items.count(), 5)

    def test_asking_to_verify_no_items_is_refused_and_nothing_is_booked(self):
        from django.core.management.base import CommandError

        from trips.models import Trip

        self.go_on_duty()
        with self.assertRaisesRegex(CommandError, "at least one item"):
            self.run_command("--verify-items", "--items", "0")
        with self.assertRaisesRegex(CommandError, "can't be negative"):
            self.run_command("--items", "-1")
        self.assertEqual(Trip.objects.count(), 0)

    def test_it_says_up_front_whether_the_payment_step_will_work(self):
        from django.test import override_settings

        self.go_on_duty()
        with override_settings(PAYMENT_PROVIDER="razorpay", RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET=""):
            output, _ = self.run_command()
        self.assertIn("Payment isn't set up", output)
        self.assertIn("dev_razorpay_stub.py", output)
        self.assertIn("PAYMENT_PROVIDER=upi_static", output)

        from trips.models import Trip

        Trip.objects.all().delete()
        self.driver.refresh_from_db()
        stub = dict(PAYMENT_PROVIDER="razorpay", RAZORPAY_KEY_ID="rzp_test_stub", RAZORPAY_KEY_SECRET="s", RAZORPAY_API_BASE="http://127.0.0.1:8003/v1")
        with override_settings(**stub):
            output, _ = self.run_command()
        self.assertIn("Razorpay stand-in", output)
        self.assertIn("/simulate/", output)
        self.assertNotIn("Payment isn't set up", output)

        Trip.objects.all().delete()
        self.driver.refresh_from_db()
        output, _ = self.run_command()  # the test settings use the static provider
        self.assertIn("upi_static", output)

    def test_a_prepaid_order_gets_no_payment_steps(self):
        self.go_on_duty()
        output, _ = self.run_command("--mode", "prepaid")
        self.assertIn("prepaid: nothing to collect", output)
        self.assertNotIn("Payment:", output)
        self.assertNotIn("Payment isn't set up", output)

    def test_drop_defaults_to_about_four_km_away_and_distance_km_is_honoured(self):
        from core.geo import haversine_distance_km

        self.go_on_duty()
        for km in (None, 7.5):
            _, get_route = self.run_command(*(["--distance-km", str(km)] if km else []))
            args = get_route.call_args.args
            actual = haversine_distance_km(args[0], args[1], args[2], args[3])
            self.assertAlmostEqual(actual, km or 4.0, delta=0.05)

    def test_explicit_addresses_and_coordinates_win(self):
        from trips.models import Trip

        self.go_on_duty()
        self.run_command(
            "--pickup-lat", "12.9716", "--pickup-lng", "77.5946", "--pickup-address", "MG Road",
            "--drop-lat", "12.9784", "--drop-lng", "77.6408", "--drop-address", "Indiranagar",
        )
        trip = Trip.objects.get()
        self.assertEqual((trip.pickup_address, trip.drop_address), ("MG Road", "Indiranagar"))
        self.assertEqual(float(trip.drop_lng), 77.6408)

    def test_prepaid_mode(self):
        from trips.models import Trip

        self.go_on_duty()
        self.run_command("--mode", "prepaid")
        trip = Trip.objects.get()
        self.assertEqual((trip.payment_mode, trip.payment_status), (PaymentMode.PREPAID, PaymentStatus.PAID))

    def test_an_offline_driver_is_told_to_start_duty_and_nothing_is_booked(self):
        from django.core.management.base import CommandError

        from trips.models import Trip

        with self.assertRaisesRegex(CommandError, "Start duty"):
            self.run_command()
        self.assertEqual(Trip.objects.count(), 0)

    def test_an_unknown_phone_is_explained(self):
        from django.core.management.base import CommandError

        with self.assertRaisesRegex(CommandError, "No driver with phone"):
            self.run_command("--phone", "+910000000000")

    def test_a_driver_with_no_reported_location_needs_coordinates(self):
        from django.core.management.base import CommandError

        self.go_on_duty()
        self.driver.last_known_lat = self.driver.last_known_lng = None
        self.driver.save(update_fields=["last_known_lat", "last_known_lng"])

        with self.assertRaisesRegex(CommandError, "hasn't reported a location"):
            self.run_command()
        # ...but explicit coordinates are enough.
        output, _ = self.run_command("--pickup-lat", "12.9", "--pickup-lng", "77.6")
        self.assertIn("Trip ", output)

    def test_routing_being_down_points_at_the_stand_in(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        self.go_on_duty()
        with patch.object(
            RoutingService, "get_route", side_effect=DomainError("ROUTING_UNAVAILABLE", "down", status_code=503)
        ):
            with self.assertRaisesRegex(CommandError, "dev_valhalla_stub"):
                call_command("book_test_trip")

    def test_it_warns_when_a_nearer_driver_took_the_trip(self):
        from trips.models import Trip

        self.go_on_duty()  # ours: far from the pickup below
        rival = self.make_driver(phone_number="+919000000001")
        self.go_on_duty(rival, lat="12.9720", lng="77.5950", vehicle=self.make_vehicle())

        output, _ = self.run_command("--pickup-lat", "12.9716", "--pickup-lng", "77.5946")

        self.assertEqual(Trip.objects.get().driver_id, rival.id)
        self.assertIn("another online driver was nearer", output)
