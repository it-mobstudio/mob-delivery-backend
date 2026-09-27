from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.choices import DriverAccountStatus, TripStatus, VehicleCategory, VerificationStatus, WalletTransactionKind
from core.exceptions import DomainError
from drivers.models import Driver, Vehicle, WalletTransaction
from drivers.services import DriverKycService, DriverService
from drivers.wallet import WalletService
from trips.models import Trip

from ..access import console_view, scoped
from ..context import ACTIVE, PENDING_KYC
from .common import csv_response, page, paginate
from .common import without as _without

DOCS = {
    "aadhar": ("Aadhaar", DriverKycService.verify_aadhar),
    "dl": ("Driving licence", DriverKycService.verify_dl),
    "police": ("Police verification", DriverKycService.verify_police),
}


def _balance_subquery():
    return Subquery(
        WalletTransaction.objects.filter(driver=OuterRef("pk")).values("driver").annotate(t=Sum("amount")).values("t")
    )


def _tabs(request, base):
    today = timezone.localdate()
    busy = Trip.objects.filter(status__in=ACTIVE, driver_id__isnull=False).values("driver_id")
    return [
        ("all", "All", base),
        ("online", "On duty", base.filter(is_online=True)),
        ("on_trip", "On a trip", base.filter(id__in=busy)),
        ("kyc", "KYC to review", base.filter(PENDING_KYC)),
        ("expiring", "Licence expiring", base.filter(kyc__dl_status=VerificationStatus.VERIFIED,
                                                     kyc__dl_expiry_date__range=(today, today + timedelta(days=30)))),
        ("blocked", "Blocked", base.exclude(account_status=DriverAccountStatus.ACTIVE)),
    ]


@console_view()
def drivers(request):
    base = scoped(request, Driver.objects.all())
    q = (request.GET.get("q") or "").strip()
    if q:
        base = base.filter(Q(full_name__icontains=q) | Q(phone_number__icontains=q) | Q(email__icontains=q)
                           | Q(kyc__dl_number__icontains=q) | Q(city__icontains=q) | Q(pincode__icontains=q))
    if request.GET.get("city"):
        base = base.filter(city__iexact=request.GET["city"])
    tabs = _tabs(request, base)
    tab = request.GET.get("tab") if request.GET.get("tab") in [t[0] for t in tabs] else "all"
    rows = dict((k, qs) for k, _, qs in tabs)[tab].distinct().select_related("kyc", "company").annotate(
        balance=_balance_subquery(),
        done=Count("trips", filter=Q(trips__status=TripStatus.COMPLETED), distinct=True),
    )
    sort = {"name": "full_name", "trips": "-done", "balance": "-balance", "seen": "-last_location_at", "new": "-created_at"}
    rows = rows.order_by(sort.get(request.GET.get("sort"), "-is_online"), "full_name")

    if request.GET.get("export") == "csv":
        return csv_response("drivers", ["Name", "Phone", "Email", "City", "Company", "Account", "Onboarding", "On duty",
                                        "Aadhaar", "Licence", "Licence expiry", "Police", "Deliveries", "Wallet", "UPI", "Joined"],
                            ([d.full_name, d.phone_number, d.email, d.city, d.company.name, d.account_status, d.onboarding_status,
                              d.is_online, d.kyc.aadhar_status, d.kyc.dl_status, d.kyc.dl_expiry_date, d.kyc.police_status,
                              d.done, d.balance or 0, d.payout_upi_id, d.created_at] for d in rows))
    cities = scoped(request, Driver.objects.exclude(city="")).values_list("city", flat=True).distinct().order_by("city")
    busy_ids = set(Trip.objects.filter(status__in=ACTIVE, driver__in=rows).values_list("driver_id", flat=True))
    return page(request, "console/drivers/list.html", "drivers", page_obj=paginate(request, rows),
                tabs=[(k, label, qs.distinct().count()) for k, label, qs in tabs], tab=tab, cities=cities,
                busy_ids=busy_ids, search_q=q, clear_url=_without(request, "q", "city"))


@console_view()
def driver(request, pk):
    d = get_object_or_404(scoped(request, Driver.objects.select_related("kyc", "company")), pk=pk)
    trips = d.trips.select_related("vehicle_type").order_by("-created_at")
    stats = trips.aggregate(
        done=Count("id", filter=Q(status=TripStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=TripStatus.CANCELLED, cancelled_by="driver")),
        total=Count("id"), earned=Sum("driver_earning"),
    )
    week = timezone.now() - timedelta(days=7)
    stats["week"] = trips.filter(status=TripStatus.COMPLETED, completed_at__gte=week).aggregate(e=Sum("driver_earning"))["e"]
    active_trip = trips.filter(status__in=ACTIVE).first()
    kyc = d.kyc
    docs = [
        {"key": "aadhar", "title": "Aadhaar", "status": kyc.aadhar_status, "note": kyc.aadhar_rejection_note,
         "when": kyc.aadhar_verified_at, "imgs": [("Front", kyc.aadhar_doc_url), ("Back", kyc.aadhar_back_doc_url)],
         "facts": [("Number", f"XXXX XXXX {kyc.aadhar_number_last4}" if kyc.aadhar_number_last4 else "—")]},
        {"key": "dl", "title": "Driving licence", "status": kyc.dl_status, "note": kyc.dl_rejection_note,
         "when": kyc.dl_verified_at, "imgs": [("Front", kyc.dl_doc_url), ("Back", kyc.dl_back_doc_url)],
         "facts": [("Number", kyc.dl_number or "—"), ("Expires", kyc.dl_expiry_date or "—"),
                   ("Allowed", ", ".join(kyc.dl_allowed_categories or []) or "—")]},
        {"key": "police", "title": "Police verification", "status": kyc.police_status, "note": kyc.police_rejection_note,
         "when": kyc.police_verified_at, "imgs": [("Certificate", kyc.police_doc_url)], "facts": []},
    ]
    for doc in docs:
        doc["imgs"] = [(label, url, str(url).lower().split("?")[0].endswith(".pdf")) for label, url in doc["imgs"] if url]
    tab = request.GET.get("tab", "overview")
    return page(
        request, "console/drivers/detail.html", "drivers", d=d, kyc=kyc, docs=docs, stats=stats,
        active_trip=active_trip, tab=tab, balance=WalletService.balance(d),
        trips_page=paginate(request, trips, 10) if tab == "trips" else None, recent=trips[:6],
        ledger=d.wallet_transactions.order_by("-created_at")[:50],
        vehicles=Vehicle.objects.filter(Q(owner_driver=d) | Q(pk=d.current_vehicle_id)).select_related("vehicle_type").distinct(),
        current_vehicle=Vehicle.objects.filter(pk=d.current_vehicle_id).first() if d.current_vehicle_id else None,
        categories=VehicleCategory.choices, manual_kinds=[k for k in WalletTransactionKind.choices if k[0] != "trip_earning"],
    )


def _driver_for_action(request, pk):
    return get_object_or_404(scoped(request, Driver.objects.select_related("kyc")), pk=pk)


@console_view(edit=True)
@require_POST
def driver_kyc(request, pk):
    """Approve or reject one document (or all three at once)."""
    d = _driver_for_action(request, pk)
    doc, decision = request.POST.get("doc"), request.POST.get("decision")
    note = (request.POST.get("note") or "").strip() or None
    status = VerificationStatus.VERIFIED if decision == "approve" else VerificationStatus.REJECTED
    if status == VerificationStatus.REJECTED and not note:
        messages.error(request, "Say why the document is rejected — the driver sees this note.")
        return redirect(request.POST.get("next") or f"/admin/drivers/{pk}/?tab=kyc")
    keys = list(DOCS) if doc == "all" else [doc] if doc in DOCS else []
    for key in keys:
        _, verify = DOCS[key]
        if key == "dl" and status == VerificationStatus.VERIFIED:
            expiry = request.POST.get("dl_expiry") or d.kyc.dl_expiry_date
            if not expiry:
                messages.error(request, "Enter the licence's expiry date to approve it.")
                return redirect(request.POST.get("next") or f"/admin/drivers/{pk}/?tab=kyc")
            if isinstance(expiry, str):
                from datetime import date

                expiry = date.fromisoformat(expiry)
            cats = request.POST.getlist("dl_categories") or d.kyc.dl_allowed_categories or [c for c, _ in VehicleCategory.choices]
            verify(d, status, request.user.pk, note=note, expiry_date=expiry, allowed_categories=cats)
        else:
            verify(d, status, request.user.pk, note=note)
    label = "All documents" if doc == "all" else DOCS.get(doc, ("Document",))[0]
    messages.success(request, f"{label} {'approved' if status == VerificationStatus.VERIFIED else 'rejected'} for {d.full_name}.")
    return redirect(request.POST.get("next") or f"/admin/drivers/{pk}/?tab=kyc")


@console_view(edit=True)
@require_POST
def driver_status(request, pk):
    d = _driver_for_action(request, pk)
    action = request.POST.get("action")
    try:
        if action == "offline":
            DriverService.go_offline(d)
            messages.success(request, f"{d.full_name} taken off duty.")
        elif action == "block":
            if DriverService.has_active_trip(d):
                raise DomainError("DRIVER_HAS_ACTIVE_TRIP", "They're on a trip right now — block them once it ends.")
            d.account_status, d.is_online = DriverAccountStatus.DISABLED, False
            d.save(update_fields=["account_status", "is_online"])
            messages.success(request, f"{d.full_name} blocked. They can't go on duty or take orders.")
        elif action == "unblock":
            d.account_status = DriverAccountStatus.ACTIVE
            d.save(update_fields=["account_status"])
            messages.success(request, f"{d.full_name} can work again.")
    except DomainError as exc:
        messages.error(request, exc.detail)
    return redirect("console:driver", pk=pk)


@console_view(edit=True)
@require_POST
def driver_wallet(request, pk):
    d = _driver_for_action(request, pk)
    try:
        amount = Decimal(request.POST.get("amount") or "0")
        WalletService.record_manual(
            d, request.POST.get("kind"), amount, description=(request.POST.get("description") or "").strip()[:255],
            reference=(request.POST.get("reference") or "").strip()[:100], created_by=request.user.pk,
        )
        messages.success(request, f"Recorded. {d.full_name}'s balance is now ₹{WalletService.balance(d):,.2f}.")
    except (DomainError, InvalidOperation) as exc:
        messages.error(request, getattr(exc, "detail", "Enter a valid amount."))
    return redirect(request.POST.get("next") or f"/admin/drivers/{pk}/?tab=wallet")


@console_view()
def kyc_queue(request):
    rows = scoped(request, Driver.objects.select_related("kyc")).filter(PENDING_KYC).distinct().order_by("kyc__updated_at")
    items = []
    for d in paginate(request, rows, 10):
        k = d.kyc
        pending = []
        for key, title, status, imgs in [
            ("aadhar", "Aadhaar", k.aadhar_status, [("Front", k.aadhar_doc_url), ("Back", k.aadhar_back_doc_url)]),
            ("dl", "Driving licence", k.dl_status, [("Front", k.dl_doc_url), ("Back", k.dl_back_doc_url)]),
            ("police", "Police verification", k.police_status, [("Certificate", k.police_doc_url)]),
        ]:
            imgs = [(label, url, str(url).lower().split("?")[0].endswith(".pdf")) for label, url in imgs if url]
            if status == VerificationStatus.PENDING and imgs:
                facts = []
                if key == "dl":
                    facts = [("Number", k.dl_number or "—"), ("Expires", k.dl_expiry_date or "—")]
                if key == "aadhar" and k.aadhar_number_last4:
                    facts = [("Number", f"XXXX XXXX {k.aadhar_number_last4}")]
                pending.append({"key": key, "title": title, "imgs": imgs, "status": status, "facts": facts})
        vehicles = [(veh, ([veh.photo_url] if veh.photo_url else []) + [p.url for p in veh.photos.all()])
                    for veh in d.own_vehicles.select_related("vehicle_type").prefetch_related("photos")]
        items.append((d, pending, vehicles))
    return page(request, "console/drivers/kyc.html", "kyc", items=items, page_obj=paginate(request, rows, 10),
                categories=VehicleCategory.choices)


@console_view(edit=True)
@require_POST
def kyc_reject(request):
    """The shared reject dialog posts the driver's id with the form."""
    return driver_kyc(request, request.POST.get("driver"))
