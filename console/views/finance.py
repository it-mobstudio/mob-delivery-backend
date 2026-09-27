from datetime import timedelta

from django.db.models import OuterRef, Q, Subquery, Sum
from django.utils import timezone

from core.choices import WalletTransactionKind
from drivers.models import Driver, WalletTransaction

from ..access import console_view, scoped
from .common import csv_response, day_start, page, paginate, parse_date


@console_view()
def payouts(request):
    """Who is owed what, and paying them."""
    balance = Subquery(WalletTransaction.objects.filter(driver=OuterRef("pk")).values("driver")
                       .annotate(t=Sum("amount")).values("t"))
    last_payout = Subquery(WalletTransaction.objects.filter(driver=OuterRef("pk"), kind=WalletTransactionKind.PAYOUT)
                           .order_by("-created_at").values("created_at")[:1])
    rows = scoped(request, Driver.objects.all()).annotate(balance=balance, last_payout=last_payout)
    q = (request.GET.get("q") or "").strip()
    if q:
        rows = rows.filter(Q(full_name__icontains=q) | Q(phone_number__icontains=q))
    if request.GET.get("owed", "1") == "1":
        rows = rows.filter(balance__gt=0)
    rows = rows.order_by("-balance")
    ledger = scoped(request, WalletTransaction.objects.all())
    month = timezone.now() - timedelta(days=30)
    summary = {
        "owed": rows.aggregate(s=Sum("balance"))["s"] or 0,
        "paid_30": -(ledger.filter(kind=WalletTransactionKind.PAYOUT, created_at__gte=month).aggregate(s=Sum("amount"))["s"] or 0),
        "earned_30": ledger.filter(kind__in=[WalletTransactionKind.TRIP_EARNING, WalletTransactionKind.BONUS],
                                   created_at__gte=month).aggregate(s=Sum("amount"))["s"] or 0,
        "drivers_owed": rows.count(),
    }
    return page(request, "console/finance/payouts.html", "payouts", page_obj=paginate(request, rows), summary=summary)


@console_view()
def ledger(request):
    rows = scoped(request, WalletTransaction.objects.select_related("driver")).order_by("-created_at")
    g = request.GET
    if g.get("kind") in WalletTransactionKind.values:
        rows = rows.filter(kind=g["kind"])
    q = (g.get("q") or "").strip()
    if q:
        rows = rows.filter(Q(driver__full_name__icontains=q) | Q(driver__phone_number__icontains=q) | Q(reference__icontains=q)
                           | Q(description__icontains=q))
    start, end = parse_date(g.get("from")), parse_date(g.get("to"))
    if start:
        rows = rows.filter(created_at__gte=day_start(start))
    if end:
        rows = rows.filter(created_at__lt=day_start(end + timedelta(days=1)))
    if g.get("export") == "csv":
        return csv_response("wallet-ledger", ["When", "Driver", "Phone", "Type", "Amount", "Balance after", "Description", "Reference", "Trip"],
                            ([w.created_at, w.driver.full_name, w.driver.phone_number, w.kind, w.amount, w.balance_after,
                              w.description, w.reference, w.trip_id] for w in rows.iterator()))
    totals = rows.aggregate(credit=Sum("amount", filter=Q(amount__gt=0)), debit=Sum("amount", filter=Q(amount__lt=0)))
    return page(request, "console/finance/ledger.html", "ledger", page_obj=paginate(request, rows),
                kinds=WalletTransactionKind.choices, totals=totals)
