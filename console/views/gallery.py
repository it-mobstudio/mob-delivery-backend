from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Q

from trips.models import Trip, TripItem

from ..access import console_view, scoped
from .common import day_start, page, parse_date

KINDS = [("", "All photos"), ("pickup", "Pickup"), ("delivery", "Delivery"), ("check", "Item check")]


@console_view()
def gallery(request):
    """Every proof photo — at pickup, at the drop, and from the item checks —
    newest first, filterable by kind, date, order, driver or customer."""
    g = request.GET
    kind, q = g.get("kind", ""), (g.get("q") or "").strip()
    start, end = parse_date(g.get("from")), parse_date(g.get("to"))

    trips = scoped(request, Trip.objects.select_related("driver"))
    if q:
        trips = trips.filter(Q(order_number__icontains=q) | Q(reference_id__icontains=q) | Q(driver__full_name__icontains=q)
                             | Q(driver__phone_number__icontains=q) | Q(drop_contact_name__icontains=q))
    if start:
        trips = trips.filter(updated_at__gte=day_start(start))
    if end:
        trips = trips.filter(updated_at__lt=day_start(end + timedelta(days=1)))

    shots = []
    if kind in ("", "pickup", "delivery"):
        for t in trips.exclude(pickup_photo_url="", delivery_photo_url="").order_by("-updated_at")[:600]:
            for k, url, when in (("pickup", t.pickup_photo_url, t.started_at), ("delivery", t.delivery_photo_url, t.completed_at)):
                if url and kind in ("", k):
                    shots.append({"url": url, "kind": k, "trip": t, "title": "Whole order", "when": when or t.updated_at})
    items = TripItem.objects.filter(trip__in=trips).select_related("trip", "trip__driver")
    for i in items.exclude(pickup_photo_url="", delivery_photo_url="", proof_image_url="").order_by("-updated_at")[:1200]:
        for k, url, when in (("pickup", i.pickup_photo_url, i.trip.started_at), ("delivery", i.delivery_photo_url, i.trip.completed_at),
                             ("check", i.proof_image_url, i.verified_at)):
            if url and kind in ("", k):
                shots.append({"url": url, "kind": k, "trip": i.trip, "title": i.name, "when": when or i.updated_at,
                              "issue": i.status == "not_delivered", "note": i.driver_note})
    shots.sort(key=lambda s: s["when"], reverse=True)
    page_obj = Paginator(shots, 48).get_page(g.get("page"))
    return page(request, "console/gallery.html", "gallery", page_obj=page_obj, kinds=KINDS, kind=kind, total=len(shots))
