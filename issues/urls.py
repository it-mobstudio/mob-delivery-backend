from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import IssueViewSet, TripIssueCreateView

router = DefaultRouter(trailing_slash=False)
router.register("issues", IssueViewSet, basename="issue")

urlpatterns = [
    path("trips/<uuid:trip_pk>/issues", TripIssueCreateView.as_view(), name="trip-issue-create"),
] + router.urls
