from datetime import timedelta

from django.utils import timezone

from drivers.models import Driver

from .models import IdempotencyRecord

IDEMPOTENCY_WINDOW = timedelta(hours=24)


def _get_replayed_response(driver_id, key):
    cutoff = timezone.now() - IDEMPOTENCY_WINDOW
    record = IdempotencyRecord.objects.filter(driver_id=driver_id, key=key, created_at__gte=cutoff).first()
    if record is None:
        return None
    return record.status_code, record.response_body


def _record_response(driver_id, key, status_code, body):
    IdempotencyRecord.objects.update_or_create(
        driver_id=driver_id, key=key, defaults={"status_code": status_code, "response_body": body}
    )


def with_idempotency(request, handler):
    """Wrap a mutating view handler with Idempotency-Key replay support.

    `handler` is a zero-arg callable returning (status_code, body). If the
    request carries an `Idempotency-Key` header AND the caller is a Driver,
    and that (driver, key) pair was already processed within the last 24h,
    the original (status_code, body) is replayed WITHOUT calling `handler`
    again. Otherwise `handler` runs normally and its result is recorded for
    future replays.

    A backend-side safety net only — the actual offline queue/retry belongs
    in the driver app. No-ops entirely (just calls handler()) when there's
    no key or the caller isn't a Driver, so AdminUser/ApiClient callers on
    shared endpoints (e.g. damage-reports) are unaffected.
    """
    key = request.headers.get("Idempotency-Key")
    if not key or not isinstance(request.user, Driver):
        return handler()

    driver_id = request.user.id
    cached = _get_replayed_response(driver_id, key)
    if cached is not None:
        return cached

    status_code, body = handler()
    _record_response(driver_id, key, status_code, body)
    return status_code, body
