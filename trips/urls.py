from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DriverActiveTripView,
    DriverTripArriveView,
    DriverTripCancelView,
    DriverTripCompleteView,
    DriverTripDeliveryOtpResendView,
    DriverTripDetailView,
    DriverTripItemVerifyView,
    DriverTripListView,
    DriverTripNavigationView,
    DriverTripPaymentCollectView,
    DriverTripPaymentQrView,
    DriverTripStartView,
    RazorpayWebhookView,
    TripEstimateView,
    TripViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("trips", TripViewSet, basename="trip")

urlpatterns = [
    # Must precede router.urls: the router's detail route
    # (trips/<pk>) uses an unconstrained lookup regex and would otherwise
    # swallow this path first, matching "estimate" as a pk.
    path("trips/estimate", TripEstimateView.as_view(), name="trip-estimate"),
    path("webhooks/razorpay", RazorpayWebhookView.as_view(), name="webhook-razorpay"),
    path("driver/trips", DriverTripListView.as_view(), name="driver-trip-list"),
    path("driver/trips/active", DriverActiveTripView.as_view(), name="driver-trip-active"),
    path("driver/trips/<uuid:pk>", DriverTripDetailView.as_view(), name="driver-trip-detail"),
    path("driver/trips/<uuid:pk>/navigation", DriverTripNavigationView.as_view(), name="driver-trip-navigation"),
    path("driver/trips/<uuid:pk>/arrive", DriverTripArriveView.as_view(), name="driver-trip-arrive"),
    path("driver/trips/<uuid:pk>/start", DriverTripStartView.as_view(), name="driver-trip-start"),
    path("driver/trips/<uuid:pk>/payment/qr", DriverTripPaymentQrView.as_view(), name="driver-trip-payment-qr"),
    path(
        "driver/trips/<uuid:pk>/payment/collect",
        DriverTripPaymentCollectView.as_view(),
        name="driver-trip-payment-collect",
    ),
    path(
        "driver/trips/<uuid:pk>/delivery-otp/resend",
        DriverTripDeliveryOtpResendView.as_view(),
        name="driver-trip-delivery-otp-resend",
    ),
    path(
        "driver/trips/<uuid:pk>/items/<uuid:item_id>/verify",
        DriverTripItemVerifyView.as_view(),
        name="driver-trip-item-verify",
    ),
    path("driver/trips/<uuid:pk>/complete", DriverTripCompleteView.as_view(), name="driver-trip-complete"),
    path("driver/trips/<uuid:pk>/cancel", DriverTripCancelView.as_view(), name="driver-trip-cancel"),
] + router.urls
