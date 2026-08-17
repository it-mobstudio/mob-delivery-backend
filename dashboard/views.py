from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from damage_reports.serializers import DamageReportListSerializer
from issues.serializers import IssueListSerializer

from . import services


@extend_schema(
    summary="Get live fleet status",
    description=(
        "One row per active vehicle: assigned driver (if on a shift today), current trip, and "
        "a computed status — `moving` (recent GPS ping), `paused` (open TripPause), "
        "`idle_alert` (no recent ping or an unacknowledged stationary alert), or `offline` "
        "(no active shift). Also includes today's working minutes so far and last known location."
    ),
)
class FleetStatusView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(services.get_fleet_status(request.user.company_id))


@extend_schema(
    summary="Get dashboard KPIs",
    description=(
        "Company-wide summary counters for the Admin Panel home screen: vehicles active today, "
        "trips currently in transit, unacknowledged anomaly alerts, today's average delivery "
        "duration, open damage reports, drivers locked for an expired DL, and documents "
        "expiring soon."
    ),
)
class DashboardKpisView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(services.get_kpis(request.user.company_id))


@extend_schema(
    summary="Get recent issues",
    description="Most recently raised trip issues, newest first (default 5, override with ?limit=).",
)
class RecentIssuesView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        limit = int(request.query_params.get("limit", 5))
        issues = services.get_recent_issues(request.user.company_id, limit)
        return Response(IssueListSerializer(issues, many=True).data)


@extend_schema(
    summary="Get recent damage reports",
    description="Most recently filed vehicle damage reports, newest first (default 5, override with ?limit=).",
)
class RecentDamageReportsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        limit = int(request.query_params.get("limit", 5))
        reports = services.get_recent_damage_reports(request.user.company_id, limit)
        return Response(DamageReportListSerializer(reports, many=True).data)
