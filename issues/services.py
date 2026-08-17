from django.utils import timezone

from core.exceptions import DomainError
from trips import services as trip_services
from trips.models import TripStop

from .models import IssueStatus, TripIssue


def _get_issue(issue_id, company_id):
    try:
        return TripIssue.objects.select_related("trip", "trip__driver", "trip__vehicle", "trip_stop").get(
            pk=issue_id, company_id=company_id
        )
    except TripIssue.DoesNotExist:
        raise DomainError("ISSUE_NOT_FOUND", "Issue not found.", status_code=404)


def create_issue(trip_id, issue_type, note, actor, trip_stop_id=None, severity=None, photo_url=None):
    trip = trip_services.get_trip(trip_id, actor.company_id)

    trip_stop = None
    if trip_stop_id:
        try:
            trip_stop = TripStop.objects.get(pk=trip_stop_id, trip_id=trip.id)
        except TripStop.DoesNotExist:
            raise DomainError(
                "TRIP_STOP_NOT_FOUND", "This stop was not found on the given trip.", status_code=404
            )

    kwargs = {}
    if severity:
        kwargs["severity"] = severity

    return TripIssue.objects.create(
        company_id=trip.company_id,
        trip=trip,
        trip_stop=trip_stop,
        issue_type=issue_type,
        note=note,
        photo_url=photo_url,
        **kwargs,
    )


def resolve_issue(issue_id, resolution_note, actor):
    issue = _get_issue(issue_id, actor.company_id)

    if issue.status == IssueStatus.RESOLVED:
        raise DomainError("ISSUE_ALREADY_RESOLVED", "This issue has already been resolved.", status_code=409)

    issue.status = IssueStatus.RESOLVED
    issue.resolution_note = resolution_note
    issue.resolved_by = actor.id
    issue.resolved_at = timezone.now()
    issue.save(update_fields=["status", "resolution_note", "resolved_by", "resolved_at"])

    return issue
