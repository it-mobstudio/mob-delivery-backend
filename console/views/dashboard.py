import json
from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.urls import reverse
from django.utils import timezone

from core.choices import PaymentMode, PaymentStatus, TripStatus, VerificationStatus
from drivers.models import Driver, DriverKyc
from trips.models import Trip, TripItem

from ..access import console_view, scoped
from ..context import ACTIVE, PENDING_KYC
from .common import PERIODS, day_start, delta, page, period_range


def _totals(trips):
    done = trips.filter(status=TripStatus.COMPLETED)
    agg = trips.aggregate(
        orders=Count("id"),
        completed=Count("id", filter=Q(status=TripStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=TripStatus.CANCELLED)),
    )
    agg.update(done.aggregate(gmv=Sum("total_fare"), earnings=Sum("driver_earning"), bonus=Sum("bonus_fare")))
    agg["cod"] = trips.filter(payment_mode=PaymentMode.COD, payment_status=PaymentStatus.PAID).aggregate(
        s=Sum("total_fare"))["s"]
    agg["avg"] = done.filter(started_at__isnull=False).aggregate(
        a=Avg(ExpressionWrapper(F("completed_at") - F("started_at"), output_field=DurationField())))["a"]
    return agg


def _minutes(d):
    if not d:
        return "—"
    m = int(d.total_seconds() // 60)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m} min"


@console_view()
def dashboard(request):
    key, label, start, end, (prev_start, prev_end) = period_range(request)
    all_trips = scoped(request, Trip.objects.all())
    trips = all_trips.filter(created_at__gte=day_start(start), created_at__lt=day_start(end + timedelta(days=1)))
    before = all_trips.filter(created_at__gte=day_start(prev_start), created_at__lt=day_start(prev_end + timedelta(days=1)))
    now, then = _totals(trips), _totals(before)
    drivers = scoped(request, Driver.objects.all())
    orders_url = reverse("console:orders")
    span = f"from={start:%Y-%m-%d}&to={end:%Y-%m-%d}"

    def kpi(label_, value, icon, cur, prev, hint="", url=None, invert=False):
        text, direction = delta(cur, prev)
        if invert and direction in ("up", "down"):
            direction = "down" if direction == "up" else "up"
        return {"label": label_, "value": value, "icon": icon, "delta": text, "dir": direction, "hint": hint, "url": url}

    rate = lambda a, b: round(100 * (a or 0) / b) if b else 0  # noqa: E731
    kpis = [
        kpi("Orders", now["orders"], "package", now["orders"], then["orders"], "vs previous period", f"{orders_url}?{span}"),
        kpi("Delivered", now["completed"], "circle-check", now["completed"], then["completed"],
            f"{rate(now['completed'], now['orders'])}% completion", f"{orders_url}?status=completed&{span}"),
        kpi("Cancelled", now["cancelled"], "circle-x", now["cancelled"], then["cancelled"],
            f"{rate(now['cancelled'], now['orders'])}% of orders", f"{orders_url}?status=cancelled&{span}", invert=True),
        kpi("Order value", f"₹{(now['gmv'] or 0):,.0f}", "indian-rupee", now["gmv"], then["gmv"], "delivered orders"),
        kpi("Driver earnings", f"₹{(now['earnings'] or 0):,.0f}", "hand-coins", now["earnings"], then["earnings"],
            f"incl. ₹{(now['bonus'] or 0):,.0f} unloading bonus"),
        kpi("COD collected", f"₹{(now['cod'] or 0):,.0f}", "qr-code", now["cod"], then["cod"], "paid by QR"),
        kpi("Avg delivery time", _minutes(now["avg"]), "timer", (now["avg"] or timedelta()).total_seconds(),
            (then["avg"] or timedelta()).total_seconds(), "pickup → drop", invert=True),
        kpi("Active right now", all_trips.filter(status__in=ACTIVE).count(), "navigation", 0, 0,
            f"{drivers.filter(is_online=True).count()} of {drivers.count()} drivers online", f"{orders_url}?status=active"),
    ]

    # Daily series for the chart.
    rows = {r["day"]: r for r in trips.annotate(day=TruncDate("created_at")).values("day").annotate(
        total=Count("id"), done=Count("id", filter=Q(status=TripStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=TripStatus.CANCELLED)),
        gmv=Sum("total_fare", filter=Q(status=TripStatus.COMPLETED)))}
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    series = {
        "labels": [d.strftime("%d %b") for d in days],
        "done": [rows.get(d, {}).get("done", 0) for d in days],
        "cancelled": [rows.get(d, {}).get("cancelled", 0) for d in days],
        "other": [rows.get(d, {}).get("total", 0) - rows.get(d, {}).get("done", 0) - rows.get(d, {}).get("cancelled", 0) for d in days],
        "gmv": [float(rows.get(d, {}).get("gmv") or 0) for d in days],
    }
    hours = {r["h"]: r["n"] for r in trips.annotate(h=ExtractHour("created_at", tzinfo=timezone.get_current_timezone()))
             .values("h").annotate(n=Count("id"))}
    by_hour = [hours.get(h, 0) for h in range(24)]
    status_rows = list(trips.values("status").annotate(n=Count("id")).order_by("-n"))
    for r in status_rows:
        r["label"] = TripStatus(r["status"]).label
    by_type = list(trips.values("vehicle_type__name").annotate(n=Count("id"), gmv=Sum("total_fare")).order_by("-n")[:6])
    top = max([r["n"] for r in by_type] + [1])
    for r in by_type:
        r["pct"] = round(100 * r["n"] / top)

    funnel_counts = [
        ("Booked", trips.count()),
        ("Driver assigned", trips.filter(assigned_at__isnull=False).count()),
        ("Picked up", trips.filter(started_at__isnull=False).count()),
        ("Delivered", trips.filter(status=TripStatus.COMPLETED).count()),
    ]
    first = funnel_counts[0][1] or 1
    funnel = [{"label": l, "n": n, "pct": round(100 * n / first)} for l, n in funnel_counts]

    top_drivers = list(trips.filter(status=TripStatus.COMPLETED, driver__isnull=False)
                       .values("driver_id", "driver__full_name", "driver__phone_number", "driver__profile_photo_url")
                       .annotate(trips=Count("id"), earned=Sum("driver_earning")).order_by("-trips", "-earned")[:6])

    today = timezone.localdate()
    kyc = DriverKyc.objects.filter(driver__in=drivers)
    attention = [a for a in [
        ("user-round-search", "Orders waiting for a driver",
         all_trips.filter(status__in=[TripStatus.REQUESTED, TripStatus.NO_DRIVER_AVAILABLE]).count(),
         f"{orders_url}?status=unassigned"),
        ("shield-alert", "KYC documents to review", drivers.filter(PENDING_KYC).distinct().count(), reverse("console:kyc")),
        ("id-card", "Licences expiring in 30 days",
         kyc.filter(dl_status=VerificationStatus.VERIFIED, dl_expiry_date__range=(today, today + timedelta(days=30))).count(),
         f"{reverse('console:drivers')}?tab=expiring"),
        ("clock-alert", "Deliveries running over 3 hours",
         all_trips.filter(status=TripStatus.IN_PROGRESS, started_at__lt=timezone.now() - timedelta(hours=3)).count(),
         f"{orders_url}?status=in_progress"),
        ("package-x", "Items reported not delivered",
         TripItem.objects.filter(trip__in=trips, status="not_delivered").count(), f"{orders_url}?issues=1&{span}"),
    ] if a[2]]

    return page(request, "console/dashboard.html", "dashboard",
                period=key, period_label=label, periods=list(PERIODS.items()), start=start, end=end,
                kpis=kpis, series_json=json.dumps(series), by_hour_json=json.dumps(by_hour),
                status_json=json.dumps({"labels": [r["label"] for r in status_rows], "values": [r["n"] for r in status_rows],
                                        "keys": [r["status"] for r in status_rows]}),
                by_type=by_type, funnel=funnel, top_drivers=top_drivers, attention=attention,
                recent=trips.select_related("driver", "vehicle_type").order_by("-created_at")[:8])
