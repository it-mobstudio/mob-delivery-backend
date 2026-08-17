from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer

from accounts.models import AdminUser
from core.ws_auth import authenticate_ws_token
from trips.models import Trip
from vehicles.models import Vehicle


class TrackingConsumer(JsonWebsocketConsumer):
    """Admin Panel live-tracking feed.

    Clients connect to either ws/tracking/trip/<trip_id>/ or
    ws/tracking/vehicle/<vehicle_id>/ and are joined to the matching
    `trip_{id}` / `vehicle_{id}` Channels group (see realtime.py, which
    broadcasts into these same group names).

    Channels doesn't run DRF authentication classes, so auth is done by hand
    here, reusing JWTMultiPrincipalAuthentication's token validation and
    principal resolution rather than re-implementing it. The access token is
    passed as a `?token=` query param since browser WebSocket clients can't
    set custom headers.
    """

    def connect(self):
        user = authenticate_ws_token(self.scope)
        if user is None or not isinstance(user, AdminUser):
            self.close(code=4401)
            return

        group_name = self._resolve_group(user)
        if group_name is None:
            self.close(code=4404)
            return

        self.group_name = group_name
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)
        self.accept()

    def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            async_to_sync(self.channel_layer.group_discard)(self.group_name, self.channel_name)

    def _resolve_group(self, user):
        kwargs = self.scope["url_route"]["kwargs"]
        if "trip_id" in kwargs:
            if not Trip.objects.filter(pk=kwargs["trip_id"], company_id=user.company_id).exists():
                return None
            return f"trip_{kwargs['trip_id']}"
        if "vehicle_id" in kwargs:
            if not Vehicle.objects.filter(pk=kwargs["vehicle_id"], company_id=user.company_id).exists():
                return None
            return f"vehicle_{kwargs['vehicle_id']}"
        return None

    # Channels dispatch convention: an event's "type" ("location.update")
    # maps to a method name with dots replaced by underscores.
    def location_update(self, event):
        self.send_json(event["payload"])

    def anomaly_alert(self, event):
        self.send_json(event["payload"])
