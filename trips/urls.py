from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AssignmentCandidatesView,
    GroupedOrdersView,
    IntakeOrderView,
    OrderCancelView,
    TripStopCompleteView,
    TripStopDeliveryAddressView,
    TripStopPhotoView,
    TripViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("trips", TripViewSet, basename="trip")

urlpatterns = [
    path("orders", IntakeOrderView.as_view(), name="order-intake"),
    path("orders/grouped", GroupedOrdersView.as_view(), name="orders-grouped"),
    path("orders/<str:order_ref>/cancel", OrderCancelView.as_view(), name="order-cancel"),
    path("trips/assignment-candidates", AssignmentCandidatesView.as_view(), name="trip-assignment-candidates"),
    path(
        "trips/<uuid:pk>/stops/<uuid:stop_pk>/complete",
        TripStopCompleteView.as_view(),
        name="trip-stop-complete",
    ),
    path(
        "trips/<uuid:pk>/stops/<uuid:stop_pk>/photos",
        TripStopPhotoView.as_view(),
        name="trip-stop-photos",
    ),
    path(
        "trips/<uuid:pk>/stops/<uuid:stop_pk>/delivery-address",
        TripStopDeliveryAddressView.as_view(),
        name="trip-stop-delivery-address",
    ),
] + router.urls
