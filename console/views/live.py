from django.http import JsonResponse
from django.urls import reverse

from core.choices import TripStatus
from drivers.models import Driver
from trips.models import Trip

from ..access import console_view, scoped
from ..context import ACTIVE
from .common import page


@console_view()
def live(request):
    return page(request, "console/live.html", "live")


@console_view()
def live_data(request):
    """What's on the road now — polled by the live map every 15 s."""
    trips = scoped(request, Trip.objects.filter(status__in=ACTIVE + [TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE])
                   ).select_related("driver", "vehicle_type").order_by("-created_at")[:300]
    busy = {t.driver_id: t for t in trips if t.driver_id}
    drivers = scoped(request, Driver.objects.filter(is_online=True, last_known_lat__isnull=False))[:1000]
    return JsonResponse({
        "drivers": [{
            "id": str(d.pk), "name": d.full_name, "phone": d.phone_number,
            "lat": float(d.last_known_lat), "lng": float(d.last_known_lng),
            "seen": d.last_location_at.isoformat() if d.last_location_at else None,
            "busy": d.pk in busy, "order": busy[d.pk].order_number if d.pk in busy else None,
            "url": reverse("console:driver", args=[d.pk]),
        } for d in drivers],
        "trips": [{
            "id": str(t.pk), "number": t.order_number, "status": t.status, "status_label": t.get_status_display(),
            "pickup": [float(t.pickup_lat), float(t.pickup_lng)], "drop": [float(t.drop_lat), float(t.drop_lng)],
            "pickup_name": t.pickup_contact_name, "drop_name": t.drop_contact_name,
            "driver": t.driver.full_name if t.driver else None, "vehicle_type": t.vehicle_type.name,
            "url": reverse("console:order", args=[t.pk]),
        } for t in trips],
    })
