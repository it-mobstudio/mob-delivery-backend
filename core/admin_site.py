"""The operations admin: Django's admin with an analytics dashboard as its
home page. Numbers are scoped like the rest of the admin — a superuser sees
the whole platform (and can narrow to one company), a company operator only
their own company."""

from datetime import timedelta

from django.contrib import admin
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from core.choices import PaymentMode, PaymentStatus, TripStatus, VerificationStatus

PERIODS = {"today": ("Today", 1), "7d": ("Last 7 days", 7), "30d": ("Last 30 days", 30), "90d": ("Last 90 days", 90)}
ACTIVE = [TripStatus.ASSIGNED, TripStatus.ARRIVED_AT_PICKUP, TripStatus.IN_PROGRESS]


class MobAdminSite(admin.AdminSite):
    site_header = "MOB Delivery"
    site_title = "MOB Delivery admin"
    index_title = "Operations"
    index_template = "admin/mob/dashboard.html"
    enable_nav_sidebar = True

    def get_urls(self):
        return [path("analytics/", self.admin_view(self.index), name="analytics")] + super().get_urls()

    # -- dashboard -------------------------------------------------------------------

    def index(self, request, extra_context=None):
        context = {**self.each_context(request), **self._analytics(request), **(extra_context or {})}
        context["app_list"] = self.get_app_list(request)
        request.current_app = self.name
        return TemplateResponse(request, self.index_template, context)

    def _scope(self, request):
        """(company id or None for all, the companies a superuser can pick)."""
        from accounts.models import Company

        user = request.user
        if user.is_superuser:
            chosen = request.GET.get("company") or None
            return chosen, Company.objects.order_by("name")
        return user.company_id, None

    def _analytics(self, request):
        from drivers.models import Driver, DriverKyc
        from trips.models import Trip, TripItem

        company_id, companies = self._scope(request)
        period = request.GET.get("period") if request.GET.get("period") in PERIODS else "7d"
        label, days = PERIODS[period]
        now = timezone.now()
        today = timezone.localdate()
        start_day = today - timedelta(days=days - 1)
        since = timezone.make_aware(timezone.datetime.combine(start_day, timezone.datetime.min.time()))

        trips = Trip.objects.all()
        drivers = Driver.objects.all()
        if company_id:
            trips = trips.filter(company_id=company_id)
            drivers = drivers.filter(company_id=company_id)
        in_period = trips.filter(created_at__gte=since)
        done = in_period.filter(status=TripStatus.COMPLETED)

        money = done.aggregate(
            gmv=Sum("total_fare"), earnings=Sum("driver_earning"), bonus=Sum("bonus_fare"),
        )
        cod = in_period.filter(payment_mode=PaymentMode.COD, payment_status=PaymentStatus.PAID).aggregate(
            total=Sum("total_fare")
        )["total"]
        durations = done.filter(started_at__isnull=False, completed_at__isnull=False).aggregate(
            avg=Avg(ExpressionWrapper(F("completed_at") - F("started_at"), output_field=DurationField()))
        )["avg"]
        counts = in_period.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status=TripStatus.COMPLETED)),
            cancelled=Count("id", filter=Q(status=TripStatus.CANCELLED)),
            unassigned=Count("id", filter=Q(status=TripStatus.NO_DRIVER_AVAILABLE)),
        )
        total = counts["total"] or 0

        # Orders per day, split by outcome, for the bar chart.
        per_day = {
            row["day"]: row
            for row in in_period.annotate(day=TruncDate("created_at")).values("day").annotate(
                total=Count("id"),
                completed=Count("id", filter=Q(status=TripStatus.COMPLETED)),
                cancelled=Count("id", filter=Q(status=TripStatus.CANCELLED)),
            )
        }
        series = []
        for i in range(days):
            day = start_day + timedelta(days=i)
            row = per_day.get(day, {})
            series.append({
                "day": day,
                "total": row.get("total", 0),
                "completed": row.get("completed", 0),
                "cancelled": row.get("cancelled", 0),
            })
        peak = max([s["total"] for s in series] + [1])
        for s in series:
            s["h_total"] = round(100 * s["total"] / peak)
            s["h_completed"] = round(100 * s["completed"] / peak)
            s["h_cancelled"] = round(100 * s["cancelled"] / peak)
            s["h_other"] = max(s["h_total"] - s["h_completed"] - s["h_cancelled"], 0)

        by_status = list(in_period.values("status").annotate(n=Count("id")).order_by("-n"))
        for row in by_status:
            row["pct"] = round(100 * row["n"] / total) if total else 0
            row["label"] = TripStatus(row["status"]).label

        by_type = list(
            in_period.values("vehicle_type__name").annotate(n=Count("id"), gmv=Sum("total_fare")).order_by("-n")[:6]
        )
        top_type = max([r["n"] for r in by_type] + [1])
        for row in by_type:
            row["pct"] = round(100 * row["n"] / top_type)

        top_drivers = list(
            done.filter(driver__isnull=False)
            .values("driver_id", "driver__full_name", "driver__phone_number")
            .annotate(trips=Count("id"), earned=Sum("driver_earning"))
            .order_by("-trips", "-earned")[:6]
        )
        for row in top_drivers:
            row["url"] = reverse(f"{self.name}:drivers_driver_change", args=[row["driver_id"]])

        kyc = DriverKyc.objects.filter(driver__in=drivers)
        pending_kyc = kyc.filter(
            Q(aadhar_status=VerificationStatus.PENDING, aadhar_doc_url__isnull=False)
            | Q(dl_status=VerificationStatus.PENDING, dl_doc_url__isnull=False)
            | Q(police_status=VerificationStatus.PENDING, police_doc_url__isnull=False)
        ).count()
        expiring = kyc.filter(
            dl_status=VerificationStatus.VERIFIED, dl_expiry_date__range=(today, today + timedelta(days=30))
        ).count()
        stuck = trips.filter(status=TripStatus.IN_PROGRESS, started_at__lt=now - timedelta(hours=3)).count()
        issues = TripItem.objects.filter(trip__in=in_period, status="not_delivered").count()

        photos = []
        for trip in trips.exclude(pickup_photo_url="", delivery_photo_url="").order_by("-updated_at")[:6]:
            for kind, url in (("Pickup", trip.pickup_photo_url), ("Delivery", trip.delivery_photo_url)):
                if url:
                    photos.append({"url": url, "label": f"{trip.order_number or ''} · {kind}", "trip": trip})
        for item in (
            TripItem.objects.filter(trip__in=trips).exclude(proof_image_url="").select_related("trip")
            .order_by("-verified_at")[:6]
        ):
            photos.append({"url": item.proof_image_url, "label": f"{item.trip.order_number or ''} · {item.name}", "trip": item.trip})

        def trip_list(**params):
            query = "&".join(f"{k}={v}" for k, v in params.items())
            return reverse(f"{self.name}:trips_trip_changelist") + (f"?{query}" if query else "")

        driver_list = reverse(f"{self.name}:drivers_driver_changelist")

        return {
            "period": period,
            "period_label": label,
            "periods": [(key, name) for key, (name, _) in PERIODS.items()],
            "companies": companies,
            "company_id": str(company_id or ""),
            "kpis": [
                {"label": "Orders", "value": total, "hint": label, "url": trip_list()},
                {"label": "Completed", "value": counts["completed"],
                 "hint": f"{round(100 * counts['completed'] / total) if total else 0}% of orders",
                 "url": trip_list(status__exact="completed")},
                {"label": "Active now", "value": trips.filter(status__in=ACTIVE).count(), "hint": "on the road",
                 "url": trip_list(status__in="assigned,arrived_at_pickup,in_progress")},
                {"label": "Cancelled", "value": counts["cancelled"],
                 "hint": f"{round(100 * counts['cancelled'] / total) if total else 0}% of orders",
                 "url": trip_list(status__exact="cancelled"), "tone": "red" if counts["cancelled"] else ""},
                {"label": "Order value", "value": f"₹{(money['gmv'] or 0):,.0f}", "hint": "completed orders"},
                {"label": "Driver earnings", "value": f"₹{(money['earnings'] or 0):,.0f}",
                 "hint": f"incl. ₹{(money['bonus'] or 0):,.0f} bonus"},
                {"label": "COD collected", "value": f"₹{(cod or 0):,.0f}", "hint": "paid by QR"},
                {"label": "Avg delivery", "value": _minutes(durations), "hint": "pickup → drop"},
                {"label": "Drivers online", "value": drivers.filter(is_online=True).count(),
                 "hint": f"of {drivers.count()} drivers", "url": f"{driver_list}?is_online__exact=1"},
            ],
            "attention": [
                row for row in [
                    {"label": "KYC documents waiting for review", "n": pending_kyc, "url": f"{driver_list}?review=pending"},
                    {"label": "Driving licences expiring within 30 days", "n": expiring, "url": f"{driver_list}?review=expiring"},
                    {"label": "Orders with no driver available", "n": counts["unassigned"], "url": trip_list(status__exact="no_driver_available")},
                    {"label": "Deliveries in progress for over 3 hours", "n": stuck, "url": trip_list(status__exact="in_progress")},
                    {"label": "Items reported not delivered", "n": issues,
                     "url": reverse(f"{self.name}:trips_tripitem_changelist") + "?status__exact=not_delivered"},
                ] if row["n"]
            ],
            "series": series,
            "series_last_day": series[-1]["day"],
            "series_peak": peak,
            "by_status": by_status,
            "by_type": by_type,
            "top_drivers": top_drivers,
            "recent_trips": in_period.select_related("driver", "vehicle_type").order_by("-created_at")[:8],
            "photos": photos[:12],
        }


def _minutes(duration):
    if not duration:
        return "—"
    minutes = int(duration.total_seconds() // 60)
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes} min"
