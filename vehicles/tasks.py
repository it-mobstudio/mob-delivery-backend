import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from tenant_settings.services import get_tenant_setting

from .models import VehicleDocument, VehicleDocumentExpiryAlert

logger = logging.getLogger(__name__)

DEFAULT_DOCUMENT_EXPIRY_WARNING_DAYS = 14


@shared_task
def flag_expiring_vehicle_documents():
    """Scheduled daily (see CELERY_BEAT_SCHEDULE) — flags VehicleDocument
    rows whose expiry_date falls within this tenant's configured warning
    window (TenantSetting document_expiry_warning_days, default 14 days)
    and don't already have an open (unacknowledged) alert.
    """
    today = timezone.localdate()
    created = 0
    warning_days_cache = {}

    documents = VehicleDocument.objects.filter(
        expiry_date__isnull=False, expiry_date__gte=today
    ).select_related("vehicle")

    for document in documents:
        if document.company_id not in warning_days_cache:
            warning_days_cache[document.company_id] = get_tenant_setting(
                document.company_id, "document_expiry_warning_days", DEFAULT_DOCUMENT_EXPIRY_WARNING_DAYS
            )
        warning_days = warning_days_cache[document.company_id]

        if document.expiry_date > today + timedelta(days=warning_days):
            continue

        if VehicleDocumentExpiryAlert.objects.filter(document=document, acknowledged=False).exists():
            continue

        VehicleDocumentExpiryAlert.objects.create(
            company_id=document.company_id,
            document=document,
            vehicle=document.vehicle,
            expiry_date=document.expiry_date,
            detected_at=timezone.now(),
        )
        created += 1

    logger.info("flag_expiring_vehicle_documents: created %d alert(s)", created)
    return created
