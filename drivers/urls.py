from django.urls import path
from rest_framework.routers import DefaultRouter

from .vehicle_views import VehicleDocumentViewSet, VehicleTypeViewSet, VehicleViewSet
from .views import (
    DriverAadharSubmitView,
    DriverDlSubmitView,
    DriverDutyEndView,
    DriverDutyStartView,
    DriverKycAadharView,
    DriverKycDlView,
    DriverKycPoliceView,
    DriverKycView,
    DriverLocationView,
    DriverLogoutView,
    DriverMeView,
    DriverOtpRequestView,
    DriverOtpVerifyView,
    DriverPhotoView,
    DriverPoliceSubmitView,
    DriverStatsView,
    DriverTokenRefreshView,
    DriverVehicleListView,
    DriverViewSet,
    DriverWalletAdminView,
    DriverWalletEntryView,
    DriverWalletTransactionsView,
    DriverWalletView,
)

router = DefaultRouter(trailing_slash=False)
router.register("drivers", DriverViewSet, basename="driver")
router.register("vehicle-types", VehicleTypeViewSet, basename="vehicle-type")
router.register("vehicles", VehicleViewSet, basename="vehicle")

vehicle_document_list = VehicleDocumentViewSet.as_view({"get": "list", "post": "create"})
vehicle_document_detail = VehicleDocumentViewSet.as_view({"patch": "partial_update"})

urlpatterns = router.urls + [
    path("driver/auth/otp/request", DriverOtpRequestView.as_view(), name="driver-otp-request"),
    path("driver/auth/otp/verify", DriverOtpVerifyView.as_view(), name="driver-otp-verify"),
    path("driver/auth/refresh", DriverTokenRefreshView.as_view(), name="driver-token-refresh"),
    path("driver/auth/logout", DriverLogoutView.as_view(), name="driver-logout"),
    path("driver/me", DriverMeView.as_view(), name="driver-me"),
    path("driver/me/photo", DriverPhotoView.as_view(), name="driver-me-photo"),
    path("driver/me/kyc/aadhar", DriverAadharSubmitView.as_view(), name="driver-me-kyc-aadhar"),
    path("driver/me/kyc/dl", DriverDlSubmitView.as_view(), name="driver-me-kyc-dl"),
    path("driver/me/kyc/police", DriverPoliceSubmitView.as_view(), name="driver-me-kyc-police"),
    path("driver/wallet", DriverWalletView.as_view(), name="driver-wallet"),
    path("driver/wallet/transactions", DriverWalletTransactionsView.as_view(), name="driver-wallet-transactions"),
    path("driver/vehicles", DriverVehicleListView.as_view(), name="driver-vehicles"),
    path("driver/stats", DriverStatsView.as_view(), name="driver-stats"),
    path("driver/duty/start", DriverDutyStartView.as_view(), name="driver-duty-start"),
    path("driver/duty/end", DriverDutyEndView.as_view(), name="driver-duty-end"),
    path("driver/location", DriverLocationView.as_view(), name="driver-location"),
    path("drivers/<uuid:pk>/kyc", DriverKycView.as_view(), name="driver-kyc"),
    path("drivers/<uuid:pk>/kyc/aadhar", DriverKycAadharView.as_view(), name="driver-kyc-aadhar"),
    path("drivers/<uuid:pk>/kyc/police", DriverKycPoliceView.as_view(), name="driver-kyc-police"),
    path("drivers/<uuid:pk>/kyc/dl", DriverKycDlView.as_view(), name="driver-kyc-dl"),
    path("drivers/<uuid:pk>/wallet", DriverWalletAdminView.as_view(), name="driver-wallet-admin"),
    path("drivers/<uuid:pk>/wallet/transactions", DriverWalletEntryView.as_view(), name="driver-wallet-entry"),
    path("vehicles/<uuid:vehicle_pk>/documents", vehicle_document_list, name="vehicle-document-list"),
    path("vehicles/<uuid:vehicle_pk>/documents/<uuid:pk>", vehicle_document_detail, name="vehicle-document-detail"),
]
