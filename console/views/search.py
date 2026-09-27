from django.db.models import Q
from django.shortcuts import redirect

from drivers.models import Driver, Vehicle
from trips.models import Trip

from ..access import console_view, scoped
from .common import page


@console_view()
def search(request):
    """One box for everything: an exact order number jumps straight to it;
    otherwise matching orders, drivers and vehicles side by side."""
    q = (request.GET.get("q") or "").strip()
    if not q:
        return redirect("console:dashboard")
    orders = scoped(request, Trip.objects.select_related("driver")).filter(
        Q(order_number__icontains=q) | Q(reference_id__icontains=q) | Q(invoice_number__icontains=q)
        | Q(drop_contact_name__icontains=q) | Q(drop_contact_phone__icontains=q) | Q(pickup_contact_name__icontains=q)
        | Q(pickup_contact_phone__icontains=q) | Q(drop_address__icontains=q)).order_by("-created_at")
    exact = orders.filter(order_number__iexact=q).first()
    if exact:
        return redirect("console:order", pk=exact.pk)
    drivers = scoped(request, Driver.objects.all()).filter(
        Q(full_name__icontains=q) | Q(phone_number__icontains=q) | Q(email__icontains=q) | Q(kyc__dl_number__icontains=q))
    vehicles = scoped(request, Vehicle.objects.select_related("vehicle_type")).filter(registration_number__icontains=q.replace(" ", ""))
    return page(request, "console/search.html", None, search_q=q, orders=orders[:20], n_orders=orders.count(),
                drivers=drivers[:12], vehicles=vehicles[:12])
