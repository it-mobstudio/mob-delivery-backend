from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import DamageReportViewSet, VehicleDamageReportListCreateView

router = DefaultRouter(trailing_slash=False)
router.register("damage-reports", DamageReportViewSet, basename="damage-report")

urlpatterns = [
    path(
        "vehicles/<uuid:vehicle_pk>/damage-reports",
        VehicleDamageReportListCreateView.as_view(),
        name="vehicle-damage-report-list",
    ),
] + router.urls
