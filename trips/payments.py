"""Taking payment for a cash-on-delivery trip: making the "scan to pay" QR and
finding out whether the customer has paid it.

Two providers, chosen by settings.PAYMENT_PROVIDER:

* ``razorpay`` — a **single-use, fixed-amount UPI QR created by Razorpay** for
  each trip (QR Codes API). Razorpay knows when it has been paid, so the payment
  is confirmed by Razorpay (its signed webhook, or a server-side check when the
  driver asks) and never on the driver's word alone.
* ``upi_static`` — a plain UPI deep link to the company's own VPA. Nothing sits
  behind it, so nothing can confirm the payment. Local development only.

Razorpay API used (https://razorpay.com/docs/api/qr-codes/):
``POST /payments/qr_codes`` to create the code and
``GET /payments/qr_codes/{id}/payments`` to list what was paid against it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

from core.exceptions import DomainError

logger = logging.getLogger(__name__)

RAZORPAY = "razorpay"
UPI_STATIC = "upi_static"

# Razorpay refuses a QR that closes sooner than this.
RAZORPAY_MIN_QR_MINUTES = 2


@dataclass(frozen=True)
class PaymentQr:
    """A scan-to-pay code for one trip. Show `image_url` when there is one
    (Razorpay hosts the finished QR image); otherwise draw `payload` — a UPI
    deep link — as a QR yourself."""

    provider: str
    amount: Decimal
    currency: str
    reference: str = ""  # the provider's id for this code
    payload: str | None = None
    image_url: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ReceivedPayment:
    reference: str  # the provider's id for the payment (a Razorpay `pay_...`)
    amount: Decimal


def to_paise(amount):
    """Rupees → the integer number of paise Razorpay's API speaks in."""
    return int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class PaymentProvider:
    name = ""

    #: True when the provider can tell us a payment happened. When False, the
    #: driver's "received" tap is all the evidence there is.
    verifies_payments = False

    def create_qr(self, trip):
        raise NotImplementedError

    def find_payment(self, trip):
        """A payment against this trip's code covering its fare, or None."""
        raise NotImplementedError


class UpiStaticPaymentProvider(PaymentProvider):
    """Stand-in for local development. A standard UPI deep link (upi://pay?...)
    against the company's own VPA; there is no gateway, webhook or transaction
    id behind it, so a payment can't be verified — the driver confirms it."""

    name = UPI_STATIC

    def create_qr(self, trip):
        params = {
            "pa": settings.COMPANY_UPI_VPA,
            "pn": settings.COMPANY_UPI_PAYEE_NAME,
            "am": str(trip.total_fare),
            "cu": trip.currency,
            "tn": f"Trip {trip.id}",
        }
        return PaymentQr(
            provider=self.name,
            amount=trip.total_fare,
            currency=trip.currency,
            payload=f"upi://pay?{urlencode(params)}",
        )

    def find_payment(self, trip):
        return None


class RazorpayPaymentProvider(PaymentProvider):
    name = RAZORPAY
    verifies_payments = True

    def _call(self, method, path, **kwargs):
        url = f"{settings.RAZORPAY_API_BASE.rstrip('/')}{path}"
        try:
            response = requests.request(
                method,
                url,
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
                timeout=settings.RAZORPAY_TIMEOUT_SECONDS,
                **kwargs,
            )
            print("********")
            print(response.json())
            print("********")

        except requests.RequestException:
            logger.exception("Razorpay request failed (%s %s)", method, path)
            raise DomainError(
                "PAYMENT_PROVIDER_UNAVAILABLE",
                "Couldn't reach the payment provider. Please try again in a moment.",
                status_code=503,
            )

        if response.status_code in (401, 403):
            logger.error("Razorpay rejected this server's API keys (HTTP %s)", response.status_code)
            raise DomainError(
                "PAYMENT_PROVIDER_NOT_CONFIGURED",
                "The payment provider rejected this server's API keys.",
                status_code=503,
            )
        if response.status_code >= 500:
            logger.error("Razorpay is failing (HTTP %s on %s %s)", response.status_code, method, path)
            raise DomainError(
                "PAYMENT_PROVIDER_UNAVAILABLE",
                "The payment provider is having trouble. Please try again in a moment.",
                status_code=503,
            )
        if response.status_code >= 400:
            description = self._describe(response)
            logger.warning("Razorpay refused %s %s: %s", method, path, description)
            if "requested url was not found" in description.lower():
                # What Razorpay answers for a product that isn't switched on for the
                # account: the keys are fine, but QR Codes is an on-demand feature.
                raise DomainError(
                    "PAYMENT_PROVIDER_NOT_CONFIGURED",
                    "Razorpay QR Codes isn't activated on this Razorpay account yet. "
                    "Ask Razorpay support to enable it (it is an on-demand feature), then try again.",
                    status_code=503,
                )
            raise DomainError(
                "PAYMENT_PROVIDER_ERROR", f"The payment provider refused the request: {description}", status_code=502
            )

        try:
            return response.json()
        except ValueError:
            raise DomainError(
                "PAYMENT_PROVIDER_ERROR", "The payment provider sent an answer we couldn't read.", status_code=502
            )

    @staticmethod
    def _describe(response):
        try:
            return response.json()["error"]["description"]
        except (ValueError, KeyError, TypeError):
            return f"HTTP {response.status_code}"

    def create_qr(self, trip):
        if trip.currency != "INR":
            raise DomainError(
                "PAYMENT_PROVIDER_ERROR", "UPI QR payments are in rupees only.", status_code=422
            )
        minutes = max(RAZORPAY_MIN_QR_MINUTES, settings.RAZORPAY_QR_VALID_MINUTES)
        expires_at = timezone.now() + timedelta(minutes=minutes)
        data = self._call(
            "POST",
            "/payments/qr_codes",
            json={
                "type": "upi_qr",
                "name": f"Trip {str(trip.id)[:8]}",
                # One code per trip, for exactly this fare: it can be paid once
                # and Razorpay closes it. A leftover screenshot can't be reused.
                "usage": "single_use",
                "fixed_amount": True,
                "payment_amount": to_paise(trip.total_fare),
                "description": f"Delivery {trip.reference_id or str(trip.id)[:8]}"[:255],
                "close_by": int(expires_at.timestamp()),
                "notes": {
                    "trip_id": str(trip.id),
                    "company_id": str(trip.company_id),
                    "reference_id": trip.reference_id,
                },
            },
        )
        try:
            reference, image_url = data["id"], data["image_url"]
        except (KeyError, TypeError):
            raise DomainError(
                "PAYMENT_PROVIDER_ERROR", "The payment provider sent an answer we couldn't read.", status_code=502
            )
        return PaymentQr(
            provider=self.name,
            amount=trip.total_fare,
            currency=trip.currency,
            reference=reference,
            image_url=image_url,
            expires_at=expires_at,
        )

    def find_payment(self, trip):
        data = self._call("GET", f"/payments/qr_codes/{trip.payment_qr_id}/payments")
        needed = to_paise(trip.total_fare)
        for payment in data.get("items", []) or []:
            if payment.get("status") == "captured" and int(payment.get("amount") or 0) >= needed:
                return ReceivedPayment(reference=payment["id"], amount=Decimal(payment["amount"]) / 100)
        return None


def get_payment_provider():
    name = settings.PAYMENT_PROVIDER
    if name == RAZORPAY:
        if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
            raise DomainError(
                "PAYMENT_PROVIDER_NOT_CONFIGURED",
                "Online payments aren't set up on this server yet.",
                status_code=503,
            )
        return RazorpayPaymentProvider()
    if name == UPI_STATIC:
        return UpiStaticPaymentProvider()
    raise DomainError(
        "PAYMENT_PROVIDER_NOT_CONFIGURED", f"Unknown payment provider '{name}'.", status_code=503
    )
