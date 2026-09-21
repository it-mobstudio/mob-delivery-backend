"""COD payment through Razorpay: the QR is created by Razorpay (one single-use,
fixed-amount code per trip), and the payment is confirmed by Razorpay — through
its webhook or a server-side check — never on the driver's word.

Razorpay is faked at the HTTP boundary (`requests.request` inside
trips.payments), so these tests pin exactly what we send and how we read what
comes back. They can't prove Razorpay accepts it — that needs real test keys."""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.choices import PaymentMode, PaymentStatus, TripStatus
from core.testing import LOCMEM_CACHES, DriverTestMixin
from drivers.wallet import WalletService
from trips.models import Trip, TripItem
from trips.payments import to_paise
from trips.services import TripService

BASE = "/api/v1/driver/trips"
WEBHOOK = "/api/v1/webhooks/razorpay"
SECRET = "whsec_test_secret"

RAZORPAY = override_settings(
    PAYMENT_PROVIDER="razorpay",
    RAZORPAY_KEY_ID="rzp_test_key",
    RAZORPAY_KEY_SECRET="rzp_test_secret",
    RAZORPAY_WEBHOOK_SECRET=SECRET,
    RAZORPAY_API_BASE="https://api.razorpay.com/v1",
    RAZORPAY_QR_VALID_MINUTES=30,
    RAZORPAY_TIMEOUT_SECONDS=10,
)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeRazorpay:
    """Just enough of Razorpay's QR Codes API: create a code, list what was paid
    against it. `calls` records every request exactly as it was sent."""

    def __init__(self):
        self.calls = []
        self.payments = {}
        self.created = 0
        self.next_response = None  # one-shot override: a FakeResponse or an Exception

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.next_response is not None:
            answer, self.next_response = self.next_response, None
            if isinstance(answer, Exception):
                raise answer
            return answer
        if method == "POST" and url.endswith("/payments/qr_codes"):
            self.created += 1
            qr_id = f"qr_test{self.created}"
            self.payments[qr_id] = []
            request = kwargs["json"]
            return FakeResponse(
                200,
                {
                    "id": qr_id,
                    "entity": "qr_code",
                    "type": "upi_qr",
                    "usage": "single_use",
                    "image_url": f"https://rzp.io/i/{qr_id}",
                    "payment_amount": request["payment_amount"],
                    "status": "active",
                    "close_by": request["close_by"],
                },
            )
        if method == "GET" and url.endswith("/payments"):
            qr_id = url.split("/qr_codes/")[1].split("/")[0]
            items = self.payments[qr_id]
            return FakeResponse(200, {"entity": "collection", "count": len(items), "items": items})
        raise AssertionError(f"unexpected Razorpay call: {method} {url}")

    def pay(self, qr_id, paise, status="captured", payment_id="pay_test123"):
        self.payments[qr_id].append({"id": payment_id, "entity": "payment", "amount": paise, "status": status, "method": "upi"})

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


# Order matters: settings decorators applied later win, and LOCMEM_CACHES sets
# PAYMENT_PROVIDER=upi_static for every other test.
@RAZORPAY
@LOCMEM_CACHES
class RazorpayTestCase(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeRazorpay()
        patcher = patch("trips.payments.requests.request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sms = patch("trips.services.get_sms_provider")
        self.sms = sms.start().return_value
        self.addCleanup(sms.stop)

        self.driver = self.make_driver()
        self.vehicle = self.make_vehicle()
        self.client = self.driver_client(self.driver)
        self.trip = self.make_trip(self.driver, self.vehicle, status=TripStatus.IN_PROGRESS)  # COD, ₹85.00, unpaid

    def url(self, suffix):
        return f"{BASE}/{self.trip.id}{suffix}"

    def qr(self):
        return self.client.get(self.url("/payment/qr"))

    def collect(self):
        return self.client.post(self.url("/payment/collect"))

    def reload(self):
        self.trip.refresh_from_db()
        return self.trip

    def sent_otps(self):
        return [call.args for call in self.sms.send_otp.call_args_list]


class RazorpayQrTests(RazorpayTestCase):
    def test_a_single_use_fixed_amount_upi_qr_is_created_for_exactly_the_fare(self):
        response = self.qr()

        self.assertEqual(response.status_code, 200, response.content)
        method, url, kwargs = self.fake.calls[0]
        self.assertEqual((method, url), ("POST", "https://api.razorpay.com/v1/payments/qr_codes"))
        self.assertEqual(kwargs["auth"], ("rzp_test_key", "rzp_test_secret"), "HTTP Basic with the key id and secret")
        self.assertEqual(kwargs["timeout"], 10)
        body = kwargs["json"]
        self.assertEqual((body["type"], body["usage"], body["fixed_amount"]), ("upi_qr", "single_use", True))
        self.assertEqual(body["payment_amount"], 8500, "₹85.00 in paise")
        self.assertEqual(body["notes"]["trip_id"], str(self.trip.id), "so the payment can be traced back to the trip")
        self.assertEqual(body["notes"]["company_id"], str(self.company.id))
        expected_close = (timezone.now() + timedelta(minutes=30)).timestamp()
        self.assertAlmostEqual(body["close_by"], expected_close, delta=60)

    def test_the_answer_carries_razorpays_image_not_a_static_link(self):
        body = self.qr().json()

        self.assertEqual(body["provider"], "razorpay")
        self.assertEqual(body["reference"], "qr_test1")
        self.assertEqual(body["image_url"], "https://rzp.io/i/qr_test1")
        self.assertIsNone(body["qr_payload"], "there is no static UPI link to draw")
        self.assertEqual((body["amount"], body["currency"]), (85.0, "INR"))
        self.assertIsNotNone(body["expires_at"])
        self.assertNotIn("upi://", json.dumps(body))

    def test_the_code_is_remembered_on_the_trip(self):
        self.qr()
        trip = self.reload()
        self.assertEqual((trip.payment_provider, trip.payment_qr_id), ("razorpay", "qr_test1"))
        self.assertEqual(trip.payment_qr_image_url, "https://rzp.io/i/qr_test1")
        self.assertIsNotNone(trip.payment_qr_expires_at)
        self.assertEqual(trip.payment_status, PaymentStatus.PENDING)

    def test_reopening_the_payment_screen_shows_the_same_live_code(self):
        first = self.qr().json()
        second = self.qr().json()

        self.assertEqual(first["reference"], second["reference"])
        self.assertEqual(len(self.fake.posts()), 1, "no second code minted")
        self.assertEqual(self.fake.calls[-1][0], "GET", "it did check the first one hadn't been paid")

    def test_an_expired_code_is_replaced(self):
        first = self.qr().json()
        Trip.objects.filter(pk=self.trip.pk).update(payment_qr_expires_at=timezone.now() - timedelta(minutes=1))

        second = self.qr().json()

        self.assertNotEqual(first["reference"], second["reference"])
        self.assertEqual(self.reload().payment_qr_id, second["reference"])

    def test_a_code_about_to_expire_is_replaced_too(self):
        first = self.qr().json()
        Trip.objects.filter(pk=self.trip.pk).update(payment_qr_expires_at=timezone.now() + timedelta(seconds=20))
        self.assertNotEqual(self.qr().json()["reference"], first["reference"])

    def test_a_payment_that_already_arrived_is_found_and_confirmed_instead_of_a_new_code(self):
        code = self.qr().json()["reference"]
        self.fake.pay(code, 8500)

        response = self.qr()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ALREADY_PAID")
        trip = self.reload()
        self.assertEqual(trip.payment_status, PaymentStatus.PAID)
        self.assertEqual(trip.payment_reference, "pay_test123")
        self.assertEqual(len(self.fake.posts()), 1, "no replacement code for a trip that's been paid")
        self.assertEqual(len(self.sent_otps()), 1, "and the customer got their delivery OTP")

    def test_no_code_before_the_delivery_starts_or_for_a_prepaid_trip_or_with_items_unchecked(self):
        assigned = self.make_trip(self.driver, self.vehicle, status=TripStatus.ASSIGNED)
        prepaid = self.make_trip(self.driver, self.vehicle, status=TripStatus.IN_PROGRESS, payment_mode=PaymentMode.PREPAID, payment_status=PaymentStatus.PAID)
        gated = self.make_trip(self.driver, self.vehicle, status=TripStatus.IN_PROGRESS, verify_items=True)
        TripItem.objects.create(company=self.company, trip=gated, name="Cement", quantity=1)

        answers = {
            trip.id: self.client.get(f"{BASE}/{trip.id}/payment/qr").json()["error"]["code"]
            for trip in (assigned, prepaid, gated)
        }

        self.assertEqual(answers, {assigned.id: "TRIP_NOT_IN_PROGRESS", prepaid.id: "NOT_COD_TRIP", gated.id: "ITEMS_NOT_VERIFIED"})
        self.assertEqual(self.fake.calls, [], "Razorpay is not bothered for any of them")

    def test_an_already_paid_trip_gets_no_code(self):
        Trip.objects.filter(pk=self.trip.pk).update(payment_status=PaymentStatus.PAID)
        self.assertEqual(self.qr().json()["error"]["code"], "ALREADY_PAID")
        self.assertEqual(self.fake.calls, [])

    def test_a_server_without_razorpay_keys_says_so_plainly(self):
        with override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET=""):
            response = self.qr()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "PAYMENT_PROVIDER_NOT_CONFIGURED")
        self.assertEqual(self.fake.calls, [])

    def test_an_unknown_provider_name_is_refused_not_guessed(self):
        with override_settings(PAYMENT_PROVIDER="paypal"):
            response = self.qr()
        self.assertEqual(response.json()["error"]["code"], "PAYMENT_PROVIDER_NOT_CONFIGURED")

    def test_razorpay_being_unreachable_or_failing_is_a_retryable_503(self):
        for failure in (requests.ConnectionError("down"), requests.Timeout("slow"), FakeResponse(502, {}), FakeResponse(500, None)):
            self.fake.next_response = failure
            response = self.qr()
            self.assertEqual(response.status_code, 503, failure)
            self.assertEqual(response.json()["error"]["code"], "PAYMENT_PROVIDER_UNAVAILABLE")
        self.assertEqual(self.reload().payment_qr_id, "", "nothing half-recorded")

    def test_rejected_api_keys_are_reported_as_a_configuration_problem(self):
        for status in (401, 403):
            self.fake.next_response = FakeResponse(status, {"error": {"description": "Authentication failed"}})
            response = self.qr()
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "PAYMENT_PROVIDER_NOT_CONFIGURED"))

    def test_a_refusal_from_razorpay_passes_its_reason_on(self):
        self.fake.next_response = FakeResponse(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "QR codes are not enabled for this account"}})
        response = self.qr()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "PAYMENT_PROVIDER_ERROR")
        self.assertIn("QR codes are not enabled", response.json()["error"]["message"])

    def test_qr_codes_not_activated_on_the_account_is_explained_not_reported_as_a_bad_url(self):
        # Razorpay's answer when the keys are valid but the QR Codes product isn't switched on.
        self.fake.next_response = FakeResponse(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The requested URL was not found on the server."}})
        response = self.qr()
        self.assertEqual(response.status_code, 503)
        error = response.json()["error"]
        self.assertEqual(error["code"], "PAYMENT_PROVIDER_NOT_CONFIGURED")
        self.assertIn("QR Codes isn't activated", error["message"])
        self.assertIn("Razorpay support", error["message"])
        self.assertNotIn("URL", error["message"])

    def test_an_answer_we_cannot_read_is_an_error_not_a_crash(self):
        for answer in (FakeResponse(200, {"entity": "qr_code"}), FakeResponse(200, None)):
            self.fake.next_response = answer
            self.assertEqual(self.qr().json()["error"]["code"], "PAYMENT_PROVIDER_ERROR")

    def test_only_rupees_can_be_taken_by_upi(self):
        Trip.objects.filter(pk=self.trip.pk).update(currency="USD")
        self.assertEqual(self.qr().status_code, 422)

    def test_paise_conversion_rounds_half_up(self):
        self.assertEqual([to_paise(Decimal(x)) for x in ("85.00", "111.62", "0.01", "0.005", "1499.995")], [8500, 11162, 1, 1, 150000])


class RazorpayCollectTests(RazorpayTestCase):
    """The driver asks us to check that the customer has paid."""

    def test_the_drivers_word_is_not_enough_while_nothing_has_been_paid(self):
        self.qr()

        response = self.collect()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PAYMENT_NOT_RECEIVED")
        self.assertEqual(self.reload().payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.sent_otps(), [], "no OTP goes out for money that hasn't arrived")
        cache.clear()
        self.assertEqual(self.client.post(self.url("/complete"), {}, format="json").json()["error"]["code"], "PAYMENT_NOT_COLLECTED")

    def test_saying_paid_before_any_code_was_shown_is_refused_without_asking_razorpay(self):
        response = self.collect()
        self.assertEqual(response.json()["error"]["code"], "PAYMENT_NOT_RECEIVED")
        self.assertEqual(self.fake.calls, [])

    def test_once_razorpay_reports_the_payment_it_is_confirmed_and_the_otp_goes_out(self):
        code = self.qr().json()["reference"]
        self.fake.pay(code, 8500, payment_id="pay_LZ1x")

        response = self.collect()

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("OTP", response.json()["message"])
        trip = self.reload()
        self.assertEqual(trip.payment_status, PaymentStatus.PAID)
        self.assertEqual(trip.payment_reference, "pay_LZ1x", "the Razorpay payment id, for reconciliation")
        self.assertIsNotNone(trip.cod_collected_at)
        self.assertEqual(self.sent_otps(), [(self.trip.drop_contact_phone, response.json()["otp"])])

        done = self.client.post(self.url("/complete"), {"otp": response.json()["otp"]}, format="json")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["payment_reference"], "pay_LZ1x")
        self.assertEqual(WalletService.balance(self.driver), Decimal("68.00"), "and the driver is paid for it")

    def test_a_short_or_uncaptured_payment_does_not_count(self):
        code = self.qr().json()["reference"]
        self.fake.pay(code, 8400)  # ₹1 short
        self.fake.pay(code, 8500, status="authorized", payment_id="pay_auth")
        self.fake.pay(code, 8500, status="failed", payment_id="pay_failed")

        self.assertEqual(self.collect().json()["error"]["code"], "PAYMENT_NOT_RECEIVED")

    def test_paying_more_than_the_fare_is_fine(self):
        code = self.qr().json()["reference"]
        self.fake.pay(code, 9000)
        self.assertEqual(self.collect().status_code, 200)

    def test_it_is_one_shot_like_before(self):
        code = self.qr().json()["reference"]
        self.fake.pay(code, 8500)
        self.collect()
        again = self.collect()
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (409, "ALREADY_PAID"))
        self.assertEqual(len(self.sent_otps()), 1)

    def test_a_code_from_another_provider_is_not_mistaken_for_a_razorpay_one(self):
        Trip.objects.filter(pk=self.trip.pk).update(payment_provider="upi_static", payment_qr_id="qr_old")
        self.assertEqual(self.collect().json()["error"]["code"], "PAYMENT_NOT_RECEIVED")
        self.assertEqual(self.fake.calls, [])

    @override_settings(PAYMENT_PROVIDER="upi_static")
    def test_the_local_stand_in_still_takes_the_drivers_word(self):
        response = self.collect()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reload().payment_reference, "")
        self.assertEqual(self.fake.calls, [])


class RazorpayWebhookTests(RazorpayTestCase):
    def setUp(self):
        super().setUp()
        self.code = self.qr().json()["reference"]
        self.anon = APIClient()

    def event(self, qr_id=None, paise=8500, payment_id="pay_hook1", name="qr_code.credited", with_payment=True):
        payload = {"qr_code": {"entity": {"id": qr_id or self.code, "entity": "qr_code", "payments_amount_received": paise, "status": "closed"}}}
        if with_payment:
            payload["payment"] = {"entity": {"id": payment_id, "entity": "payment", "amount": paise, "status": "captured", "method": "upi"}}
        return {"entity": "event", "event": name, "contains": list(payload), "payload": payload, "created_at": 1790000000}

    def post(self, event, secret=SECRET, signature=None, raw=None):
        body = raw if raw is not None else json.dumps(event)
        if signature is None:
            signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        return self.anon.post(WEBHOOK, data=body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature)

    def test_a_payment_notice_marks_the_trip_paid_and_texts_the_otp_with_no_driver_involved(self):
        response = self.post(self.event())

        self.assertEqual((response.status_code, response.json()), (200, {"status": "paid"}))
        trip = self.reload()
        self.assertEqual(trip.payment_status, PaymentStatus.PAID)
        self.assertEqual(trip.payment_reference, "pay_hook1")
        self.assertEqual([phone for phone, _ in self.sent_otps()], [self.trip.drop_contact_phone])

    def test_the_driver_can_then_finish_the_delivery_with_that_otp(self):
        self.post(self.event())
        otp = cache.get(TripService._delivery_otp_cache_key(self.trip.id))

        done = self.client.post(self.url("/complete"), {"otp": otp}, format="json")

        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(self.collect().json()["error"]["code"], "ALREADY_PAID", "the driver's tap after the fact is harmless")

    def test_the_app_sees_the_trip_flip_to_paid_on_its_next_poll(self):
        self.post(self.event())
        active = self.client.get(f"{BASE}/active").json()["trip"]
        self.assertEqual(active["payment_status"], "paid")
        self.assertEqual(active["payment_reference"], "pay_hook1")

    def test_the_same_notice_twice_changes_nothing_the_second_time(self):
        self.post(self.event())
        second = self.post(self.event())
        self.assertEqual(second.json(), {"status": "already_paid"})
        self.assertEqual(len(self.sent_otps()), 1, "the customer isn't texted twice")

    def test_only_a_notice_signed_with_the_webhook_secret_is_believed(self):
        forged = self.post(self.event(), secret="not-the-secret")
        unsigned = self.anon.post(WEBHOOK, data=json.dumps(self.event()), content_type="application/json")

        for response in (forged, unsigned):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "INVALID_SIGNATURE")
        self.assertEqual(self.reload().payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.sent_otps(), [])

    def test_a_tampered_body_fails_the_signature(self):
        body = json.dumps(self.event())
        signature = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        tampered = body.replace("8500", "1")
        response = self.anon.post(WEBHOOK, data=tampered, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature)
        self.assertEqual(response.status_code, 400)

    def test_without_a_webhook_secret_configured_nothing_is_accepted(self):
        with override_settings(RAZORPAY_WEBHOOK_SECRET=""):
            response = self.post(self.event(), secret="")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "WEBHOOK_NOT_CONFIGURED"))
        self.assertEqual(self.reload().payment_status, PaymentStatus.PENDING)

    def test_a_signed_body_that_is_not_a_json_object_is_refused(self):
        for raw in ("not json", "[1, 2]", '"text"'):
            response = self.post(None, raw=raw)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "INVALID_PAYLOAD"), raw)

    def test_notices_we_do_not_act_on_are_acknowledged_so_razorpay_stops_retrying(self):
        cases = {
            "ignored": self.event(name="payment.captured"),
            "unknown_qr": self.event(qr_id="qr_someone_elses"),
        }
        for outcome, event in cases.items():
            response = self.post(event)
            self.assertEqual((response.status_code, response.json()["status"]), (200, outcome))
        self.assertEqual(self.reload().payment_status, PaymentStatus.PENDING)

    def test_a_short_payment_does_not_mark_the_trip_paid(self):
        response = self.post(self.event(paise=8400))
        self.assertEqual(response.json(), {"status": "underpaid"})
        self.assertEqual(self.reload().payment_status, PaymentStatus.PENDING)

    def test_the_qr_totals_are_used_when_the_notice_has_no_payment_entity(self):
        response = self.post(self.event(with_payment=False))
        self.assertEqual(response.json(), {"status": "paid"})
        self.assertEqual(self.reload().payment_reference, "")

    def test_money_for_a_trip_that_is_no_longer_on_the_road_is_not_marked_paid(self):
        Trip.objects.filter(pk=self.trip.pk).update(status=TripStatus.CANCELLED)
        response = self.post(self.event())
        self.assertEqual(response.json(), {"status": "trip_not_in_progress"})
        self.assertEqual(self.reload().payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.sent_otps(), [])
