"""Pickup photos: the camera shots a booking can ask the driver to take of the
whole order (`pickup_photo: order`) or of every item (`per_item`) before the
delivery starts."""

from unittest.mock import patch

from django.test import TestCase

from core.choices import PaymentMode, PaymentStatus, PickupPhotoMode, TripStatus
from core.testing import FAKE_ROUTE, LOCMEM_CACHES, DriverTestMixin, image_file, pdf_file
from trips.models import TripItem
from trips.routing import RoutingService

DRIVER_TRIPS = "/api/v1/driver/trips"


@LOCMEM_CACHES
class BookingWithPickupPhotosTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_client()
        patcher = patch.object(RoutingService, "get_route", return_value=FAKE_ROUTE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def book(self, **extra):
        return self.admin.post(
            "/api/v1/trips",
            {
                "vehicle_type_id": str(self.vehicle_type.id),
                "pickup": {"address": "MG Road", "lat": "12.975000", "lng": "77.605000"},
                "drop": {"address": "Indiranagar", "lat": "12.978300", "lng": "77.640800"},
                "payment_mode": "prepaid",
                **extra,
            },
            format="json",
        )

    def test_no_pickup_photos_unless_asked(self):
        body = self.book().json()
        self.assertEqual(body["pickup_photo"], "none")
        self.assertIsNone(body["pickup_photo_url"])

    def test_a_booking_can_ask_for_one_photo_of_the_order_or_one_per_item(self):
        self.assertEqual(self.book(pickup_photo="order").json()["pickup_photo"], "order")
        body = self.book(pickup_photo="per_item", items=[{"name": "Tap"}]).json()
        self.assertEqual(body["pickup_photo"], "per_item")
        self.assertIsNone(body["items"][0]["pickup_photo_url"])

    def test_per_item_photos_need_items_and_the_mode_must_be_known(self):
        self.assertEqual(self.book(pickup_photo="per_item").status_code, 400)
        self.assertEqual(self.book(pickup_photo="gallery").status_code, 400)


@LOCMEM_CACHES
class PickupPhotoTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver()
        self.vehicle = self.make_vehicle()
        self.client = self.driver_client(self.driver)

    def make(self, mode, status=TripStatus.ARRIVED_AT_PICKUP):
        trip = self.make_trip(
            self.driver,
            self.vehicle,
            status=status,
            pickup_photo=mode,
            payment_mode=PaymentMode.PREPAID,
            payment_status=PaymentStatus.PAID,
        )
        for position, name in enumerate(["Shower", "Tap"]):
            TripItem.objects.create(company=self.company, trip=trip, position=position, name=name)
        return trip

    def upload(self, trip, item=None, photo=None, client=None):
        data = {"photo": photo or image_file("package.jpg")}
        if item is not None:
            data["item_id"] = str(item.id)
        return (client or self.client).post(f"{DRIVER_TRIPS}/{trip.id}/pickup-photo", data, format="multipart")

    def start(self, trip):
        return self.client.post(f"{DRIVER_TRIPS}/{trip.id}/start")

    def test_the_order_photo_is_kept_and_unblocks_the_start(self):
        trip = self.make(PickupPhotoMode.ORDER)
        self.assertEqual(self.start(trip).json()["error"]["code"], "PICKUP_PHOTOS_REQUIRED")

        response = self.upload(trip)

        self.assertEqual(response.status_code, 200, response.content)
        url = response.json()["pickup_photo_url"]
        self.assertTrue(url.startswith("http://testserver/media/") and "pickup-proofs" in url, url)
        self.assertEqual(self.start(trip).status_code, 200)

    def test_per_item_needs_every_item_photographed(self):
        trip = self.make(PickupPhotoMode.PER_ITEM)
        shower, tap = trip.items.all()

        response = self.upload(trip, shower)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()["items"][0]["pickup_photo_url"])
        self.assertIsNone(response.json()["items"][1]["pickup_photo_url"])
        self.assertEqual(self.start(trip).status_code, 409)

        self.upload(trip, tap)
        self.assertEqual(self.start(trip).status_code, 200)

    def test_per_item_photos_must_name_an_item_of_this_trip(self):
        trip = self.make(PickupPhotoMode.PER_ITEM)
        other = self.make(PickupPhotoMode.PER_ITEM, status=TripStatus.COMPLETED).items.first()
        self.assertEqual(self.upload(trip).status_code, 404)
        self.assertEqual(self.upload(trip, other).status_code, 404)

    def test_photos_can_be_taken_as_soon_as_the_trip_is_assigned(self):
        self.assertEqual(self.upload(self.make(PickupPhotoMode.ORDER, TripStatus.ASSIGNED)).status_code, 200)

    def test_not_once_the_delivery_has_started(self):
        response = self.upload(self.make(PickupPhotoMode.ORDER, TripStatus.IN_PROGRESS))
        self.assertEqual(response.json()["error"]["code"], "INVALID_TRIP_STATUS_TRANSITION")

    def test_only_orders_that_asked_for_them(self):
        trip = self.make(PickupPhotoMode.NONE)
        self.assertEqual(self.upload(trip).json()["error"]["code"], "PICKUP_PHOTO_NOT_REQUESTED")
        self.assertEqual(self.start(trip).status_code, 200)

    def test_it_has_to_be_an_image(self):
        self.assertEqual(self.upload(self.make(PickupPhotoMode.ORDER), photo=pdf_file("x.pdf")).status_code, 400)

    def test_another_drivers_trip_is_off_limits(self):
        other = self.driver_client(self.make_driver())
        self.assertEqual(self.upload(self.make(PickupPhotoMode.ORDER), client=other).status_code, 404)


@LOCMEM_CACHES
class DeliveryPhotoTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver()
        self.vehicle = self.make_vehicle()
        self.client = self.driver_client(self.driver)

    def make(self, mode, status=TripStatus.IN_PROGRESS):
        trip = self.make_trip(
            self.driver,
            self.vehicle,
            status=status,
            delivery_photo=mode,
            payment_mode=PaymentMode.PREPAID,
            payment_status=PaymentStatus.PAID,
        )
        for position, name in enumerate(["Shower", "Tap"]):
            TripItem.objects.create(company=self.company, trip=trip, position=position, name=name)
        return trip

    def upload(self, trip, item=None):
        data = {"photo": image_file("handover.jpg")}
        if item is not None:
            data["item_id"] = str(item.id)
        return self.client.post(f"{DRIVER_TRIPS}/{trip.id}/delivery-photo", data, format="multipart")

    def complete(self, trip):
        return self.client.post(f"{DRIVER_TRIPS}/{trip.id}/complete", {}, format="json")

    def test_the_order_photo_is_needed_to_complete(self):
        trip = self.make(PickupPhotoMode.ORDER)
        self.assertEqual(self.complete(trip).json()["error"]["code"], "DELIVERY_PHOTOS_REQUIRED")

        response = self.upload(trip)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("delivery-proofs", response.json()["delivery_photo_url"])
        self.assertEqual(self.complete(trip).status_code, 200)

    def test_per_item_needs_every_item(self):
        trip = self.make(PickupPhotoMode.PER_ITEM)
        shower, tap = trip.items.all()
        self.assertIsNotNone(self.upload(trip, shower).json()["items"][0]["delivery_photo_url"])
        self.assertEqual(self.complete(trip).status_code, 409)
        self.upload(trip, tap)
        self.assertEqual(self.complete(trip).status_code, 200)

    def test_only_at_the_drop(self):
        response = self.upload(self.make(PickupPhotoMode.ORDER, TripStatus.ARRIVED_AT_PICKUP))
        self.assertEqual(response.json()["error"]["code"], "INVALID_TRIP_STATUS_TRANSITION")

    def test_only_orders_that_asked_for_them(self):
        trip = self.make(PickupPhotoMode.NONE)
        self.assertEqual(self.upload(trip).json()["error"]["code"], "DELIVERY_PHOTO_NOT_REQUESTED")
        self.assertEqual(self.complete(trip).status_code, 200)


@LOCMEM_CACHES
class OrderNoteTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_client()
        patcher = patch.object(RoutingService, "get_route", return_value=FAKE_ROUTE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def book(self, **extra):
        return self.admin.post(
            "/api/v1/trips",
            {
                "vehicle_type_id": str(self.vehicle_type.id),
                "pickup": {"address": "MG Road", "lat": "12.975000", "lng": "77.605000"},
                "drop": {"address": "Indiranagar", "lat": "12.978300", "lng": "77.640800"},
                "payment_mode": "prepaid",
                **extra,
            },
            format="json",
        )

    def test_a_booking_can_carry_a_note_for_the_driver(self):
        self.assertEqual(self.book(notes="  Call before arriving  ").json()["notes"], "Call before arriving")
        self.assertEqual(self.book().json()["notes"], "")
        self.assertEqual(self.book(notes="x" * 501).status_code, 400)


@LOCMEM_CACHES
class PrepaidDeliveryOtpTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver()
        self.vehicle = self.make_vehicle()
        self.client = self.driver_client(self.driver)

    def make(self, delivery_otp=True, **extra):
        return self.make_trip(
            self.driver,
            self.vehicle,
            status=TripStatus.IN_PROGRESS,
            payment_mode=PaymentMode.PREPAID,
            payment_status=PaymentStatus.PAID,
            delivery_otp=delivery_otp,
            drop_contact_phone="+919888800002",
            **extra,
        )

    def send(self, trip):
        return self.client.post(f"{DRIVER_TRIPS}/{trip.id}/delivery-otp/resend")

    def complete(self, trip, otp=None):
        return self.client.post(f"{DRIVER_TRIPS}/{trip.id}/complete", {"otp": otp} if otp else {}, format="json")

    def test_a_prepaid_trip_that_asks_for_it_needs_the_customers_otp(self):
        trip = self.make()
        self.assertEqual(self.complete(trip).json()["error"]["code"], "INVALID_DELIVERY_OTP")

        sent = self.send(trip)
        self.assertEqual(sent.status_code, 200, sent.content)
        otp = sent.json()["otp"]
        self.assertRegex(otp, r"^\d{4}$")

        self.assertEqual(self.complete(trip, "0000" if otp != "0000" else "1111").status_code, 400)
        self.assertEqual(self.complete(trip, otp).status_code, 200)

    def test_the_otp_waits_for_the_delivery_photos(self):
        trip = self.make(delivery_photo=PickupPhotoMode.ORDER)
        self.assertEqual(self.send(trip).json()["error"]["code"], "DELIVERY_PHOTOS_REQUIRED")

    def test_without_the_flag_a_prepaid_trip_completes_as_before(self):
        trip = self.make(delivery_otp=False)
        self.assertEqual(self.send(trip).json()["error"]["code"], "NOT_COD_TRIP")
        self.assertEqual(self.complete(trip).status_code, 200)


@LOCMEM_CACHES
class OrderNumberTests(OrderNoteTests):
    def test_every_booking_gets_an_od_number_from_its_date_and_a_running_count(self):
        from django.utils import timezone

        first, second = self.book().json(), self.book().json()
        prefix = f"OD{timezone.localdate():%Y%m%d}000"
        self.assertTrue(first["order_number"].startswith(prefix), first["order_number"])
        n1, n2 = (int(t["order_number"][len(prefix):]) for t in (first, second))
        self.assertEqual(n2, n1 + 1)
