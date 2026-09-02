from django.utils import timezone

from core.exceptions import DomainError
from trips import services as trip_services
from trips.models import TripStop

from .models import IssueStatus, IssueType, TripIssue


def _get_issue(issue_id, company_id):
    try:
        return TripIssue.objects.select_related("trip", "trip__driver", "trip__vehicle", "trip_stop").get(
            pk=issue_id, company_id=company_id
        )
    except TripIssue.DoesNotExist:
        raise DomainError("ISSUE_NOT_FOUND", "Issue not found.", status_code=404)


def create_issue(
    trip_id,
    issue_type,
    note,
    actor,
    trip_stop_id=None,
    severity=None,
    photo_url=None,
    penalty_amount=None,
    penalty_challan_number=None,
):
    trip = trip_services.get_trip(trip_id, actor.company_id)

    trip_stop = None
    if trip_stop_id:
        try:
            trip_stop = TripStop.objects.get(pk=trip_stop_id, trip_id=trip.id)
        except TripStop.DoesNotExist:
            raise DomainError(
                "TRIP_STOP_NOT_FOUND", "This stop was not found on the given trip.", status_code=404
            )

    if issue_type == IssueType.TRAFFIC_PENALTY and penalty_amount is None:
        raise DomainError(
            "PENALTY_AMOUNT_REQUIRED", "penalty_amount is required for a traffic_penalty issue.", status_code=400
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
        penalty_amount=penalty_amount,
        penalty_challan_number=penalty_challan_number,
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


def mark_penalty_paid(issue_id, actor):
    issue = _get_issue(issue_id, actor.company_id)

    if issue.issue_type != IssueType.TRAFFIC_PENALTY:
        raise DomainError(
            "NOT_A_PENALTY_ISSUE", "Only a traffic_penalty issue can be marked paid.", status_code=409
        )
    if issue.penalty_paid:
        raise DomainError("PENALTY_ALREADY_PAID", "This penalty is already marked paid.", status_code=409)

    issue.penalty_paid = True
    issue.penalty_paid_at = timezone.now()
    issue.save(update_fields=["penalty_paid", "penalty_paid_at"])

    return issue
