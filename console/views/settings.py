from django.contrib import messages
from django.db.models import Count, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.models import AdminUser, ApiClient, Company
from core.choices import AdminRole, ApiClientStatus, CompanyStatus, TripStatus

from ..access import console_view, is_platform
from .common import page


@console_view(platform=True)
def companies(request):
    if request.method == "POST":
        c = get_object_or_404(Company, pk=request.POST.get("id"))
        if request.POST.get("status") in CompanyStatus.values:
            c.status = request.POST["status"]
            c.save(update_fields=["status", "updated_at"])
            messages.success(request, f"{c.name} is now {c.get_status_display().lower()}.")
        return redirect("console:companies")
    from drivers.models import Driver
    from trips.models import Trip

    def count(qs):
        return Subquery(qs.filter(company_id=OuterRef("pk")).values("company_id").annotate(n=Count("id")).values("n"))

    rows = Company.objects.annotate(
        n_drivers=count(Driver.objects.all()),
        n_online=count(Driver.objects.filter(is_online=True)),
        n_orders=count(Trip.objects.all()),
        n_done=count(Trip.objects.filter(status=TripStatus.COMPLETED)),
    ).order_by("name")
    return page(request, "console/settings/companies.html", "companies", rows=rows, statuses=CompanyStatus.choices)


@console_view(team=True)
def team(request):
    users = AdminUser.objects.select_related("company").order_by("company__name", "email")
    if not is_platform(request.user):
        users = users.filter(company_id=request.user.company_id)
    return page(request, "console/settings/team.html", "team", users=users, roles=AdminRole.choices,
                companies=Company.objects.order_by("name") if is_platform(request.user) else None)


@console_view(team=True)
@require_POST
def team_save(request):
    """Add a teammate, or change / switch off an existing one."""
    platform = is_platform(request.user)
    role = request.POST.get("role") if request.POST.get("role") in AdminRole.values else AdminRole.STAFF
    if request.POST.get("id"):
        u = get_object_or_404(AdminUser if platform else AdminUser.objects.filter(company_id=request.user.company_id),
                              pk=request.POST["id"])
        if u.pk == request.user.pk and request.POST.get("active") != "1":
            messages.error(request, "You can't switch off your own login.")
            return redirect("console:team")
        u.role, u.is_active = role, request.POST.get("active") == "1"
        if request.POST.get("password"):
            u.set_password(request.POST["password"])
        u.save()
        messages.success(request, f"Saved {u.email}.")
        return redirect("console:team")
    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    if not email or len(password) < 8:
        messages.error(request, "Enter an email and a password of at least 8 characters.")
        return redirect("console:team")
    if AdminUser.objects.filter(email__iexact=email).exists():
        messages.error(request, f"{email} already has a login.")
        return redirect("console:team")
    company_id = request.POST.get("company") if platform and request.POST.get("company") else request.user.company_id
    AdminUser.objects.create_user(email=email, company=Company.objects.get(pk=company_id), password=password,
                                  first_name=(request.POST.get("first_name") or "").strip(),
                                  last_name=(request.POST.get("last_name") or "").strip(), role=role, is_staff=True)
    messages.success(request, f"{email} can now sign in to the console.")
    return redirect("console:team")


@console_view(platform=True)
def api_clients(request):
    if request.method == "POST":
        c = get_object_or_404(ApiClient, pk=request.POST.get("id"))
        c.status = ApiClientStatus.INACTIVE if request.POST.get("action") == "revoke" else ApiClientStatus.ACTIVE
        c.save(update_fields=["status", "updated_at"])
        messages.success(request, f"{c.name} {'revoked' if c.status == ApiClientStatus.INACTIVE else 're-enabled'}.")
        return redirect("console:api_clients")
    return page(request, "console/settings/api_clients.html", "api",
                rows=ApiClient.objects.select_related("company").order_by("company__name", "name"))
