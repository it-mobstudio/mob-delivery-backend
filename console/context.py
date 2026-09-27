"""What every console page needs: the sidebar's live counts and the
company switcher."""

from django.db.models import Q

from core.choices import TripStatus, VerificationStatus

from .access import can_edit, company_id_for, is_platform, scoped

ACTIVE = [TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS]
PENDING_KYC = (
    Q(kyc__aadhar_status=VerificationStatus.PENDING, kyc__aadhar_doc_url__isnull=False)
    | Q(kyc__dl_status=VerificationStatus.PENDING, kyc__dl_doc_url__isnull=False)
    | Q(kyc__police_status=VerificationStatus.PENDING, kyc__police_doc_url__isnull=False)
)


def console(request):
    if not request.path.startswith("/admin") or request.path.startswith("/admin/db"):
        return {}
    user = request.user
    if not (user.is_authenticated and user.is_staff):
        return {}
    from accounts.models import Company
    from drivers.models import Driver
    from trips.models import Trip

    trips = scoped(request, Trip.objects.all())
    return {
        "nav": {
            "active_orders": trips.filter(status__in=ACTIVE).count(),
            "unassigned": trips.filter(status__in=[TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE]).count(),
            "pending_kyc": scoped(request, Driver.objects.all()).filter(PENDING_KYC).distinct().count(),
        },
        "is_platform": is_platform(user),
        "can_edit": can_edit(user),
        "companies": Company.objects.order_by("name") if is_platform(user) else None,
        "current_company": str(company_id_for(request) or ""),
    }
