from django.urls import path

from .views import TenantSettingListView, TenantSettingUpdateView

urlpatterns = [
    path("settings", TenantSettingListView.as_view(), name="tenant-setting-list"),
    path("settings/<str:key>", TenantSettingUpdateView.as_view(), name="tenant-setting-update"),
]
