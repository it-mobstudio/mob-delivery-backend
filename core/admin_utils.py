"""Shared pieces of the operations admin (/admin/): company scoping and
role-based access, image previews, status badges and CSV export."""

import csv

from django.contrib import admin
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.html import format_html, format_html_join

from core.choices import AdminRole

# -- access ------------------------------------------------------------------------


def is_platform_admin(user):
    return bool(user.is_active and user.is_superuser)


def company_role(user):
    return getattr(user, "role", None)


class CompanyScopedAdmin(admin.ModelAdmin):
    """Everything an operator of one company may see is that company's own
    data. A superuser (the platform) sees every company.

    Access follows AdminUser.role rather than per-model permissions, so a
    company's operators don't need permission wiring to be useful:
    owner / admin manage their company's data, staff can only look.
    """

    #: The path from this model to its Company, for filtering.
    company_field = "company"
    list_per_page = 50

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if is_platform_admin(request.user):
            return qs
        return qs.filter(**{self.company_field: request.user.company_id})

    def _is_operator(self, request):
        return request.user.is_active and request.user.is_staff

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or self._is_operator(request)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user) or self._is_operator(request)

    def has_change_permission(self, request, obj=None):
        if is_platform_admin(request.user):
            return True
        return self._is_operator(request) and company_role(request.user) in (AdminRole.OWNER, AdminRole.ADMIN)

    def has_add_permission(self, request):
        return self.has_change_permission(request)

    def has_delete_permission(self, request, obj=None):
        # Deleting operational records breaks history (wallets, deliveries).
        # Only the platform may, and most admins below switch it off entirely.
        return is_platform_admin(request.user)

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if not is_platform_admin(request.user):
            filters = [f for f in filters if f not in ("company", self.company_field)]
        return filters

    def get_list_display(self, request):
        cols = list(super().get_list_display(request))
        if not is_platform_admin(request.user) and "company" in cols:
            cols.remove("company")
        return cols

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "company_id") and not obj.company_id:
            obj.company_id = request.user.company_id
        super().save_model(request, obj, form, change)


# -- presentation -------------------------------------------------------------------

BADGE_TONES = {
    # trips
    "requested": "blue", "assigned": "blue", "arrived_at_pickup": "amber", "in_progress": "violet",
    "completed": "green", "cancelled": "red", "no_driver_available": "red",
    # verification / accounts / items
    "pending": "amber", "verified": "green", "rejected": "red", "active": "green",
    "inactive": "grey", "disabled": "grey", "suspended": "red", "maintenance": "amber",
    "locked_dl_expired": "red", "delivered": "green", "not_delivered": "red",
    "paid": "green", "approved": "green", "under_review": "amber", "action_required": "red",
    "profile_incomplete": "grey", "documents_required": "amber",
    # wallet
    "trip_earning": "green", "bonus": "green", "penalty": "red", "payout": "blue", "adjustment": "grey",
}


def badge(value, label=None, tone=None):
    if value in (None, ""):
        return mark_safe('<span class="mob-muted">—</span>')
    tone = tone or BADGE_TONES.get(str(value), "grey")
    return format_html('<span class="mob-badge mob-badge--{}">{}</span>', tone, label or str(value).replace("_", " ").capitalize())


def is_pdf(url):
    return str(url).lower().split("?")[0].endswith(".pdf")


def thumb(url, size=56, title=""):
    """A clickable preview of an uploaded picture (opens full size in a new
    tab); a PDF gets a document chip instead."""
    if not url:
        return mark_safe('<span class="mob-muted">—</span>')
    if is_pdf(url):
        return format_html('<a class="mob-doc" href="{}" target="_blank" rel="noopener">PDF ↗</a>', url)
    return format_html(
        '<a href="{0}" target="_blank" rel="noopener" title="{2}">'
        '<img class="mob-thumb" src="{0}" loading="lazy" style="width:{1}px;height:{1}px" alt="{2}"></a>',
        url, size, title or "photo",
    )


def gallery(items, size=150):
    """(label, url) pairs as a row of captioned previews, skipping empty ones."""
    items = [(label, url) for label, url in items if url]
    if not items:
        return mark_safe('<span class="mob-muted">No photos yet</span>')
    return format_html(
        '<div class="mob-gallery">{}</div>',
        format_html_join(
            "", '<figure>{}<figcaption>{}</figcaption></figure>', ((thumb(url, size, label), label) for label, url in items)
        ),
    )


def map_link(lat, lng, label="Open in Maps"):
    if lat is None or lng is None:
        return mark_safe('<span class="mob-muted">—</span>')
    return format_html(
        '<a href="https://www.google.com/maps/search/?api=1&query={},{}" target="_blank" rel="noopener">{} ↗</a>',
        lat, lng, label,
    )


def admin_link(obj, label=None):
    if obj is None:
        return mark_safe('<span class="mob-muted">—</span>')
    url = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
    return format_html('<a href="{}">{}</a>', url, label or str(obj))


def money(value, currency="₹"):
    if value is None:
        return "—"
    return f"{currency}{value:,.2f}"


# -- export -------------------------------------------------------------------------


def export_csv_action(fields, filename):
    """An admin action writing the selected rows as CSV. `fields` are
    (header, getter) pairs; a getter is an attribute path or a callable."""

    def value_of(obj, getter):
        if callable(getter):
            return getter(obj)
        for part in getter.split("."):
            obj = getattr(obj, part, None) if obj is not None else None
        return obj

    def action(modeladmin, request, queryset):
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}-{stamp}.csv"'
        writer = csv.writer(response)
        writer.writerow([header for header, _ in fields])
        for obj in queryset.iterator():
            writer.writerow(["" if (v := value_of(obj, getter)) is None else v for _, getter in fields])
        return response

    action.short_description = "Export selected to CSV"
    action.__name__ = f"export_{filename.replace('-', '_')}_csv"
    return action
