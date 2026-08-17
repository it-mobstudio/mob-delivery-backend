from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ActiveShiftView,
    AlertViewSet,
    LocationPingView,
    ShiftEndView,
    ShiftStartView,
    TripPauseView,
    TripResumeView,
    TripTimeSummaryView,
    VehicleStartPointViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register("alerts", AlertViewSet, basename="alert")
router.register("start-points", VehicleStartPointViewSet, basename="start-point")

urlpatterns = [
    path("trips/<uuid:pk>/location", LocationPingView.as_view(), name="trip-location"),
    path("trips/<uuid:pk>/pause", TripPauseView.as_view(), name="trip-pause"),
    path("trips/<uuid:pk>/resume", TripResumeView.as_view(), name="trip-resume"),
    path("trips/<uuid:pk>/time-summary", TripTimeSummaryView.as_view(), name="trip-time-summary"),
    path("shifts/start", ShiftStartView.as_view(), name="shift-start"),
    path("shifts/active", ActiveShiftView.as_view(), name="shift-active"),
    path("shifts/<uuid:pk>/end", ShiftEndView.as_view(), name="shift-end"),
] + router.urls
