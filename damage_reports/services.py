from django.utils import timezone

from core.exceptions import DomainError
from drivers.models import Driver
from vehicles.models import Vehicle

from .models import DamageReportStatus, ReporterType, VehicleDamageReport


def _get_vehicle(vehicle_id, company_id):
    try:
        return Vehicle.objects.get(pk=vehicle_id, company_id=company_id)
    except Vehicle.DoesNotExist:
        raise DomainError("VEHICLE_NOT_FOUND", "Vehicle not found.", status_code=404)


def _get_report(report_id, company_id):
    try:
        return VehicleDamageReport.objects.select_related("vehicle").get(pk=report_id, company_id=company_id)
    except VehicleDamageReport.DoesNotExist:
        raise DomainError("DAMAGE_REPORT_NOT_FOUND", "Damage report not found.", status_code=404)


def create_damage_report(vehicle_id, description, actor, photo_url=None, severity=None):
    vehicle = _get_vehicle(vehicle_id, actor.company_id)

    if isinstance(actor, Driver):
        if vehicle.id != actor.current_vehicle_id:
            raise DomainError(
                "NOT_YOUR_VEHICLE",
                "You can only report damage for your currently assigned vehicle.",
                status_code=403,
            )
        reporter_type = ReporterType.DRIVER
    else:
        reporter_type = ReporterType.ADMIN

    kwargs = {}
    if severity:
        kwargs["severity"] = severity

    return VehicleDamageReport.objects.create(
        company_id=actor.company_id,
        vehicle=vehicle,
        reported_by_id=actor.id,
        reporter_type=reporter_type,
        description=description,
        photo_url=photo_url,
        **kwargs,
    )


def resolve_damage_report(report_id, resolution_note, actor):
    report = _get_report(report_id, actor.company_id)

    if report.status == DamageReportStatus.RESOLVED:
        raise DomainError("ALREADY_RESOLVED", "This damage report has already been resolved.", status_code=409)

    report.status = DamageReportStatus.RESOLVED
    report.resolution_note = resolution_note
    report.resolved_by = actor.id
    report.resolved_at = timezone.now()
    report.save(update_fields=["status", "resolution_note", "resolved_by", "resolved_at"])

    return report
