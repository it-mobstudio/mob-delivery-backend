import logging
import random
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.choices import (
    CancelledBy,
    ItemVerificationStatus,
    PaymentMode,
    PaymentStatus,
    TripStatus,
    UploadPurpose,
    VehicleTypeStatus,
)
from core.constants import DELIVERY_OTP_RESEND_THROTTLE_SECONDS, DELIVERY_OTP_TTL_SECONDS
from core.exceptions import DomainError
from drivers.services import DriverKycService
from drivers.sms import get_sms_provider
from drivers.wallet import WalletService

from .matching import MatchingService
from .models import Trip, TripItem
from .notifications import trip_notifier
from .payments import PaymentQr, get_payment_provider, to_paise
from .pricing import PricingService
from .routing import RoutingService

logger = logging.getLogger(__name__)

# A code with less than this left is replaced rather than handed to a customer
# who might not get to scan it in time.
QR_MIN_REMAINING = timedelta(seconds=45)

CANCELLABLE_STATUSES = [
    TripStatus.REQUESTED,
    TripStatus.NO_DRIVER_AVAILABLE,
    TripStatus.ASSIGNED,
    TripStatus.ARRIVED_AT_PICKUP,
]


class TripService:
    """Trip booking and lifecycle. Every state-changing method here notifies
    `trip_notifier` (core.observers.Subject) rather than reaching for a
    Kafka client or a notification provider directly — see
    trips.notifications for what's actually listening.
    """

    @staticmethod
    def _require_vehicle_type(vehicle_type, company_id):
        if vehicle_type.company_id != company_id or vehicle_type.status != VehicleTypeStatus.ACTIVE:
            raise DomainError("VEHICLE_TYPE_NOT_FOUND", "This vehicle type is not available.", status_code=404)

    @staticmethod
    def _delivery_otp_cache_key(trip_id):
        return f"trip_delivery_otp:{trip_id}"

    @staticmethod
    def _delivery_otp_throttle_key(trip_id):
        return f"trip_delivery_otp_throttle:{trip_id}"

    @classmethod
    def _issue_delivery_otp(cls, trip):
        """Generates a fresh delivery OTP (replacing any earlier one), texts
        it to the drop contact, and starts the resend throttle. Shared by
        collect_cod_payment (first send) and resend_delivery_otp."""
        otp = f"{random.randint(0, 999999):06d}"
        cache.set(cls._delivery_otp_cache_key(trip.id), otp, timeout=DELIVERY_OTP_TTL_SECONDS)
        cache.set(cls._delivery_otp_throttle_key(trip.id), 1, timeout=DELIVERY_OTP_RESEND_THROTTLE_SECONDS)
        get_sms_provider().send_otp(trip.drop_contact_phone, otp)
        return otp

    @classmethod
    def estimate_trip(cls, company, vehicle_type, pickup_lat, pickup_lng, drop_lat, drop_lng):
        """Route + fare for one vehicle type, without creating a Trip. Used
        by TripEstimateView, which calls this once per active vehicle type
        in the company's fleet so callers can show a Porter-style
        vehicle-type picker.
        """
        cls._require_vehicle_type(vehicle_type, company.id)

        route = RoutingService.get_route(
            pickup_lat, pickup_lng, drop_lat, drop_lng, costing=RoutingService.costing_for_category(vehicle_type.category)
        )
        fare = PricingService.calculate_fare(vehicle_type, route["distance_meters"], route["duration_seconds"], pickup_lat, pickup_lng)
        return {**route, **fare}

    @classmethod
    @transaction.atomic
    def create_trip(
        cls,
        company,
        vehicle_type,
        pickup,
        drop,
        payment_mode,
        reference_id="",
        invoice_url="",
        invoice_number="",
        verify_items=False,
        items=None,
    ):
        """Creates a Trip priced against a freshly-computed route, then
        makes one synchronous attempt to assign the nearest available
        driver. If nobody's available the trip is created anyway (status
        NO_DRIVER_AVAILABLE) — the caller sees that in the response and
        can retry via TripService.retry_assignment.

        payment_mode=PREPAID is taken as already settled outside this
        system, so payment_status is set to paid immediately. COD starts
        pending and is only settled via collect_cod_payment, right before
        driver_complete.

        `items` (dicts: name, quantity, ...) and `invoice_url` describe the
        goods; with `verify_items` the driver has to confirm every item at the
        drop — see verify_item.
        """
        cls._require_vehicle_type(vehicle_type, company.id)

        route = RoutingService.get_route(
            pickup["lat"], pickup["lng"], drop["lat"], drop["lng"],
            costing=RoutingService.costing_for_category(vehicle_type.category),
        )
        fare = PricingService.calculate_fare(
            vehicle_type, route["distance_meters"], route["duration_seconds"], pickup["lat"], pickup["lng"]
        )

        trip = Trip.objects.create(
            company=company,
            vehicle_type=vehicle_type,
            reference_id=reference_id,
            payment_mode=payment_mode,
            payment_status=PaymentStatus.PAID if payment_mode == PaymentMode.PREPAID else PaymentStatus.PENDING,
            pickup_address=pickup["address"],
            pickup_lat=pickup["lat"],
            pickup_lng=pickup["lng"],
            pickup_contact_name=pickup.get("contact_name", ""),
            pickup_contact_phone=pickup.get("contact_phone", ""),
            drop_address=drop["address"],
            drop_lat=drop["lat"],
            drop_lng=drop["lng"],
            drop_contact_name=drop.get("contact_name", ""),
            drop_contact_phone=drop.get("contact_phone", ""),
            distance_meters=route["distance_meters"],
            duration_seconds=route["duration_seconds"],
            route_polyline=route["polyline"],
            polyline_precision=route["polyline_precision"],
            base_fare=fare["base_fare"],
            distance_fare=fare["distance_fare"],
            time_fare=fare["time_fare"],
            surge_multiplier=fare["surge_multiplier"],
            total_fare=fare["total_fare"],
            currency=fare["currency"],
            invoice_url=invoice_url,
            invoice_number=invoice_number,
            verify_items=verify_items,
        )
        TripItem.objects.bulk_create(
            [
                TripItem(company=company, trip=trip, position=position, **item)
                for position, item in enumerate(items or [])
            ]
        )

        trip_notifier.notify("trip.created", trip)
        MatchingService.try_assign_driver(trip)
        trip_notifier.notify("trip.assigned" if trip.status == TripStatus.ASSIGNED else "trip.no_driver_available", trip)

        return trip

    @staticmethod
    def retry_assignment(trip):
        if trip.status != TripStatus.NO_DRIVER_AVAILABLE:
            raise DomainError(
                "TRIP_NOT_RETRYABLE", "Only a trip with no driver available can be (re)assigned.", status_code=409
            )
        MatchingService.try_assign_driver(trip)
        trip_notifier.notify("trip.assigned" if trip.status == TripStatus.ASSIGNED else "trip.no_driver_available", trip)
        return trip

    @staticmethod
    def cancel_trip(trip, reason, cancelled_by):
        if trip.status not in CANCELLABLE_STATUSES:
            raise DomainError(
                "TRIP_NOT_CANCELLABLE", f"A trip in status '{trip.status}' cannot be cancelled.", status_code=409
            )
        trip.status = TripStatus.CANCELLED
        trip.cancellation_reason = reason
        trip.cancelled_by = cancelled_by
        trip.cancelled_at = timezone.now()
        trip.save(update_fields=["status", "cancellation_reason", "cancelled_by", "cancelled_at", "updated_at"])
        trip_notifier.notify("trip.cancelled", trip)
        return trip

    @classmethod
    def generate_payment_qr(cls, trip):
        """The scan-to-pay code for a COD trip's fare — one per trip. Opening the
        payment screen twice shows the same live code rather than minting a new
        one; an expired one is replaced. Doesn't mark anything as paid.

        If the customer has in fact already paid (their webhook was late, or the
        driver reopened the screen after paying) that's discovered here and
        confirmed, and the caller is told ALREADY_PAID.
        """
        if trip.payment_mode != PaymentMode.COD:
            raise DomainError("NOT_COD_TRIP", "This trip is not COD; there's nothing to collect.", status_code=409)
        if trip.payment_status == PaymentStatus.PAID:
            raise DomainError("ALREADY_PAID", "This trip has already been paid.", status_code=409)
        if trip.status != TripStatus.IN_PROGRESS:
            raise DomainError(
                "TRIP_NOT_IN_PROGRESS", "The payment code is shown at the drop, once the delivery has started.", status_code=409
            )
        # Verify-then-pay: no code to scan until the items have been checked.
        cls._require_items_verified(trip)

        provider = get_payment_provider()
        has_code = trip.payment_qr_id and trip.payment_provider == provider.name
        if provider.verifies_payments and has_code:
            received = provider.find_payment(trip)
            if received is not None:
                cls.confirm_payment(trip, reference=received.reference)
                raise DomainError("ALREADY_PAID", "This trip has already been paid.", status_code=409)
            live = trip.payment_qr_expires_at and trip.payment_qr_expires_at - timezone.now() > QR_MIN_REMAINING
            if live:
                return PaymentQr(
                    provider=trip.payment_provider,
                    amount=trip.total_fare,
                    currency=trip.currency,
                    reference=trip.payment_qr_id,
                    image_url=trip.payment_qr_image_url or None,
                    expires_at=trip.payment_qr_expires_at,
                )

        qr = provider.create_qr(trip)
        trip.payment_provider = qr.provider
        trip.payment_qr_id = qr.reference
        trip.payment_qr_image_url = qr.image_url or ""
        trip.payment_qr_expires_at = qr.expires_at
        trip.save(
            update_fields=[
                "payment_provider",
                "payment_qr_id",
                "payment_qr_image_url",
                "payment_qr_expires_at",
                "updated_at",
            ]
        )
        return qr

    @classmethod
    @transaction.atomic
    def confirm_payment(cls, trip, *, reference=""):
        """The customer has paid a COD trip: mark it paid and text them their
        delivery OTP. Called from both places that learn of a payment — the
        provider's webhook and the driver asking us to check — and safe to call
        twice (whichever arrives second changes nothing and sends nothing).
        Returns (trip, otp); otp is None when it had already been confirmed.
        """
        trip = Trip.objects.select_for_update().get(pk=trip.pk)
        if trip.payment_status == PaymentStatus.PAID:
            return trip, None

        trip.payment_status = PaymentStatus.PAID
        trip.cod_collected_at = timezone.now()
        trip.payment_reference = reference
        trip.save(update_fields=["payment_status", "cod_collected_at", "payment_reference", "updated_at"])

        otp = cls._issue_delivery_otp(trip)
        trip_notifier.notify("trip.payment_collected", trip)
        return trip, otp

    @classmethod
    def collect_cod_payment(cls, trip, driver):
        """The driver says the customer has paid a COD trip. With a payment
        provider that can verify (Razorpay) we don't take their word for it: the
        provider is asked whether the code was paid, and if not the driver is
        told to wait. Either way, once paid the customer is texted a delivery
        OTP — driver_complete won't finalize the trip without it, so completion
        can't happen before payment does.
        """
        if trip.driver_id != driver.id:
            raise DomainError("NOT_YOUR_TRIP", "This trip is not assigned to you.", status_code=403)
        if trip.payment_mode != PaymentMode.COD:
            raise DomainError("NOT_COD_TRIP", "This trip is not COD; there's nothing to collect.", status_code=409)
        if trip.payment_status == PaymentStatus.PAID:
            raise DomainError("ALREADY_PAID", "This trip has already been paid.", status_code=409)
        if trip.status != TripStatus.IN_PROGRESS:
            raise DomainError(
                "TRIP_NOT_IN_PROGRESS", "Payment can only be collected while the trip is in progress.", status_code=409
            )
        cls._require_items_verified(trip)

        reference = ""
        provider = get_payment_provider()
        if provider.verifies_payments:
            received = provider.find_payment(trip) if trip.payment_qr_id and trip.payment_provider == provider.name else None
            if received is None:
                raise DomainError(
                    "PAYMENT_NOT_RECEIVED",
                    "The customer's payment hasn't arrived yet. Ask them to scan the code and pay, then check again in a moment.",
                    status_code=409,
                )
            reference = received.reference

        trip, otp = cls.confirm_payment(trip, reference=reference)
        return trip, otp

    @classmethod
    def apply_razorpay_event(cls, event):
        """Acts on one (already signature-verified) Razorpay webhook event and
        says what it did — `paid`, or why not — for the log and the tests.
        Everything but a QR credit is ignored; the answer is always a 200 so
        Razorpay doesn't keep retrying something we've understood.
        """
        if event.get("event") != "qr_code.credited":
            return "ignored"
        payload = event.get("payload") or {}
        qr = ((payload.get("qr_code") or {}).get("entity")) or {}
        payment = ((payload.get("payment") or {}).get("entity")) or {}

        trip = Trip.objects.filter(payment_qr_id=qr.get("id") or "", payment_provider="razorpay").first()
        if trip is None:
            logger.warning("Razorpay credited an unknown QR code: %s", qr.get("id"))
            return "unknown_qr"
        if trip.payment_status == PaymentStatus.PAID:
            return "already_paid"
        if trip.status != TripStatus.IN_PROGRESS:
            # Money arrived for a trip that is no longer on the road (cancelled
            # meanwhile). Nothing to mark paid; a person has to refund it.
            logger.error("Razorpay payment %s arrived for trip %s in status %s", payment.get("id"), trip.id, trip.status)
            return "trip_not_in_progress"

        received = payment.get("amount")
        if received is None:
            received = qr.get("payments_amount_received")
        if received is None or int(received) < to_paise(trip.total_fare):
            logger.warning("Razorpay payment for trip %s is short: %s of %s paise", trip.id, received, to_paise(trip.total_fare))
            return "underpaid"

        cls.confirm_payment(trip, reference=payment.get("id") or "")
        return "paid"

    @classmethod
    def resend_delivery_otp(cls, trip, driver):
        """Sends a new delivery OTP for a COD trip whose payment is already
        collected. Needed because collect_cod_payment is one-shot (a second
        call is rejected as ALREADY_PAID) while the OTP it sends expires
        after DELIVERY_OTP_TTL_SECONDS — without this, an OTP that lapsed or
        never arrived would leave a paid trip impossible to finish."""
        if trip.driver_id != driver.id:
            raise DomainError("NOT_YOUR_TRIP", "This trip is not assigned to you.", status_code=403)
        if trip.payment_mode != PaymentMode.COD:
            raise DomainError("NOT_COD_TRIP", "This trip is not COD; there's no delivery OTP.", status_code=409)
        if trip.status != TripStatus.IN_PROGRESS:
            raise DomainError(
                "TRIP_NOT_IN_PROGRESS", "The delivery OTP can only be resent while the trip is in progress.", status_code=409
            )
        if trip.payment_status != PaymentStatus.PAID:
            raise DomainError(
                "PAYMENT_NOT_COLLECTED", "Collect the COD payment first; that's what sends the OTP.", status_code=409
            )

        if cache.get(cls._delivery_otp_throttle_key(trip.id)) is not None:
            raise DomainError(
                "OTP_ALREADY_REQUESTED",
                "An OTP was sent for this trip recently. Please wait before requesting another.",
                status_code=429,
            )
        return cls._issue_delivery_otp(trip)

    @staticmethod
    def _transition(trip, *, from_status, to_status, timestamp_field, event_type, driver=None):
        if trip.status != from_status:
            raise DomainError(
                "INVALID_TRIP_STATUS_TRANSITION",
                f"Trip must be '{from_status}' to do this (currently '{trip.status}').",
                status_code=409,
            )
        if driver is not None and trip.driver_id != driver.id:
            raise DomainError("NOT_YOUR_TRIP", "This trip is not assigned to you.", status_code=403)

        trip.status = to_status
        setattr(trip, timestamp_field, timezone.now())
        trip.save(update_fields=["status", timestamp_field, "updated_at"])
        trip_notifier.notify(event_type, trip)
        return trip

    @classmethod
    def driver_arrive(cls, trip, driver):
        return cls._transition(
            trip,
            from_status=TripStatus.ASSIGNED,
            to_status=TripStatus.ARRIVED_AT_PICKUP,
            timestamp_field="arrived_at_pickup_at",
            event_type="trip.arrived_at_pickup",
            driver=driver,
        )

    @classmethod
    def driver_start(cls, trip, driver):
        return cls._transition(
            trip,
            from_status=TripStatus.ARRIVED_AT_PICKUP,
            to_status=TripStatus.IN_PROGRESS,
            timestamp_field="started_at",
            event_type="trip.started",
            driver=driver,
        )

    @classmethod
    def driver_complete(cls, trip, driver, otp=None):
        """For a COD trip, payment must already be collected and the
        delivery OTP sent by collect_cod_payment must be supplied here —
        that's what actually finalizes the order. Prepaid trips complete
        the same way they always did, no OTP required.

        Completing pays the driver: their share of the fare goes into their
        wallet in the same transaction, so a completed trip and its earning
        can't get out of step.
        """
        cls._require_items_verified(trip)
        if trip.payment_mode == PaymentMode.COD:
            if trip.payment_status != PaymentStatus.PAID:
                raise DomainError(
                    "PAYMENT_NOT_COLLECTED", "Collect the COD payment before completing this trip.", status_code=409
                )
            cache_key = cls._delivery_otp_cache_key(trip.id)
            stored_otp = cache.get(cache_key)
            if not otp or stored_otp is None or stored_otp != otp:
                raise DomainError(
                    "INVALID_DELIVERY_OTP", "The delivery OTP is invalid or has expired.", status_code=400
                )
            cache.delete(cache_key)

        with transaction.atomic():
            trip = cls._transition(
                trip,
                from_status=TripStatus.IN_PROGRESS,
                to_status=TripStatus.COMPLETED,
                timestamp_field="completed_at",
                event_type="trip.completed",
                driver=driver,
            )
            WalletService.credit_trip_earning(trip)
        return trip

    # -- delivery verification --------------------------------------------------

    @staticmethod
    def _require_items_verified(trip):
        """A trip booked with verify_items can't be paid for or finished until
        the driver has answered for every item (delivered, or not delivered
        with a reason)."""
        if trip.verify_items and trip.items.filter(status=ItemVerificationStatus.PENDING).exists():
            raise DomainError(
                "ITEMS_NOT_VERIFIED", "Verify every item on this order before continuing.", status_code=409
            )

    @staticmethod
    def _item_for_verification(trip, driver, item_id):
        if trip.driver_id != driver.id:
            raise DomainError("NOT_YOUR_TRIP", "This trip is not assigned to you.", status_code=403)
        if not trip.verify_items:
            raise DomainError(
                "VERIFICATION_NOT_REQUESTED", "This order doesn't need its items verified.", status_code=409
            )
        if trip.status != TripStatus.IN_PROGRESS:
            raise DomainError(
                "TRIP_NOT_IN_PROGRESS", "Items are verified at the drop, once the delivery has started.", status_code=409
            )
        try:
            return trip.items.get(pk=item_id)
        except TripItem.DoesNotExist:
            raise DomainError("ITEM_NOT_FOUND", "That item isn't on this order.", status_code=404)

    @classmethod
    def verify_item(cls, trip, driver, item_id, status, note="", photo=None):
        """The driver's word on one item — delivered, or not delivered (with
        why) — optionally with a photo taken on the spot. Saying it again
        replaces the earlier answer (a mis-tap is fixable until the trip is
        completed); a new photo replaces the old one, no photo keeps it."""
        item = cls._item_for_verification(trip, driver, item_id)
        if photo is not None:
            item.proof_image_url = DriverKycService.store_file(photo, driver, UploadPurpose.DELIVERY_PROOF)
        item.status = status
        item.driver_note = note
        item.verified_at = timezone.now()
        item.verified_by = driver
        item.save()
        trip_notifier.notify("trip.item_verified", trip)
        return item

    @classmethod
    def reset_item(cls, trip, driver, item_id):
        """Takes an item back to pending (and drops its photo and note)."""
        item = cls._item_for_verification(trip, driver, item_id)
        item.status = ItemVerificationStatus.PENDING
        item.driver_note = ""
        item.proof_image_url = ""
        item.verified_at = None
        item.verified_by = None
        item.save()
        return item

    @classmethod
    def driver_cancel(cls, trip, driver, reason):
        if trip.driver_id != driver.id:
            raise DomainError("NOT_YOUR_TRIP", "This trip is not assigned to you.", status_code=403)
        return cls.cancel_trip(trip, reason, CancelledBy.DRIVER)

    @classmethod
    def navigation_route(cls, trip, driver, lat, lng):
        """The leg the driver still has to drive, from wherever they are now
        (lat/lng) to the trip's next stop: pickup until they've picked up,
        drop after. trip.route_polyline only covers pickup -> drop, so this
        is what the app draws for "get me to the pickup" — the part of the
        journey a booking-time route can't know about.
        """
        if trip.driver_id != driver.id:
            raise DomainError("NOT_YOUR_TRIP", "This trip is not assigned to you.", status_code=403)

        if trip.status in (TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP):
            target, target_lat, target_lng = "pickup", trip.pickup_lat, trip.pickup_lng
        elif trip.status == TripStatus.IN_PROGRESS:
            target, target_lat, target_lng = "drop", trip.drop_lat, trip.drop_lng
        else:
            raise DomainError(
                "TRIP_NOT_ACTIVE", f"A trip in status '{trip.status}' has nothing left to navigate.", status_code=409
            )

        route = RoutingService.get_route(
            lat, lng, target_lat, target_lng, costing=RoutingService.costing_for_category(trip.vehicle_type.category)
        )
        return {"target": target, "target_lat": float(target_lat), "target_lng": float(target_lng), **route}

    @staticmethod
    def driver_stats(driver, since=None):
        """Completed/cancelled counts and totals for a driver, from `since` (a
        datetime) onwards, or all-time if None. `total_fare` is the fare value
        of the trips they completed; `earnings` is what they personally made
        (from the wallet ledger — see drivers.wallet).
        """
        completed = Trip.objects.filter(driver=driver, status=TripStatus.COMPLETED)
        cancelled = Trip.objects.filter(driver=driver, status=TripStatus.CANCELLED)
        if since is not None:
            completed = completed.filter(completed_at__gte=since)
            cancelled = cancelled.filter(cancelled_at__gte=since)

        # Aggregate aliases must not shadow a Trip field name (Django refuses
        # `Sum("total_fare")` under an alias of `total_fare`), hence the
        # `sum_`/`count_` prefixes.
        totals = completed.aggregate(
            count_trips=Count("id"),
            sum_fare=Sum("total_fare"),
            sum_distance=Sum("distance_meters"),
            sum_cod=Sum("total_fare", filter=Q(payment_mode=PaymentMode.COD)),
        )
        return {
            "trips_completed": totals["count_trips"] or 0,
            "trips_cancelled": cancelled.count(),
            "total_fare": f"{totals['sum_fare'] or 0:.2f}",
            "earnings": f"{WalletService.earnings_since(driver, since):.2f}",
            "cod_collected": f"{totals['sum_cod'] or 0:.2f}",
            "distance_meters": totals["sum_distance"] or 0,
        }
