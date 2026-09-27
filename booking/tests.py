"""Customer booking (the site root, /): phone sign-in, fares, booking, tracking and
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
class BookingTestBase(DriverTestMixin, TestCase):
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
        otp = self.post("/api/web/otp/request/", {"phone": phone}, client).json()["debug_otp"]
        self.assertEqual(self.post("/api/web/otp/verify/", {"phone": phone, "otp": otp}, client).status_code, 200)
        return Customer.objects.get(phone_number=f"+91{phone}")

    def book(self, **extra):
        return self.post("/api/web/book/", {"vehicle_type_id": str(self.vehicle_type.pk), "pickup": PICKUP, "drop": DROP, **extra})


class BookingTests(BookingTestBase):
    # -- sign-in ------------------------------------------------------------------------

    def test_signing_in_with_a_phone_code_creates_the_customer_once(self):
        self.assertRedirects(self.web.get("/"), "/login/?next=/", fetch_redirect_response=False)
        customer = self.sign_in()
        self.assertEqual(self.web.get("/").status_code, 200)
        from django.core.cache import cache

        cache.clear()  # past the 30 s resend throttle
        self.sign_in(client=Client(HTTP_HOST="localhost"))
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(customer.company, self.company)

    def test_a_wrong_code_is_refused_and_five_wrong_codes_burn_it(self):
        otp = self.post("/api/web/otp/request/", {"phone": "9876543210"}).json()["debug_otp"]
        wrong = "0000" if otp != "0000" else "1111"
        for _ in range(5):
            self.assertEqual(self.post("/api/web/otp/verify/", {"phone": "9876543210", "otp": wrong}).status_code, 400)
        self.assertEqual(self.post("/api/web/otp/verify/", {"phone": "9876543210", "otp": otp}).status_code, 400)

    def test_bad_numbers_and_rapid_resends_are_refused(self):
        self.assertEqual(self.post("/api/web/otp/request/", {"phone": "12345"}).status_code, 400)
        self.post("/api/web/otp/request/", {"phone": "9876543210"})
        self.assertEqual(self.post("/api/web/otp/request/", {"phone": "9876543210"}).status_code, 429)

    def test_the_apis_need_a_signed_in_customer(self):
        self.assertEqual(self.post("/api/web/options/", {"pickup": PICKUP, "drop": DROP}).status_code, 401)

    # -- fares & drivers -------------------------------------------------------------------

    def test_options_list_every_vehicle_with_a_fare_and_the_nearest_driver(self):
        self.sign_in()
        self.make_vehicle_type("Tata Ace", "four_wheeler")
        driver, vehicle = self.make_driver(), self.make_vehicle()
        from drivers.services import DriverService

        DriverService.go_online(driver, vehicle, Decimal("12.976"), Decimal("77.606"))
        options = self.post("/api/web/options/", {"pickup": PICKUP, "drop": DROP}).json()["options"]
        self.assertEqual({o["name"] for o in options}, {"Bike", "Tata Ace"})
        bike = next(o for o in options if o["name"] == "Bike")
        self.assertGreater(bike["fare"], 0)
        self.assertIsNotNone(bike["eta_min"])
        self.assertIsNone(next(o for o in options if o["name"] == "Tata Ace")["eta_min"], "no ace on duty")

        near = self.web.get("/api/web/nearby/?lat=12.975&lng=77.605").json()["drivers"]
        self.assertEqual(len(near), 1)
        self.assertNotIn("name", near[0], "customers never see who the drivers are")

    # -- booking & tracking -----------------------------------------------------------------

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

        state = self.web.get(f"/api/web/trips/{trip.pk}/").json()
        self.assertEqual(state["number"], trip.order_number)
        self.assertTrue(state["can_cancel"])
        self.assertEqual(self.web.get(f"/trips/{trip.pk}/").status_code, 200)
        listed = self.web.get("/api/web/trips/").json()
        self.assertEqual([t["number"] for t in listed["trips"]], [trip.order_number])
        self.assertEqual(self.web.get("/api/web/trips/?tab=cancelled").json()["total"], 0)
        self.assertEqual(self.web.get("/api/web/trips/?tab=active").json()["trips"][0]["active"], True)

    def test_another_customer_cannot_see_or_cancel_it(self):
        self.sign_in()
        trip_id = self.book().json()["id"]
        other = Client(HTTP_HOST="localhost")
        self.sign_in("9000011111", client=other)
        self.assertEqual(other.get(f"/api/web/trips/{trip_id}/").status_code, 404)
        self.assertEqual(self.post(f"/api/web/trips/{trip_id}/cancel/", {}, other).status_code, 404)

    def test_cancelling_before_pickup_but_not_after(self):
        self.sign_in()
        trip = Trip.objects.get(pk=self.book().json()["id"])
        state = self.post(f"/api/web/trips/{trip.pk}/cancel/", {"reason": "Changed plans"}).json()
        self.assertEqual((state["status"], state["cancelled"]["by"]), ("cancelled", "customer"))

        moving = Trip.objects.get(pk=self.book().json()["id"])
        Trip.objects.filter(pk=moving.pk).update(status=TripStatus.IN_PROGRESS)
        self.assertEqual(self.post(f"/api/web/trips/{moving.pk}/cancel/", {}).status_code, 409)

    def test_every_page_opens(self):
        self.sign_in()
        self.book()
        for url in ("/", "/trips/", "/trips/?tab=active", "/profile/"):
            with self.subTest(url=url):
                self.assertEqual(self.web.get(url).status_code, 200)

    @override_settings(GOOGLE_MAPS_API_KEY="")
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
            first = self.web.get("/api/web/places/?q=hazratganj").json()["places"]
            self.web.get("/api/web/places/?q=hazratganj")
        self.assertEqual(first[0]["title"], "Hazratganj")
        self.assertEqual(get.call_count, 1, "the second search came from the cache")


class Reply:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


@LOCMEM_CACHES
@override_settings(DRIVER_OTP_DEBUG_RESPONSE=True, GOOGLE_MAPS_API_KEY="test-key")
class GooglePlacesTests(BookingTestBase):
    """Address search through Google (Autocomplete → Details, Geocoding for a
    pin), always from our server, falling back to OpenStreetMap."""

    PREDICTIONS = {"status": "OK", "predictions": [{
        "place_id": "ChIJEx6PwQn9mzkRN_tYMY3mAb8", "description": "Hazratganj, Lucknow, Uttar Pradesh, India",
        "distance_meters": 2380, "types": ["sublocality", "political"],
        "structured_formatting": {"main_text": "Hazratganj", "secondary_text": "Lucknow, Uttar Pradesh, India",
                                  "main_text_matched_substrings": [{"offset": 0, "length": 5}]}}]}

    def test_google_suggestions_carry_a_place_id_the_match_and_the_distance(self):
        self.sign_in()
        with patch("booking.services.requests.get", return_value=Reply(self.PREDICTIONS)) as get:
            places = self.web.get("/api/web/places/?q=hazra&lat=26.85&lng=80.94&session=abc-123").json()["places"]
            self.web.get("/api/web/places/?q=hazra&lat=26.85&lng=80.94&session=abc-123")
        self.assertEqual(places, [{"place_id": "ChIJEx6PwQn9mzkRN_tYMY3mAb8", "title": "Hazratganj",
                                   "subtitle": "Lucknow, Uttar Pradesh", "match": [[0, 5]], "distance_m": 2380,
                                   "types": ["sublocality", "political"]}])
        params = get.call_args.kwargs["params"]
        self.assertIn("place/autocomplete", get.call_args.args[0])
        self.assertEqual((params["sessiontoken"], params["components"], params["origin"]), ("abc-123", "country:in", "26.8500,80.9400"))
        self.assertEqual(params["key"], "test-key", "the key is added on the server, never sent to the browser")
        self.assertEqual(get.call_count, 1, "repeat searches come from the cache")

    def test_picking_a_suggestion_gets_its_coordinates(self):
        self.sign_in()
        details = {"status": "OK", "result": {"name": "Hazratganj", "formatted_address": "Hazratganj, Lucknow, Uttar Pradesh 226001, India",
                                              "geometry": {"location": {"lat": 26.8505, "lng": 80.9467}}}}
        with patch("booking.services.requests.get", return_value=Reply(details)):
            place = self.web.get("/api/web/place/?id=ChIJEx6PwQn9mzkRN_tYMY3mAb8&session=abc").json()
        self.assertEqual(place["title"], "Hazratganj")
        self.assertEqual(place["subtitle"], "Lucknow, Uttar Pradesh 226001")
        self.assertEqual((place["lat"], place["lng"]), (26.8505, 80.9467))
        self.assertEqual(self.web.get("/api/web/place/?id=bad id!").status_code, 404)

    def test_a_refused_key_falls_back_to_openstreetmap_and_backs_off(self):
        self.sign_in()
        photon = {"features": [{"geometry": {"coordinates": [80.94, 26.85]},
                                "properties": {"name": "Hazratganj", "city": "Lucknow", "countrycode": "IN"}}]}
        replies = [Reply({"status": "REQUEST_DENIED", "error_message": "API key not valid"}), Reply(photon), Reply(photon)]
        with patch("booking.services.requests.get", side_effect=replies) as get:
            places = self.web.get("/api/web/places/?q=hazratganj").json()["places"]
            self.web.get("/api/web/places/?q=aminabad")
        self.assertEqual((places[0]["title"], places[0]["lat"]), ("Hazratganj", 26.85))
        urls = [c.args[0] for c in get.call_args_list]
        self.assertEqual(sum("googleapis" in u for u in urls), 1, "Google isn't retried straight after refusing")

    def test_a_dropped_pin_is_named_by_google_but_stays_where_it_was_put(self):
        self.sign_in()
        geocode = {"status": "OK", "results": [
            {"types": ["plus_code"], "formatted_address": "RWXQ+XX Lucknow, Uttar Pradesh, India"},
            {"types": ["street_address"], "formatted_address": "29, Ashok Marg, Hazratganj, Lucknow, Uttar Pradesh 226001, India"}]}
        with patch("booking.services.requests.get", return_value=Reply(geocode)):
            place = self.web.get("/api/web/reverse/?lat=26.851234&lng=80.941234").json()
        self.assertEqual(place["title"], "29, Ashok Marg")
        self.assertEqual(place["subtitle"], "Hazratganj, Lucknow, Uttar Pradesh 226001")
        self.assertEqual((place["lat"], place["lng"]), (26.851234, 80.941234))


@LOCMEM_CACHES
@override_settings(DRIVER_OTP_DEBUG_RESPONSE=True)
class SavedPlacesAndBookingExtrasTests(BookingTestBase):
    def test_saved_places_home_is_one_of_a_kind_and_others_are_named(self):
        self.sign_in()
        home = self.post("/api/web/saved/", {"kind": "home", "address": "Gomti Nagar, Lucknow", "lat": 26.85, "lng": 81.0,
                                               "details": "Flat 402"})
        self.assertEqual(home.status_code, 201, home.content)
        again = self.post("/api/web/saved/", {"kind": "home", "address": "Aliganj, Lucknow", "lat": 26.89, "lng": 80.94}).json()
        self.assertEqual(again["id"], home.json()["id"], "a second Home replaces the first")
        shop = self.post("/api/web/saved/", {"kind": "other", "label": "Shop", "address": "Aminabad", "lat": 26.84, "lng": 80.92}).json()
        self.assertEqual(shop["name"], "Shop")
        self.assertEqual(self.post("/api/web/saved/", {"kind": "work", "address": ""}).status_code, 400)

        listed = self.web.get("/api/web/saved/").json()["saved"]
        self.assertEqual([p["name"] for p in listed], ["Home", "Shop"])
        self.assertEqual(self.post(f"/api/web/saved/{shop['id']}/", {"kind": "other", "label": "Godown", "address": "Aminabad",
                                                                       "lat": 26.84, "lng": 80.92}).json()["name"], "Godown")

        other = Client(HTTP_HOST="localhost")
        self.sign_in("9000011111", client=other)
        self.assertEqual(other.delete(f"/api/web/saved/{shop['id']}/").status_code, 404, "not theirs")
        self.assertEqual(self.web.delete(f"/api/web/saved/{shop['id']}/").status_code, 200)
        self.assertEqual(len(self.web.get("/api/web/saved/").json()["saved"]), 1)

    def test_goods_and_flat_details_reach_the_driver_and_recent_places_follow(self):
        self.sign_in()
        response = self.book(goods="Furniture & home", notes="Call on arrival",
                             drop={**DROP, "details": "Flat 402, 4th floor"})
        trip = Trip.objects.get(pk=response.json()["id"])
        self.assertEqual(trip.notes, "Goods: Furniture & home · Call on arrival")
        self.assertEqual(trip.drop_address, "Flat 402, 4th floor, Indiranagar, Bengaluru")
        recent = self.web.get("/api/web/saved/").json()["recent"]
        self.assertEqual([p["address"] for p in recent], [trip.drop_address, trip.pickup_address])

    def test_options_come_with_a_picture_and_the_route_to_draw(self):
        self.sign_in()
        data = self.post("/api/web/options/", {"pickup": PICKUP, "drop": DROP}).json()
        self.assertTrue(data["options"][0]["image"].endswith("booking/vehicles/scooter.png"))
        self.assertEqual(data["route"][0], [12.975, 77.605])

    def test_book_again_prefills_the_last_trip(self):
        self.sign_in()
        trip_id = self.book(receiver_name="Rahul", receiver_phone="9123456789").json()["id"]
        page = self.web.get(f"/?again={trip_id}")
        boot = page.context["boot"]
        self.assertEqual(boot["again"]["drop"]["address"], DROP["address"])
        self.assertEqual((boot["again"]["receiver_name"], boot["again"]["receiver_phone"]), ("Rahul", "9123456789"))
        self.assertIsNone(self.web.get("/?again=nonsense").context["boot"]["again"])
        self.assertEqual(len(boot["active"]), 1)

    def test_the_delivery_otp_shows_on_tracking_once_it_is_issued(self):
        from django.core.cache import cache

        from trips.services import TripService

        self.sign_in()
        trip = Trip.objects.get(pk=self.book().json()["id"])
        self.assertIsNone(self.web.get(f"/api/web/trips/{trip.pk}/").json()["otp"])
        Trip.objects.filter(pk=trip.pk).update(status=TripStatus.IN_PROGRESS)
        cache.set(TripService._delivery_otp_cache_key(trip.id), "4821", 600)
        self.assertEqual(self.web.get(f"/api/web/trips/{trip.pk}/").json()["otp"], "4821")

    def test_vehicle_pictures_follow_the_name_then_the_category(self):
        from .services import vehicle_art

        cases = {("Bike", "two_wheeler"): "scooter", ("E-Rickshaw Loader", "three_wheeler"): "auto",
                 ("Tata Ace", "four_wheeler"): "pickup", ("Eicher 14 ft", "four_wheeler"): "lorry",
                 ("Truck 1 tonne", "four_wheeler"): "truck", ("Carrier", "four_wheeler"): "pickup",
                 ("Pickup 8 ft", "four_wheeler"): "truck", ("Pickup", "four_wheeler"): "pickup", ("Truck 14 ft", "four_wheeler"): "lorry",
                 ("Mini truck", "four_wheeler"): "pickup"}
        for (name, category), art in cases.items():
            self.assertEqual(vehicle_art(category, name), f"booking/vehicles/{art}.png", name)


    def test_profile_saves_through_the_api(self):
        customer = self.sign_in()
        self.assertEqual(self.post("/api/web/profile/", {"full_name": "", "email": ""}).status_code, 400)
        self.assertEqual(self.post("/api/web/profile/", {"full_name": "Priya", "email": "nope"}).status_code, 400)
        self.assertEqual(self.post("/api/web/profile/", {"full_name": "Priya Sharma", "email": "p@example.com"}).json()["full_name"], "Priya Sharma")
        customer.refresh_from_db()
        self.assertEqual((customer.full_name, customer.email), ("Priya Sharma", "p@example.com"))


class RootUrlTests(BookingTestBase):
    def test_the_app_lives_at_the_root_and_old_book_links_redirect(self):
        self.assertRedirects(self.web.get("/"), "/login/?next=/", fetch_redirect_response=False)
        self.assertEqual(self.web.get("/login/").status_code, 200)
        old = self.web.get("/book/trips/?tab=active")
        self.assertEqual((old.status_code, old["Location"]), (301, "/trips/?tab=active"))
        self.assertEqual(self.web.get("/api/v1/health").status_code in (200, 404), True, "the driver API is untouched")
