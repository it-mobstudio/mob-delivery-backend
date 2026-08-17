from django.urls import path

from .views import SosAcknowledgeView, SosAlertListCreateView, SosResolveView

urlpatterns = [
    path("sos", SosAlertListCreateView.as_view(), name="sos-list-create"),
    path("sos/<uuid:pk>/acknowledge", SosAcknowledgeView.as_view(), name="sos-acknowledge"),
    path("sos/<uuid:pk>/resolve", SosResolveView.as_view(), name="sos-resolve"),
]
