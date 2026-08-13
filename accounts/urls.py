from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import AdminLoginView, ApiClientTokenView

urlpatterns = [
    path("login", AdminLoginView.as_view(), name="admin-login"),
    path("refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("client-token", ApiClientTokenView.as_view(), name="api-client-token"),
]
