import random

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.choices import CancelledBy, PaymentMode, PaymentStatus, TripStatus, VehicleTypeStatus
from core.constants import DELIVERY_OTP_TTL_SECONDS
from core.exceptions import DomainError
from drivers.sms import get_sms_provider

from .matching import MatchingService
from .models import Trip
from .notifications import trip_notifier
from .payments import get_payment_provider
from .pricing import PricingService
from .routing import RoutingService

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
    def create_trip(cls, company, vehicle_type, pickup, drop, payment_mode, reference_id=""):
        """Creates a Trip priced against a freshly-computed route, then
        makes one synchronous attempt to assign the nearest available
        driver. If nobody's available the trip is created anyway (status
        NO_DRIVER_AVAILABLE) — the caller sees that in the response and
        can retry via TripService.retry_assignment.

        payment_mode=PREPAID is taken as already settled outside this
        system, so payment_status is set to paid immediately. COD starts
        pending and is only settled via collect_cod_payment, right before
        driver_complete.
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
        """QR payload for the customer to scan and pay a COD trip's fare —
        read-only, doesn't mark anything as paid (that's collect_cod_payment,
        which the driver calls after the customer has actually paid)."""
        if trip.payment_mode != PaymentMode.COD:
            raise DomainError("NOT_COD_TRIP", "This trip is not COD; there's nothing to collect.", status_code=409)
        if trip.payment_status == PaymentStatus.PAID:
            raise DomainError("ALREADY_PAID", "This trip has already been paid.", status_code=409)
        return get_payment_provider().generate_qr(trip)

    @classmethod
    def collect_cod_payment(cls, trip, driver):
        """Driver-confirmed "the customer just paid" for a COD trip. Sends a
        one-time delivery OTP to the drop contact — driver_complete below
        won't finalize the trip without it, so completion can't happen
        before payment does.
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

        trip.payment_status = PaymentStatus.PAID
        trip.cod_collected_at = timezone.now()
        trip.save(update_fields=["payment_status", "cod_collected_at", "updated_at"])

        otp = f"{random.randint(0, 999999):06d}"
        cache.set(cls._delivery_otp_cache_key(trip.id), otp, timeout=DELIVERY_OTP_TTL_SECONDS)
        get_sms_provider().send_otp(trip.drop_contact_phone, otp)

        trip_notifier.notify("trip.payment_collected", trip)
        return trip, otp

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
        """
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

        return cls._transition(
            trip,
            from_status=TripStatus.IN_PROGRESS,
            to_status=TripStatus.COMPLETED,
            timestamp_field="completed_at",
            event_type="trip.completed",
            driver=driver,
        )

    @classmethod
    def driver_cancel(cls, trip, driver, reason):
        if trip.driver_id != driver.id:
            raise DomainError("NOT_YOUR_TRIP", "This trip is not assigned to you.", status_code=403)
        return cls.cancel_trip(trip, reason, CancelledBy.DRIVER)
