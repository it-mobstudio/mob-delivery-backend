from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import VehicleDocumentViewSet, VehicleTypeViewSet, VehicleViewSet

router = DefaultRouter(trailing_slash=False)
router.register("vehicle-types", VehicleTypeViewSet, basename="vehicle-type")
router.register("vehicles", VehicleViewSet, basename="vehicle")

document_list = VehicleDocumentViewSet.as_view({"get": "list", "post": "create"})
document_detail = VehicleDocumentViewSet.as_view({"patch": "partial_update"})

urlpatterns = router.urls + [
    path("vehicles/<uuid:vehicle_pk>/documents", document_list, name="vehicle-document-list"),
    path("vehicles/<uuid:vehicle_pk>/documents/<uuid:pk>", document_detail, name="vehicle-document-detail"),
]
