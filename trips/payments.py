from urllib.parse import urlencode

from django.conf import settings


class PaymentProvider:
    """Pluggable interface for collecting a COD trip's payment. Swap in a
    real gateway (Razorpay, Cashfree, PhonePe, ...) later by adding one
    concrete subclass and pointing get_payment_provider() at it — the COD
    flow itself (TripService.generate_payment_qr / collect_cod_payment)
    doesn't change.
    """

    def generate_qr(self, trip):
        """Returns {"qr_payload": str, "amount": Decimal, "currency": str}
        describing how the customer should pay trip.total_fare."""
        raise NotImplementedError


class UpiDeepLinkPaymentProvider(PaymentProvider):
    """Stand-in used until a real payment gateway is chosen — same role as
    drivers.sms.LogSmsProvider. Builds a standard UPI deep link
    (upi://pay?...) against the company's own VPA; the driver app renders
    qr_payload as a QR code for the customer to scan with any UPI app.
    There's no gateway account, webhook, or transaction id behind this, so
    payment isn't verified automatically — confirmation is the driver
    tapping "payment received" (TripService.collect_cod_payment).
    """

    def generate_qr(self, trip):
        params = {
            "pa": settings.COMPANY_UPI_VPA,
            "pn": settings.COMPANY_UPI_PAYEE_NAME,
            "am": str(trip.total_fare),
            "cu": trip.currency,
            "tn": f"Trip {trip.id}",
        }
        qr_payload = f"upi://pay?{urlencode(params)}"
        return {"qr_payload": qr_payload, "amount": trip.total_fare, "currency": trip.currency}


def get_payment_provider():
    return UpiDeepLinkPaymentProvider()
