from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.choices import VehicleStatus, VehicleTypeStatus
from drivers.models import Driver, Vehicle, VehicleType

from ..access import console_view, scoped
from .common import page, paginate


@console_view()
def vehicles(request):
    rows = scoped(request, Vehicle.objects.select_related("vehicle_type", "owner_driver")).annotate(
        n_photos=Count("photos", distinct=True), n_docs=Count("documents", distinct=True))
    q = (request.GET.get("q") or "").strip()
    if q:
        rows = rows.filter(Q(registration_number__icontains=q.replace(" ", "")) | Q(owner_driver__full_name__icontains=q)
                           | Q(owner_driver__phone_number__icontains=q))
    if request.GET.get("type"):
        rows = rows.filter(vehicle_type_id=request.GET["type"])
    if request.GET.get("status") in VehicleStatus.values:
        rows = rows.filter(status=request.GET["status"])
    drivers_on = dict(Driver.objects.filter(is_online=True, current_vehicle_id__in=rows.values("id"))
                      .values_list("current_vehicle_id", "full_name"))
    return page(request, "console/fleet/vehicles.html", "vehicles", page_obj=paginate(request, rows.order_by("registration_number")),
                types=scoped(request, VehicleType.objects.all()).order_by("name"), statuses=VehicleStatus.choices,
                drivers_on=drivers_on)


@console_view(edit=True)
def vehicle(request, pk):
    v = get_object_or_404(scoped(request, Vehicle.objects.select_related("vehicle_type", "owner_driver")), pk=pk)
    if request.method == "POST":
        if request.POST.get("status") in VehicleStatus.values:
            v.status = request.POST["status"]
            v.save(update_fields=["status", "updated_at"])
            messages.success(request, f"{v.registration_number} is now {v.get_status_display().lower()}.")
        return redirect("console:vehicle", pk=pk)
    today = timezone.localdate()
    docs = [{"doc": d, "days": (d.expiry_date - today).days if d.expiry_date else None} for d in v.documents.all()]
    photos = ([("Main photo", v.photo_url)] if v.photo_url else []) + [(f"Photo {i + 1}", p.url) for i, p in enumerate(v.photos.all())]
    driving = Driver.objects.filter(pk=v.current_driver_id, is_online=True).first() if v.current_driver_id else None
    return page(request, "console/fleet/vehicle.html", "vehicles", v=v, docs=docs, photos=photos, driving=driving,
                statuses=VehicleStatus.choices, trips=v.trips.select_related("driver").order_by("-created_at")[:10])


@console_view(edit=True)
def fares(request):
    types = scoped(request, VehicleType.objects.all()).annotate(n_vehicles=Count("vehicles", distinct=True)).order_by("name")
    if request.method == "POST":
        vt = get_object_or_404(types, pk=request.POST.get("id"))
        try:
            for field in ("base_fare", "per_km_rate", "per_min_rate", "min_fare"):
                value = Decimal(request.POST.get(field))
                if value < 0:
                    raise InvalidOperation
                setattr(vt, field, value)
        except (InvalidOperation, TypeError):
            messages.error(request, "Fares must be numbers of 0 or more.")
            return redirect("console:fares")
        if request.POST.get("status") in VehicleTypeStatus.values:
            vt.status = request.POST["status"]
        vt.save()
        messages.success(request, f"{vt.name} fare card saved. New bookings use it straight away.")
        return redirect("console:fares")
    types = list(types)
    for t in types:  # what a typical 5 km / 15 min trip costs, to sanity-check a fare card
        t.example = max(t.base_fare + 5 * t.per_km_rate + 15 * t.per_min_rate, t.min_fare)
    return page(request, "console/fleet/fares.html", "fares", types=types, statuses=VehicleTypeStatus.choices)
