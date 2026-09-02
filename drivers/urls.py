from django.urls import path
from rest_framework.routers import DefaultRouter

from vehicles.views import DriverCurrentVehicleView

from .views import (
    DriverKycView,
    DriverMeView,
    DriverOtpRequestView,
    DriverOtpVerifyView,
    DriverViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("drivers", DriverViewSet, basename="driver")

urlpatterns = router.urls + [
    path("driver/auth/otp/request", DriverOtpRequestView.as_view(), name="driver-otp-request"),
    path("driver/auth/otp/verify", DriverOtpVerifyView.as_view(), name="driver-otp-verify"),
    path("driver/me", DriverMeView.as_view(), name="driver-me"),
    path("driver/vehicle", DriverCurrentVehicleView.as_view(), name="driver-vehicle"),
    path("drivers/<uuid:pk>/kyc", DriverKycView.as_view(), name="driver-kyc"),
]
