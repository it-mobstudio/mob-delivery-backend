from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def sos_group_name(company_id):
    # Company-scoped, not a single global "sos_alerts" group — every other
    # broadcast in this system is tenant-scoped (trip_{id}/vehicle_{id}
    # groups are implicitly scoped via the trip/vehicle's own company), and
    # an SOS alert is exactly the kind of event that must never leak across
    # tenants. The module spec's "sos_alerts" group name is used as a prefix.
    return f"sos_alerts_{company_id}"


def broadcast_sos_alert(company_id, payload):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(sos_group_name(company_id), {"type": "sos.alert", "payload": payload})
