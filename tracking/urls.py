from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ActiveShiftView,
    AlertViewSet,
    LocationPingView,
    ShiftEndView,
    ShiftStartView,
    ShiftViewSet,
    TripLocationHistoryView,
    TripPauseView,
    TripResumeView,
    TripTimeSummaryView,
)

router = DefaultRouter(trailing_slash=False)
router.register("alerts", AlertViewSet, basename="alert")
# ShiftViewSet only implements `list` — registered after the explicit
# shifts/start, shifts/active, shifts/<pk>/end paths so there's no collision.
router.register("shifts", ShiftViewSet, basename="admin-shift")

urlpatterns = [
    path("trips/<uuid:pk>/location", LocationPingView.as_view(), name="trip-location"),
    path("trips/<uuid:pk>/pause", TripPauseView.as_view(), name="trip-pause"),
    path("trips/<uuid:pk>/resume", TripResumeView.as_view(), name="trip-resume"),
    path("trips/<uuid:pk>/time-summary", TripTimeSummaryView.as_view(), name="trip-time-summary"),
    path("trips/<uuid:pk>/location-history", TripLocationHistoryView.as_view(), name="trip-location-history"),
    path("shifts/start", ShiftStartView.as_view(), name="shift-start"),
    path("shifts/active", ActiveShiftView.as_view(), name="shift-active"),
    path("shifts/<uuid:pk>/end", ShiftEndView.as_view(), name="shift-end"),
] + router.urls
