from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DriverKycAadharView,
    DriverKycDlView,
    DriverKycPoliceView,
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
    path("drivers/<uuid:pk>/kyc", DriverKycView.as_view(), name="driver-kyc"),
    path("drivers/<uuid:pk>/kyc/aadhar", DriverKycAadharView.as_view(), name="driver-kyc-aadhar"),
    path("drivers/<uuid:pk>/kyc/police", DriverKycPoliceView.as_view(), name="driver-kyc-police"),
    path("drivers/<uuid:pk>/kyc/dl", DriverKycDlView.as_view(), name="driver-kyc-dl"),
]
