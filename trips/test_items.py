"""Invoice + item list on a booking, and the driver's item-by-item verification
at the drop (with optional proof photos) that gates payment and completion."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.choices import ItemVerificationStatus as Item
from core.choices import PaymentMode, PaymentStatus, TripStatus
from core.constants import MAX_TRIP_ITEMS
from core.testing import FAKE_ROUTE, LOCMEM_CACHES, DriverTestMixin, image_file, pdf_file
from trips.models import TripItem
from trips.routing import RoutingService

DRIVER_TRIPS = "/api/v1/driver/trips"
INVOICE = "https://files.example.com/invoices/INV-1001.pdf"

ITEMS = [
    {"name": "Cement bag 50kg", "quantity": 4, "unit": "bags", "sku": "CEM-50", "image_url": "https://img.example.com/cement.jpg", "unit_price": "380.00"},
    {"name": "TMT bar 12mm", "quantity": 20, "notes": "Fe500D"},
]


def booking(**extra):
    return {
        "pickup": {"address": "MG Road", "lat": "12.975000", "lng": "77.605000"},
        "drop": {"address": "Indiranagar", "lat": "12.978300", "lng": "77.640800", "contact_phone": "+919888800002"},
        "payment_mode": "prepaid",
        **extra,
    }


@LOCMEM_CACHES
class BookingWithItemsTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_client()
        patcher = patch.object(RoutingService, "get_route", return_value=FAKE_ROUTE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def book(self, **extra):
        return self.admin.post(
            "/api/v1/trips", booking(vehicle_type_id=str(self.vehicle_type.id), **extra), format="json"
        )

    def test_a_booking_can_carry_an_invoice_and_an_item_list(self):
        response = self.book(invoice_url=INVOICE, invoice_number="INV-1001", verify_items=True, items=ITEMS)

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["invoice_url"], INVOICE)
        self.assertEqual(body["invoice_number"], "INV-1001")
        self.assertTrue(body["verify_items"])
        self.assertEqual([i["name"] for i in body["items"]], ["Cement bag 50kg", "TMT bar 12mm"])  # order kept
        first, second = body["items"]
        self.assertEqual((first["quantity"], first["unit"], first["sku"]), (4, "bags", "CEM-50"))
        self.assertEqual((first["image_url"], first["unit_price"]), ("https://img.example.com/cement.jpg", "380.00"))
        self.assertEqual((second["quantity"], second["notes"], second["image_url"]), (20, "Fe500D", None))
        self.assertTrue(all(i["status"] == "pending" and i["verified_at"] is None for i in body["items"]))

    def test_items_and_invoice_are_optional(self):
        body = self.book().json()
        self.assertEqual(body["items"], [])
        self.assertFalse(body["verify_items"])
        self.assertIsNone(body["invoice_url"])

    def test_items_without_the_verify_flag_are_just_information(self):
        body = self.book(items=ITEMS).json()
        self.assertFalse(body["verify_items"])
        self.assertEqual(len(body["items"]), 2)

    def test_the_quantity_defaults_to_one(self):
        self.assertEqual(self.book(items=[{"name": "Parcel"}]).json()["items"][0]["quantity"], 1)

    def test_asking_for_verification_needs_something_to_verify(self):
        for extra in ({"verify_items": True}, {"verify_items": True, "items": []}):
            response = self.book(**extra)
            self.assertEqual(response.status_code, 400, extra)
            self.assertIn("items", response.json()["error"]["details"])

    def test_the_lists_and_links_are_validated(self):
        bad = [
            {"invoice_url": "ftp://files.example.com/a.pdf"},
            {"invoice_url": "javascript:alert(1)"},
            {"invoice_url": "not a url"},
            {"items": [{"name": "X", "image_url": "file:///etc/passwd"}]},
            {"items": [{"quantity": 2}]},  # no name
            {"items": [{"name": "X", "quantity": 0}]},
            {"items": [{"name": "X", "unit_price": "-1"}]},
            {"items": [{"name": "Y" * 201}]},
            {"items": [{"name": f"Item {n}"} for n in range(MAX_TRIP_ITEMS + 1)]},
        ]
        for extra in bad:
            self.assertEqual(self.book(**extra).status_code, 400, str(extra)[:80])

    def test_a_hundred_items_are_fine(self):
        response = self.book(items=[{"name": f"Item {n}"} for n in range(MAX_TRIP_ITEMS)])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TripItem.objects.count(), MAX_TRIP_ITEMS)

    def test_the_company_reads_the_items_back_on_the_trip(self):
        trip_id = self.book(items=ITEMS, verify_items=True).json()["id"]
        body = self.admin.get(f"/api/v1/trips/{trip_id}").json()
        self.assertEqual(len(body["items"]), 2)
        self.assertTrue(body["verify_items"])

    def test_an_invoice_can_be_uploaded_first_and_then_referenced(self):
        upload = self.admin.post(
            "/api/v1/uploads",
            {"file": pdf_file("inv.pdf"), "purpose": "trip_invoice"},
            format="multipart",
            HTTP_HOST="localhost:8000",  # a real-looking host: URL validation wants one
        )
        self.assertEqual(upload.status_code, 201)
        url = upload.json()["url"]
        self.assertTrue(url.startswith("http://localhost:8000/media/"), url)  # absolute: usable as invoice_url as-is

        self.assertEqual(self.book(invoice_url=url).json()["invoice_url"], url)

    def test_item_pictures_can_be_uploaded_but_not_as_a_pdf(self):
        ok = self.admin.post(
            "/api/v1/uploads", {"file": image_file("cement.jpg"), "purpose": "trip_item_image"}, format="multipart"
        )
        self.assertEqual(ok.status_code, 201)
        bad = self.admin.post(
            "/api/v1/uploads", {"file": pdf_file("x.pdf"), "purpose": "trip_item_image"}, format="multipart"
        )
        self.assertEqual(bad.status_code, 400)

    def test_a_pdf_that_is_not_a_pdf_is_not_stored(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.admin.post(
            "/api/v1/uploads",
            {"file": SimpleUploadedFile("inv.pdf", b"<script>"), "purpose": "trip_invoice"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)


@LOCMEM_CACHES
class ItemVerificationTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver()
        self.vehicle = self.make_vehicle()
        self.client = self.driver_client(self.driver)
        self.trip = self.make_verifiable_trip()
        self.cement, self.tmt = self.trip.items.all()

    def make_verifiable_trip(self, verify=True, status=TripStatus.IN_PROGRESS, **overrides):
        fields = dict(
            status=status,
            verify_items=verify,
            invoice_url=INVOICE,
            invoice_number="INV-1001",
            payment_mode=PaymentMode.PREPAID,
            payment_status=PaymentStatus.PAID,
        )
        fields.update(overrides)
        trip = self.make_trip(self.driver, self.vehicle, **fields)
        for position, name in enumerate(["Cement bag", "TMT bar"]):
            TripItem.objects.create(company=self.company, trip=trip, position=position, name=name, quantity=2 + position)
        return trip

    def verify(self, item, trip=None, client=None, **data):
        trip = trip or self.trip
        return (client or self.client).post(
            f"{DRIVER_TRIPS}/{trip.id}/items/{item.id}/verify", {"status": "delivered", **data}, format="multipart"
        )

    def reset(self, item, trip=None):
        return self.client.delete(f"{DRIVER_TRIPS}/{(trip or self.trip).id}/items/{item.id}/verify")

    def statuses(self, response):
        return [i["status"] for i in response.json()["items"]]

    # -- what the driver sees --------------------------------------------------

    def test_the_driver_sees_the_items_and_the_invoice_on_the_active_trip(self):
        body = self.client.get(f"{DRIVER_TRIPS}/active").json()["trip"]

        self.assertEqual(body["invoice_url"], INVOICE)
        self.assertEqual(body["invoice_number"], "INV-1001")
        self.assertTrue(body["verify_items"])
        self.assertEqual([(i["name"], i["quantity"]) for i in body["items"]], [("Cement bag", 2), ("TMT bar", 3)])

    def test_the_item_list_is_on_the_trip_detail_too(self):
        self.assertEqual(len(self.client.get(f"{DRIVER_TRIPS}/{self.trip.id}").json()["items"]), 2)

    def test_locally_stored_pictures_are_served_with_a_host_the_phone_can_reach(self):
        TripItem.objects.filter(pk=self.cement.pk).update(image_url="/media/dev/cement.jpg")
        TripItem.objects.filter(pk=self.tmt.pk).update(image_url="https://cdn.example.com/tmt.jpg")

        images = [i["image_url"] for i in self.client.get(f"{DRIVER_TRIPS}/active").json()["trip"]["items"]]

        self.assertEqual(images, ["http://testserver/media/dev/cement.jpg", "https://cdn.example.com/tmt.jpg"])

    # -- verifying --------------------------------------------------------------

    def test_marking_an_item_delivered_records_who_and_when(self):
        response = self.verify(self.cement)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.statuses(response), ["delivered", "pending"])  # the whole trip comes back
        self.cement.refresh_from_db()
        self.assertEqual(self.cement.verified_by_id, self.driver.id)
        self.assertIsNotNone(self.cement.verified_at)

    def test_a_photo_taken_at_the_drop_is_kept_as_proof(self):
        response = self.verify(self.cement, photo=image_file("proof.jpg"))

        proof = response.json()["items"][0]["proof_image_url"]
        self.assertTrue(proof.startswith("http://testserver/media/"), proof)
        self.assertIn("delivery-proofs", proof)

    def test_the_photo_is_optional(self):
        response = self.verify(self.cement)
        self.assertIsNone(response.json()["items"][0]["proof_image_url"])

    def test_the_proof_has_to_be_an_image(self):
        response = self.verify(self.cement, photo=pdf_file("proof.pdf"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_UPLOAD")
        self.cement.refresh_from_db()
        self.assertEqual(self.cement.status, Item.PENDING)  # nothing half-recorded

    def test_a_missing_item_needs_an_explanation(self):
        response = self.verify(self.cement, status="not_delivered")
        self.assertEqual(response.status_code, 400)
        self.assertIn("note", response.json()["error"]["details"])

        response = self.verify(self.cement, status="not_delivered", note="  Customer refused — damaged  ")
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual((item["status"], item["driver_note"]), ("not_delivered", "Customer refused — damaged"))

    def test_only_delivered_or_not_delivered_can_be_submitted(self):
        self.assertEqual(self.verify(self.cement, status="pending").status_code, 400)
        self.assertEqual(self.verify(self.cement, status="whatever").status_code, 400)

    def test_saying_it_again_replaces_the_answer_and_keeps_the_photo_unless_replaced(self):
        self.verify(self.cement, photo=image_file("first.jpg"))
        first_photo = self.client.get(f"{DRIVER_TRIPS}/{self.trip.id}").json()["items"][0]["proof_image_url"]

        again = self.verify(self.cement, status="not_delivered", note="Actually short by one").json()["items"][0]
        self.assertEqual(again["status"], "not_delivered")
        self.assertEqual(again["proof_image_url"], first_photo)  # no new photo sent → kept

        newer = self.verify(self.cement, photo=image_file("second.jpg")).json()["items"][0]
        self.assertNotEqual(newer["proof_image_url"], first_photo)

    def test_an_item_can_be_taken_back_to_pending(self):
        self.verify(self.cement, photo=image_file("p.jpg"), note="ok")

        response = self.reset(self.cement)

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["status"], "pending")
        self.assertIsNone(item["verified_at"])
        self.assertIsNone(item["proof_image_url"])
        self.assertEqual(item["driver_note"], "")

    # -- when it's allowed ----------------------------------------------------

    def test_items_are_verified_at_the_drop_not_before_the_delivery_starts(self):
        trip = self.make_verifiable_trip(status=TripStatus.ASSIGNED)
        item = trip.items.first()
        response = self.verify(item, trip=trip)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "TRIP_NOT_IN_PROGRESS")

    def test_verification_is_only_for_orders_that_asked_for_it(self):
        trip = self.make_verifiable_trip(verify=False)
        response = self.verify(trip.items.first(), trip=trip)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "VERIFICATION_NOT_REQUESTED")

    def test_an_item_must_belong_to_this_trip(self):
        other = self.make_verifiable_trip()
        response = self.verify(other.items.first(), trip=self.trip)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "ITEM_NOT_FOUND")

    def test_another_drivers_trip_is_off_limits(self):
        stranger = self.driver_client(self.make_driver())
        self.assertEqual(self.verify(self.cement, client=stranger).status_code, 404)
        self.cement.refresh_from_db()
        self.assertEqual(self.cement.status, Item.PENDING)

    def test_needs_a_driver_token(self):
        self.assertEqual(self.verify(self.cement, client=APIClient()).status_code, 401)

    def test_a_completed_trips_history_cannot_be_rewritten(self):
        self.verify(self.cement)
        self.verify(self.tmt)
        self.client.post(f"{DRIVER_TRIPS}/{self.trip.id}/complete", {}, format="json")

        self.assertEqual(self.verify(self.cement, status="not_delivered", note="changed my mind").status_code, 409)
        self.assertEqual(self.reset(self.cement).status_code, 409)

    # -- the gate ---------------------------------------------------------------

    def test_a_trip_cannot_be_completed_until_every_item_is_answered(self):
        blocked = self.client.post(f"{DRIVER_TRIPS}/{self.trip.id}/complete", {}, format="json")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["code"], "ITEMS_NOT_VERIFIED")

        self.verify(self.cement)
        still = self.client.post(f"{DRIVER_TRIPS}/{self.trip.id}/complete", {}, format="json")
        self.assertEqual(still.json()["error"]["code"], "ITEMS_NOT_VERIFIED")  # one to go

        self.verify(self.tmt, status="not_delivered", note="Out of stock")  # an answer, even if "no"
        done = self.client.post(f"{DRIVER_TRIPS}/{self.trip.id}/complete", {}, format="json")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["status"], "completed")

    def test_taking_an_answer_back_re_blocks_completion(self):
        self.verify(self.cement)
        self.verify(self.tmt)
        self.reset(self.tmt)
        response = self.client.post(f"{DRIVER_TRIPS}/{self.trip.id}/complete", {}, format="json")
        self.assertEqual(response.json()["error"]["code"], "ITEMS_NOT_VERIFIED")

    def test_payment_cannot_be_collected_before_the_items_are_verified(self):
        cod = self.make_verifiable_trip(payment_mode=PaymentMode.COD, payment_status=PaymentStatus.PENDING)
        response = self.client.post(f"{DRIVER_TRIPS}/{cod.id}/payment/collect")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ITEMS_NOT_VERIFIED")
        cod.refresh_from_db()
        self.assertEqual(cod.payment_status, PaymentStatus.PENDING)  # no money taken, no OTP sent

    def test_the_full_cod_flow_with_verification(self):
        cod = self.make_verifiable_trip(payment_mode=PaymentMode.COD, payment_status=PaymentStatus.PENDING)
        for item in cod.items.all():
            self.verify(item, trip=cod, photo=image_file("p.jpg"))

        collected = self.client.post(f"{DRIVER_TRIPS}/{cod.id}/payment/collect")
        self.assertEqual(collected.status_code, 200)
        otp = collected.json()["otp"]
        done = self.client.post(f"{DRIVER_TRIPS}/{cod.id}/complete", {"otp": otp}, format="json")

        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["driver_earning"], "68.00")

    def test_orders_that_did_not_ask_for_verification_are_unaffected(self):
        trip = self.make_verifiable_trip(verify=False)  # has items, but only as information
        response = self.client.post(f"{DRIVER_TRIPS}/{trip.id}/complete", {}, format="json")
        self.assertEqual(response.status_code, 200)

    # -- the company's history ------------------------------------------------------

    def test_the_company_keeps_the_delivery_history(self):
        self.verify(self.cement, photo=image_file("p.jpg"), note="Left at gate")
        self.verify(self.tmt, status="not_delivered", note="Damaged in transit")
        self.client.post(f"{DRIVER_TRIPS}/{self.trip.id}/complete", {}, format="json")

        body = self.admin_client().get(f"/api/v1/trips/{self.trip.id}").json()

        cement, tmt = body["items"]
        self.assertEqual((cement["status"], cement["driver_note"]), ("delivered", "Left at gate"))
        self.assertIsNotNone(cement["verified_at"])
        self.assertIn("delivery-proofs", cement["proof_image_url"])
        self.assertEqual((tmt["status"], tmt["driver_note"]), ("not_delivered", "Damaged in transit"))
        self.assertIsNone(tmt["proof_image_url"])

    def test_verification_is_announced_to_the_rest_of_the_system(self):
        with patch("trips.services.trip_notifier") as notifier:
            self.verify(self.cement)
        events = [call.args[0] for call in notifier.notify.call_args_list]
        self.assertIn("trip.item_verified", events)

    def test_completion_still_works_if_the_otp_cache_is_the_only_thing_missing(self):
        # Guard for the gate's ordering: items are checked before the OTP is
        # consumed, so a blocked completion doesn't burn the customer's OTP.
        cod = self.make_verifiable_trip(payment_mode=PaymentMode.COD, payment_status=PaymentStatus.PAID)
        cache.set(f"trip_delivery_otp:{cod.id}", "123456")

        blocked = self.client.post(f"{DRIVER_TRIPS}/{cod.id}/complete", {"otp": "123456"}, format="json")

        self.assertEqual(blocked.json()["error"]["code"], "ITEMS_NOT_VERIFIED")
        self.assertEqual(cache.get(f"trip_delivery_otp:{cod.id}"), "123456")
