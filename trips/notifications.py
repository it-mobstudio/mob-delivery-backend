"""Trip lifecycle side effects, via the Observer pattern (core.observers):
TripService.notify(...) fires one event into `trip_notifier`, which fans it
out to every attached Observer below. Adding a new side effect (a push
notification provider, a real invoicing engine, a webhook) means writing
one more Observer and attaching it — TripService itself never changes.

None of this is allowed to raise back into the booking/lifecycle request —
a failing notification must never fail the trip action that triggered it.
Observer.update() implementations therefore catch and log their own
errors.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone

from core.observers import Observer, Subject

logger = logging.getLogger(__name__)


class DriverNotificationObserver(Observer):
    """Stand-in for a real push-notification provider (FCM/APNs), same role
    as drivers.sms.LogSmsProvider — logs instead of sending until a
    provider is chosen. Swap in a real implementation without touching
    TripService or any other observer.
    """

    NOTIFIABLE_EVENTS = {
        "trip.assigned",
        "trip.arrived_at_pickup",
        "trip.started",
        "trip.completed",
        "trip.cancelled",
    }

    def update(self, event_type, trip):
        if event_type not in self.NOTIFIABLE_EVENTS or trip.driver_id is None:
            return
        try:
            logger.info(
                "Push notification stand-in: would notify driver %s of %s for trip %s",
                trip.driver_id, event_type, trip.id,
            )
        except Exception:
            logger.exception("DriverNotificationObserver failed for %s on trip %s", event_type, trip.id)


class TripInvoiceObserver(Observer):
    """Stand-in for real invoice generation (line items, PDF, totals) — logs
    the fare summary that a completed trip would be invoiced for. No
    invoicing/billing model exists yet; this is the extension point for one.
    """

    def update(self, event_type, trip):
        if event_type != "trip.completed":
            return
        try:
            logger.info(
                "Invoice stand-in: trip %s completed, total_fare=%s %s",
                trip.id, trip.total_fare, trip.currency,
            )
        except Exception:
            logger.exception("TripInvoiceObserver failed for trip %s", trip.id)


class KafkaEventObserver(Observer):
    """Publishes every trip event to KAFKA_TRIP_EVENTS_TOPIC, keyed by trip
    id (so a consumer partitioned by key sees a given trip's events in
    order), for other services (analytics, partner webhooks, live
    tracking) to consume. Lazily connects; disabled entirely unless
    KAFKA_ENABLED=True (see mob_delivery/settings.py) so local dev doesn't
    need a broker running.
    """

    def __init__(self):
        self._producer = None
        self._init_failed = False

    def _get_producer(self):
        if not settings.KAFKA_ENABLED or self._init_failed:
            return None
        if self._producer is not None:
            return self._producer

        try:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8"),
            )
        except Exception:
            logger.exception("Could not initialize Kafka producer — trip events will not be published.")
            self._init_failed = True
            return None

        return self._producer

    def update(self, event_type, trip):
        producer = self._get_producer()
        if producer is None:
            return

        payload = {
            "event_type": event_type,
            "trip_id": str(trip.id),
            "company_id": str(trip.company_id),
            "reference_id": trip.reference_id,
            "status": trip.status,
            "driver_id": str(trip.driver_id) if trip.driver_id else None,
            "vehicle_id": str(trip.vehicle_id) if trip.vehicle_id else None,
            "occurred_at": timezone.now().isoformat(),
        }

        try:
            producer.send(settings.KAFKA_TRIP_EVENTS_TOPIC, key=str(trip.id), value=payload)
        except Exception:
            logger.exception("Failed to publish Kafka event %s for trip %s", event_type, trip.id)


trip_notifier = Subject()
trip_notifier.attach(DriverNotificationObserver())
trip_notifier.attach(TripInvoiceObserver())
trip_notifier.attach(KafkaEventObserver())
