from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminUser
from core.tenancy import CompanyScopedMixin
from damage_reports.permissions import IsDriverOrAdminUser

from . import services
from .filters import IssueFilter
from .models import TripIssue
from .serializers import CreateIssueSerializer, IssueDetailSerializer, IssueListSerializer, ResolveIssueSerializer


@extend_schema(
    tags=["Admin: Issues", "Driver: Issues"],
    summary="Flag an issue on a trip",
    description="Manually raises a TripIssue against a trip (e.g. driver or admin flags a problem mid-delivery), separate from the automated anomaly alerts raised by the tracking jobs.",
)
class TripIssueCreateView(APIView):
    permission_classes = [IsDriverOrAdminUser]

    def post(self, request, trip_pk=None):
        serializer = CreateIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue = services.create_issue(trip_id=trip_pk, actor=request.user, **serializer.validated_data)
        return Response(IssueDetailSerializer(issue).data, status=201)


@extend_schema_view(
    list=extend_schema(tags=["Admin: Issues"], summary="List issues", description="Company-wide list of flagged trip issues — includes both manually-raised ones and the ones auto-created when a trip is cancelled with picked-up, undelivered goods."),
    retrieve=extend_schema(tags=["Admin: Issues"], summary="Get an issue"),
)
class IssueViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = TripIssue.objects.select_related("trip", "trip__driver", "trip__vehicle", "trip_stop").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = IssueFilter

    def get_permissions(self):
        if self.action in ("resolve", "mark_penalty_paid"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "list":
            return IssueListSerializer
        return IssueDetailSerializer

    @extend_schema(tags=["Admin: Issues"], summary="Resolve an issue", description="Admin-only: marks a flagged issue resolved.")
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        serializer = ResolveIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue = services.resolve_issue(issue_id=pk, actor=request.user, **serializer.validated_data)
        return Response(IssueDetailSerializer(issue).data)

    @extend_schema(
        tags=["Admin: Issues"],
        summary="Mark a traffic penalty paid",
        description="Admin-only: records that a traffic_penalty issue's fine has been paid/reimbursed. 409 if the issue isn't a traffic_penalty, or is already marked paid.",
        request=None,
    )
    @action(detail=True, methods=["post"], url_path="mark-penalty-paid")
    def mark_penalty_paid(self, request, pk=None):
        issue = services.mark_penalty_paid(issue_id=pk, actor=request.user)
        return Response(IssueDetailSerializer(issue).data)
