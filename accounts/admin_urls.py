from django.urls import path

from .views import AdminUserDisableView, AdminUserListCreateView

urlpatterns = [
    path("admin/users", AdminUserListCreateView.as_view(), name="admin-user-list-create"),
    path("admin/users/<uuid:pk>/disable", AdminUserDisableView.as_view(), name="admin-user-disable"),
]
