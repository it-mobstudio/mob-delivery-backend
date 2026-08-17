from django.urls import path

from .consumers import TrackingConsumer

websocket_urlpatterns = [
    path("ws/tracking/trip/<uuid:trip_id>/", TrackingConsumer.as_asgi()),
    path("ws/tracking/vehicle/<uuid:vehicle_id>/", TrackingConsumer.as_asgi()),
]
