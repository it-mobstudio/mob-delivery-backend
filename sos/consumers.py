from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer

from accounts.models import AdminUser
from core.ws_auth import authenticate_ws_token

from .realtime import sos_group_name


class SosConsumer(JsonWebsocketConsumer):
    """Admin Panel SOS feed — every connected AdminUser for a company joins
    the same `sos_alerts_{company_id}` group (see realtime.py) on connect,
    regardless of which trip/vehicle they're viewing. Deliberately not
    scoped to a single trip/vehicle: an SOS alert must interrupt whatever
    the admin is currently looking at, not wait for them to be on the right
    screen.
    """

    def connect(self):
        user = authenticate_ws_token(self.scope)
        if user is None or not isinstance(user, AdminUser):
            self.close(code=4401)
            return

        self.group_name = sos_group_name(user.company_id)
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)
        self.accept()

    def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            async_to_sync(self.channel_layer.group_discard)(self.group_name, self.channel_name)

    def sos_alert(self, event):
        self.send_json(event["payload"])
