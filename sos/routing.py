from django.urls import path

from .consumers import SosConsumer

websocket_urlpatterns = [
    path("ws/sos/", SosConsumer.as_asgi()),
]
