from django.urls import path

from .views import DeviceDeregisterView, DeviceRegisterView

urlpatterns = [
    path("driver/devices/register", DeviceRegisterView.as_view(), name="driver-device-register"),
    path("driver/devices/<path:token>", DeviceDeregisterView.as_view(), name="driver-device-deregister"),
]
