"""Customer booking (/book/): phone sign-in, fares, booking, tracking and
cancelling — end to end through the same services the company API uses."""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from core.choices import TripStatus
from core.testing import FAKE_ROUTE, LOCMEM_CACHES, DriverTestMixin
from trips.models import Trip
from trips.routing import RoutingService

from .models import Customer
from .services import BookingService

PICKUP = {"lat": 12.975, "lng": 77.605, "address": "MG Road, Bengaluru"}
DROP = {"lat": 12.9783, "lng": 77.6408, "address": "Indiranagar, Bengaluru"}


@LOCMEM_CACHES
@override_settings(DRIVER_OTP_DEBUG_RESPONSE=True)
class BookingTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(BookingService, "company", return_value=self.company)
        patcher.start()
        self.addCleanup(patcher.stop)
        route = patch.object(RoutingService, "get_route", return_value=FAKE_ROUTE)
        route.start()
        self.addCleanup(route.stop)
        self.web = Client(HTTP_HOST="localhost")

    def post(self, url, body, client=None):
        return (client or self.web).post(url, json.dumps(body), content_type="application/json")

    def sign_in(self, phone="9876543210", client=None):
        client = client or self.web
        otp = self.post("/book/api/otp/request/", {"phone": phone}, client).json()["debug_otp"]
        self.assertEqual(self.post("/book/api/otp/verify/", {"phone": phone, "otp": otp}, client).status_code, 200)
        return Customer.objects.get(phone_number=f"+91{phone}")

    # -- sign-in ------------------------------------------------------------------------

    def test_signing_in_with_a_phone_code_creates_the_customer_once(self):
        self.assertRedirects(self.web.get("/book/"), "/book/login/?next=/book/", fetch_redirect_response=False)
        customer = self.sign_in()
        self.assertEqual(self.web.get("/book/").status_code, 200)
        from django.core.cache import cache

        cache.clear()  # past the 30 s resend throttle
        self.sign_in(client=Client(HTTP_HOST="localhost"))
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(customer.company, self.company)

    def test_a_wrong_code_is_refused_and_five_wrong_codes_burn_it(self):
        otp = self.post("/book/api/otp/request/", {"phone": "9876543210"}).json()["debug_otp"]
        wrong = "0000" if otp != "0000" else "1111"
        for _ in range(5):
            self.assertEqual(self.post("/book/api/otp/verify/", {"phone": "9876543210", "otp": wrong}).status_code, 400)
        self.assertEqual(self.post("/book/api/otp/verify/", {"phone": "9876543210", "otp": otp}).status_code, 400)

    def test_bad_numbers_and_rapid_resends_are_refused(self):
        self.assertEqual(self.post("/book/api/otp/request/", {"phone": "12345"}).status_code, 400)
        self.post("/book/api/otp/request/", {"phone": "9876543210"})
        self.assertEqual(self.post("/book/api/otp/request/", {"phone": "9876543210"}).status_code, 429)

    def test_the_apis_need_a_signed_in_customer(self):
        self.assertEqual(self.post("/book/api/options/", {"pickup": PICKUP, "drop": DROP}).status_code, 401)

    # -- fares & drivers -------------------------------------------------------------------

    def test_options_list_every_vehicle_with_a_fare_and_the_nearest_driver(self):
        self.sign_in()
        self.make_vehicle_type("Tata Ace", "four_wheeler")
        driver, vehicle = self.make_driver(), self.make_vehicle()
        from drivers.services import DriverService

        DriverService.go_online(driver, vehicle, Decimal("12.976"), Decimal("77.606"))
        options = self.post("/book/api/options/", {"pickup": PICKUP, "drop": DROP}).json()["options"]
        self.assertEqual({o["name"] for o in options}, {"Bike", "Tata Ace"})
        bike = next(o for o in options if o["name"] == "Bike")
        self.assertGreater(bike["fare"], 0)
        self.assertIsNotNone(bike["eta_min"])
        self.assertIsNone(next(o for o in options if o["name"] == "Tata Ace")["eta_min"], "no ace on duty")

        near = self.web.get("/book/api/nearby/?lat=12.975&lng=77.605").json()["drivers"]
        self.assertEqual(len(near), 1)
        self.assertNotIn("name", near[0], "customers never see who the drivers are")

    # -- booking & tracking -----------------------------------------------------------------

    def book(self, **extra):
        return self.post("/book/api/book/", {"vehicle_type_id": str(self.vehicle_type.pk), "pickup": PICKUP, "drop": DROP, **extra})

    def test_booking_creates_a_trip_the_customer_can_track(self):
        customer = self.sign_in()
        customer.full_name = "Priya Sharma"
        customer.save()
        response = self.book(receiver_name="Rahul", receiver_phone="9123456789", notes="Fragile")
        self.assertEqual(response.status_code, 201, response.content)
        trip = Trip.objects.get(pk=response.json()["id"])
        self.assertEqual((trip.customer, trip.pickup_contact_name, trip.drop_contact_phone, trip.notes),
                         (customer, "Priya Sharma", "+919123456789", "Fragile"))
        self.assertEqual((trip.payment_mode, trip.pickup_photo, trip.delivery_photo), ("cod", "order", "order"))
        self.assertTrue(trip.order_number.startswith("OD"))

        state = self.web.get(f"/book/api/trips/{trip.pk}/").json()
        self.assertEqual(state["number"], trip.order_number)
        self.assertTrue(state["can_cancel"])
        self.assertEqual(self.web.get(f"/book/trips/{trip.pk}/").status_code, 200)
        self.assertIn(trip.order_number, self.web.get("/book/trips/").content.decode())

    def test_another_customer_cannot_see_or_cancel_it(self):
        self.sign_in()
        trip_id = self.book().json()["id"]
        other = Client(HTTP_HOST="localhost")
        self.sign_in("9000011111", client=other)
        self.assertEqual(other.get(f"/book/api/trips/{trip_id}/").status_code, 404)
        self.assertEqual(self.post(f"/book/api/trips/{trip_id}/cancel/", {}, other).status_code, 404)

    def test_cancelling_before_pickup_but_not_after(self):
        self.sign_in()
        trip = Trip.objects.get(pk=self.book().json()["id"])
        state = self.post(f"/book/api/trips/{trip.pk}/cancel/", {"reason": "Changed plans"}).json()
        self.assertEqual((state["status"], state["cancelled"]["by"]), ("cancelled", "customer"))

        moving = Trip.objects.get(pk=self.book().json()["id"])
        Trip.objects.filter(pk=moving.pk).update(status=TripStatus.IN_PROGRESS)
        self.assertEqual(self.post(f"/book/api/trips/{moving.pk}/cancel/", {}).status_code, 409)

    def test_every_page_opens(self):
        self.sign_in()
        self.book()
        for url in ("/book/", "/book/trips/", "/book/trips/?tab=active", "/book/profile/"):
            with self.subTest(url=url):
                self.assertEqual(self.web.get(url).status_code, 200)

    def test_place_search_goes_through_our_server_and_is_cached(self):
        self.sign_in()
        feature = {"geometry": {"coordinates": [80.94, 26.85]},
                   "properties": {"name": "Hazratganj", "city": "Lucknow", "state": "Uttar Pradesh", "countrycode": "IN"}}

        class Reply:
            def raise_for_status(self):
                pass

            def json(self):
                return {"features": [feature]}

        with patch("booking.services.requests.get", return_value=Reply()) as get:
            first = self.web.get("/book/api/places/?q=hazratganj").json()["places"]
            self.web.get("/book/api/places/?q=hazratganj")
        self.assertEqual(first[0]["title"], "Hazratganj")
        self.assertEqual(get.call_count, 1, "the second search came from the cache")
