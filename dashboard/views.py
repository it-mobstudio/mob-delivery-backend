from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.exceptions import DomainError
from damage_reports.serializers import DamageReportListSerializer
from issues.serializers import IssueListSerializer

from . import services


@extend_schema(
    tags=["Admin: Dashboard"],
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
    tags=["Admin: Dashboard"],
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
    tags=["Admin: Dashboard"],
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
    tags=["Admin: Dashboard"],
    summary="Get recent damage reports",
    description="Most recently filed vehicle damage reports, newest first (default 5, override with ?limit=).",
)
class RecentDamageReportsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        limit = int(request.query_params.get("limit", 5))
        reports = services.get_recent_damage_reports(request.user.company_id, limit)
        return Response(DamageReportListSerializer(reports, many=True).data)


def _parse_since(raw):
    parsed = parse_datetime(raw)
    if parsed is None:
        parsed_date = parse_date(raw)
        if parsed_date is None:
            return None
        parsed = datetime.combine(parsed_date, datetime.min.time())
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@extend_schema(
    tags=["Admin: Dashboard"],
    summary="Get a driver's safety score",
    description=(
        "A 0-100 at-a-glance quality signal for one driver: 100 minus 3 per unacknowledged "
        "tracking anomaly alert, 5 per unresolved trip issue, and 10 per traffic-penalty issue "
        "raised in the window (floored at 0). Defaults to the start of the current week; "
        "override with `?since=` (an ISO date or datetime)."
    ),
)
class DriverSafetyScoreView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, pk=None):
        since = None
        since_param = request.query_params.get("since")
        if since_param:
            since = _parse_since(since_param)
            if since is None:
                raise DomainError("INVALID_SINCE", "since must be an ISO date or datetime.", status_code=400)
        result = services.get_driver_safety_score(driver_id=pk, company_id=request.user.company_id, since=since)
        return Response(result)
