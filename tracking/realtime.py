"""Server -> Channels-group broadcast helpers, called from synchronous view
and Celery task code. Group naming (`trip_{id}` / `vehicle_{id}`) must match
TrackingConsumer's group_add calls in consumers.py.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def _send(group_name, event_type, payload):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(group_name, {"type": event_type, "payload": payload})


def broadcast_location(trip_id, vehicle_id, payload):
    _send(f"trip_{trip_id}", "location.update", payload)
    _send(f"vehicle_{vehicle_id}", "location.update", payload)


def broadcast_anomaly_alert(trip_id, payload):
    _send(f"trip_{trip_id}", "anomaly.alert", payload)
