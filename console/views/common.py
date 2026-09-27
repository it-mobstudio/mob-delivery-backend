import csv
from datetime import datetime, time, timedelta

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

PERIODS = {"today": ("Today", 1), "7d": ("7 days", 7), "30d": ("30 days", 30), "90d": ("90 days", 90)}


def page(request, template, section, **context):
    return render(request, template, {"section": section, **context})


def paginate(request, queryset, default=25):
    try:
        size = int(request.GET.get("size", default))
    except ValueError:
        size = default
    size = size if size in (10, 25, 50, 100) else default
    paginator = Paginator(queryset, size)
    return paginator.get_page(request.GET.get("page"))


def day_start(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def period_range(request, default="7d"):
    """(key, label, start_date, end_date inclusive, previous (start, end))
    from ?period=… or a custom ?from=…&to=…"""
    today = timezone.localdate()
    start, end = parse_date(request.GET.get("from")), parse_date(request.GET.get("to"))
    if start or end:
        start, end = start or end, end or today
        if start > end:
            start, end = end, start
        days = (end - start).days + 1
        key, label = "custom", f"{start:%d %b} – {end:%d %b}"
    else:
        key = request.GET.get("period") if request.GET.get("period") in PERIODS else default
        label, days = PERIODS[key]
        end, start = today, today - timedelta(days=days - 1)
    prev = (start - timedelta(days=days), start - timedelta(days=1))
    return key, label, start, end, prev


def csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="{filename}-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return response


def without(request, *keys):
    """The current URL minus some filters (for the removable filter chips)."""
    params = request.GET.copy()
    for key in (*keys, "page"):
        params.pop(key, None)
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


def delta(current, previous):
    """(text, direction) comparing two numbers, for the KPI cards."""
    current, previous = float(current or 0), float(previous or 0)
    if previous == 0:
        return ("new", "up") if current else ("—", "flat")
    change = round(100 * (current - previous) / previous)
    return (f"{'+' if change > 0 else ''}{change}%", "up" if change > 0 else "down" if change < 0 else "flat")
