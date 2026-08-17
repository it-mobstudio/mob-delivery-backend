from django.urls import path

from .views import DashboardKpisView, FleetStatusView, RecentDamageReportsView, RecentIssuesView

urlpatterns = [
    path("dashboard/fleet-status", FleetStatusView.as_view(), name="dashboard-fleet-status"),
    path("dashboard/kpis", DashboardKpisView.as_view(), name="dashboard-kpis"),
    path("dashboard/recent-issues", RecentIssuesView.as_view(), name="dashboard-recent-issues"),
    path(
        "dashboard/recent-damage-reports",
        RecentDamageReportsView.as_view(),
        name="dashboard-recent-damage-reports",
    ),
]
