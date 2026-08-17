from django.urls import path

from .views import AdminLoginView, ApiClientTokenView, RefreshTokenView

urlpatterns = [
    path("login", AdminLoginView.as_view(), name="admin-login"),
    path("refresh", RefreshTokenView.as_view(), name="token-refresh"),
    path("client-token", ApiClientTokenView.as_view(), name="api-client-token"),
]
