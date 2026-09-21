"""Cash on delivery: the Razorpay code, confirming the payment, the delivery OTP."""

from core.openapi.dsl import DRIVER, doc, document, ex, ok, path_param, raw_ex
from trips.serializers import PaymentCollectedSerializer, PaymentQrSerializer
from trips.views import DriverTripDeliveryOtpResendView, DriverTripPaymentCollectView, DriverTripPaymentQrView

TAG = "Driver payments"
TRIP_ID = path_param("id", "The trip's id (UUID).")

RAZORPAY_QR = {
    "provider": "razorpay",
    "reference": "qr_Nx4a1Kp0aB9cXz",
    "qr_payload": None,
    "image_url": "https://rzp.io/i/Nx4a1Kp",
    "amount": 85.0,
    "currency": "INR",
    "expires_at": "2026-09-21T10:15:00Z",
}

UPI_STATIC_QR = {
    "provider": "upi_static",
    "reference": None,
    "qr_payload": "upi://pay?pa=mob-delivery%40upi&pn=MOB+Delivery&am=85.00&cu=INR&tn=Trip+5920b157",
    "image_url": None,
    "amount": 85.0,
    "currency": "INR",
    "expires_at": None,
}

FLOW = """
**The cash-on-delivery flow:** items verified (if asked) -> `GET .../payment/qr` shows the customer a Razorpay code for the exact fare -> the customer pays with any UPI app ->
Razorpay tells the server, which marks the trip **paid** and texts the customer their delivery OTP -> the driver enters that OTP in `POST .../complete`.
The driver's tap on "check payment" (`POST .../payment/collect`) never takes anyone's word for it - the server asks Razorpay. See the **Payments (Razorpay)** guide.
"""

document(
    DriverTripPaymentQrView,
    get=doc(
        id="driverGetPaymentQr",
        tag=TAG,
        summary="Get the scan-to-pay code",
        description="""
For a **cash-on-delivery** trip that is `in_progress`: returns a **single-use Razorpay UPI QR code for exactly this trip's fare**. Show `image_url` (a ready-made image) full-screen and
let the customer scan it with any UPI app - GPay, PhonePe, Paytm, a bank app.

Asking again while the code is still valid returns the **same code** (reopening the screen never creates a second one); after `expires_at` (30 minutes by default) a fresh one is issued.
Because a paid code closes, a screenshot of an old code cannot be paid twice. If the customer has in fact already paid (the webhook was late), the server notices here and answers `ALREADY_PAID`.

The response's `provider` says where the code came from: `razorpay` in production; `upi_static` is a development-only stand-in with no `image_url` - it gives a UPI link
(`qr_payload`) to draw as a QR yourself, and nothing can confirm that it was paid.

If the order asked for item verification, no code is issued until every item is answered (`ITEMS_NOT_VERIFIED`).
""",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        responses={
            200: ok(
                PaymentQrSerializer,
                raw_ex("razorpay", RAZORPAY_QR, "Razorpay code (production)"),
                raw_ex("upi_static", UPI_STATIC_QR, "upi_static (development only)"),
            )
        },
        errors=["NOT_COD_TRIP", "TRIP_NOT_IN_PROGRESS", "ALREADY_PAID", "ITEMS_NOT_VERIFIED", "PAYMENT_PROVIDER_UNAVAILABLE", "PAYMENT_PROVIDER_ERROR", "PAYMENT_PROVIDER_NOT_CONFIGURED", "NOT_YOUR_TRIP"],
        notes=FLOW,
    ),
)

document(
    DriverTripPaymentCollectView,
    post=doc(
        id="driverConfirmPayment",
        tag=TAG,
        summary="Check the payment and send the delivery OTP",
        description="""
The driver's **"Check payment"** button. With Razorpay, the server **asks Razorpay** whether this trip's code has been paid (in full) - it does not take the driver's word for it:

- **Paid** -> the trip is marked paid, the customer is texted their **delivery OTP**, and this returns `200`.
- **Not paid (yet)** -> `409 PAYMENT_NOT_RECEIVED`. That is *not a fault*: ask the customer to scan and pay, then check again in a moment.
- Already paid (the webhook got there first) -> `409 ALREADY_PAID`: carry on to the OTP.

The app doesn't need to poll this: the server marks the trip paid as soon as Razorpay's webhook arrives, so simply re-reading the trip (`GET /driver/trips/{id}`, `payment_status`) shows it.
On a non-production server the response also echoes the delivery OTP in `otp`.
""",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        request=None,
        responses={200: ok(PaymentCollectedSerializer, ex("driver_trip.collect", "Paid - OTP sent (test server shows the code)"))},
        errors=["PAYMENT_NOT_RECEIVED", "ALREADY_PAID", "NOT_COD_TRIP", "TRIP_NOT_IN_PROGRESS", "ITEMS_NOT_VERIFIED", "PAYMENT_PROVIDER_UNAVAILABLE", "PAYMENT_PROVIDER_ERROR", "PAYMENT_PROVIDER_NOT_CONFIGURED", "NOT_YOUR_TRIP"],
        notes=FLOW,
    ),
)

document(
    DriverTripDeliveryOtpResendView,
    post=doc(
        id="driverResendDeliveryOtp",
        tag=TAG,
        summary="Send the delivery OTP again",
        description="""
For a **paid** cash-on-delivery trip whose OTP never arrived or has expired (it lasts 5 minutes): sends the customer a **new** code (the old one stops working). Limited to
one send every **30 seconds** per trip (`OTP_ALREADY_REQUESTED`, HTTP 429). On a non-production server the response also echoes the code in `otp`.
""",
        auth=DRIVER,
        params=[TRIP_ID],
        by_id=True,
        request=None,
        responses={200: ok(PaymentCollectedSerializer, ex("driver_trip.collect", "Sent again", statuses=[200]))},
        errors=["NOT_COD_TRIP", "TRIP_NOT_IN_PROGRESS", "PAYMENT_NOT_COLLECTED", "OTP_ALREADY_REQUESTED", "NOT_YOUR_TRIP"],
    ),
)
