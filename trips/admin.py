from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.admin_utils import (
    CompanyScopedAdmin,
    admin_link,
    badge,
    export_csv_action,
    gallery,
    is_platform_admin,
    map_link,
    money,
    thumb,
)
from core.choices import CancelledBy, TripStatus
from core.exceptions import DomainError

from .models import Trip, TripItem
from .services import TripService

TRIP_CSV = export_csv_action(
    [
        ("Order", "order_number"), ("Status", "status"), ("Company", "company.name"), ("Reference", "reference_id"),
        ("Driver", "driver.full_name"), ("Driver phone", "driver.phone_number"), ("Vehicle", "vehicle.registration_number"),
        ("Vehicle type", "vehicle_type.name"), ("Pickup", "pickup_address"), ("Pickup contact", "pickup_contact_name"),
        ("Drop", "drop_address"), ("Customer", "drop_contact_name"), ("Customer phone", "drop_contact_phone"),
        ("Distance km", lambda t: round(t.distance_meters / 1000, 2) if t.distance_meters else ""),
        ("Fare", "total_fare"), ("Bonus", "bonus_fare"), ("Driver earning", "driver_earning"),
        ("Payment", "payment_mode"), ("Payment status", "payment_status"), ("Invoice", "invoice_number"),
        ("Created", "created_at"), ("Assigned", "assigned_at"), ("Started", "started_at"),
        ("Completed", "completed_at"), ("Cancelled", "cancelled_at"), ("Cancellation reason", "cancellation_reason"),
    ],
    "orders",
)


class TripItemInline(admin.TabularInline):
    model = TripItem
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ("picture", "name", "quantity", "unit", "sku", "state", "photos", "driver_note", "verified_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="")
    def picture(self, item):
        return thumb(item.image_url, 44, item.name)

    @admin.display(description="Status")
    def state(self, item):
        return badge(item.status)

    @admin.display(description="Proof photos")
    def photos(self, item):
        return gallery(
            [("Pickup", item.pickup_photo_url), ("Delivery", item.delivery_photo_url), ("Checked", item.proof_image_url)],
            size=64,
        )


class PhotosFilter(admin.SimpleListFilter):
    title = "proof photos"
    parameter_name = "photos"

    def lookups(self, request, model_admin):
        return [("any", "Has photos"), ("missing", "Owes photos")]

    def queryset(self, request, queryset):
        has_any = ~Q(pickup_photo_url="") | ~Q(delivery_photo_url="")
        if self.value() == "any":
            return queryset.filter(has_any)
        if self.value() == "missing":
            return queryset.filter(
                (~Q(pickup_photo="none") & Q(pickup_photo_url="") & Q(started_at__isnull=False))
                | (~Q(delivery_photo="none") & Q(delivery_photo_url="") & Q(status=TripStatus.COMPLETED))
            )
        return queryset


@admin.register(Trip)
class TripAdmin(CompanyScopedAdmin):
    list_display = (
        "order", "state", "company", "driver_col", "route", "fare", "payment", "proofs", "created_at",
    )
    list_display_links = ("order",)
    list_filter = (
        "status", "payment_mode", "payment_status", "vehicle_type", "verify_items", "pickup_photo",
        "delivery_photo", "delivery_otp", PhotosFilter, "created_at", "company",
    )
    search_fields = (
        "order_number", "reference_id", "invoice_number", "=id",
        "pickup_address", "pickup_contact_name", "pickup_contact_phone",
        "drop_address", "drop_contact_name", "drop_contact_phone",
        "driver__full_name", "driver__phone_number", "vehicle__registration_number",
    )
    search_help_text = "Order number, reference, invoice, address, contact name/phone, driver or vehicle number"
    date_hierarchy = "created_at"
    list_select_related = ("driver", "vehicle_type", "company", "vehicle")
    inlines = [TripItemInline]
    actions = ["cancel_orders", "retry_assignment", TRIP_CSV]
    ordering = ("-created_at",)
    # What a person may still change on a booked order; everything else is the
    # record of what happened and stays as the system wrote it.
    editable = ("notes", "invoice_url", "invoice_number")

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "notes":
            from django import forms

            kwargs["widget"] = forms.Textarea(attrs={"rows": 2, "cols": 90})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    fieldsets = (
        ("Order", {"fields": (("order_number", "state", "company"), ("reference_id", "vehicle_type"), "notes", "timeline")}),
        ("Driver", {"fields": (("driver_col", "vehicle"),)}),
        ("Pickup", {"fields": ("pickup_address", ("pickup_contact_name", "pickup_contact_phone"), "pickup_map")}),
        ("Drop", {"fields": ("drop_address", ("drop_contact_name", "drop_contact_phone"), "drop_map")}),
        ("Proof photos", {"fields": ("proof_gallery", ("pickup_photo", "delivery_photo", "delivery_otp", "verify_items"))}),
        ("Fare & payment", {"fields": (
            ("distance", "duration"), ("base_fare", "distance_fare", "time_fare", "surge_multiplier"),
            ("total_fare", "bonus_fare", "driver_earning"),
            ("payment_mode", "payment_status", "cod_collected_at"), ("payment_provider", "payment_reference"),
        )}),
        ("Invoice", {"fields": (("invoice_number", "invoice_url"), "invoice_link")}),
        ("Cancellation", {"classes": ("collapse",), "fields": (("cancelled_by", "cancellation_reason"),)}),
    )

    def get_readonly_fields(self, request, obj=None):
        names = {f for row in self.fieldsets for f in _flatten(row[1]["fields"])}
        return tuple(sorted(names - set(self.editable)))

    def has_add_permission(self, request):
        return False  # orders come in through the booking API

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(item_count=Count("items"))

    # -- columns -------------------------------------------------------------

    @admin.display(description="Order", ordering="order_number")
    def order(self, trip):
        ref = f" · {trip.reference_id}" if trip.reference_id else ""
        return format_html('<div class="mob-stack"><b>{}</b><span class="mob-sub">{} items{}</span></div>',
                           trip.order_number or str(trip.pk)[:8], getattr(trip, "item_count", trip.items.count()), ref)

    @admin.display(description="Status", ordering="status")
    def state(self, trip):
        return badge(trip.status, trip.get_status_display())

    @admin.display(description="Driver", ordering="driver__full_name")
    def driver_col(self, trip):
        if not trip.driver:
            return mark_safe('<span class="mob-muted">Unassigned</span>')
        return format_html('<div class="mob-stack">{}<span class="mob-sub">{}</span></div>',
                           admin_link(trip.driver, trip.driver.full_name or trip.driver.phone_number), trip.driver.phone_number)

    @admin.display(description="Route")
    def route(self, trip):
        return format_html(
            '<div class="mob-route"><i class="mob-dot mob-dot--pickup"></i><span>{}<br><span class="mob-sub">{}</span></span>'
            '<i class="mob-dot mob-dot--drop"></i><span>{}<br><span class="mob-sub">{}</span></span></div>',
            trip.pickup_contact_name or "Pickup", _short(trip.pickup_address),
            trip.drop_contact_name or "Drop", _short(trip.drop_address),
        )

    @admin.display(description="Fare", ordering="total_fare")
    def fare(self, trip):
        bonus = format_html('<span class="mob-sub">+ {} bonus</span>', money(trip.bonus_fare)) if trip.bonus_fare else ""
        return format_html('<div class="mob-stack"><b>{}</b>{}</div>', money(trip.total_fare), bonus)

    @admin.display(description="Payment")
    def payment(self, trip):
        return format_html('<div class="mob-stack">{}<span class="mob-sub">{}</span></div>',
                           trip.get_payment_mode_display(), badge(trip.payment_status))

    @admin.display(description="Proof")
    def proofs(self, trip):
        shots = [u for u in (trip.pickup_photo_url, trip.delivery_photo_url) if u]
        if not shots:
            return mark_safe('<span class="mob-muted">—</span>')
        return format_html('<div style="display:flex;gap:4px">{}</div>',
                           mark_safe("".join(str(thumb(u, 36)) for u in shots)))

    # -- detail --------------------------------------------------------------

    @admin.display(description="Timeline")
    def timeline(self, trip):
        steps = [
            ("Booked", trip.created_at), ("Driver assigned", trip.assigned_at), ("Reached pickup", trip.arrived_at_pickup_at),
            ("Picked up", trip.started_at), ("Paid (COD)", trip.cod_collected_at), ("Delivered", trip.completed_at),
            ("Cancelled", trip.cancelled_at),
        ]
        rows = [(label, when) for label, when in steps if when]
        return format_html('<ul class="mob-timeline">{}</ul>', mark_safe("".join(
            str(format_html("<li><b>{}</b><span>{}</span></li>", label, _when(when))) for label, when in rows
        )))

    @admin.display(description="Map")
    def pickup_map(self, trip):
        return map_link(trip.pickup_lat, trip.pickup_lng, f"{trip.pickup_lat}, {trip.pickup_lng}")

    @admin.display(description="Map")
    def drop_map(self, trip):
        return map_link(trip.drop_lat, trip.drop_lng, f"{trip.drop_lat}, {trip.drop_lng}")

    @admin.display(description="Photos")
    def proof_gallery(self, trip):
        shots = [("Pickup · whole order", trip.pickup_photo_url), ("Delivery · whole order", trip.delivery_photo_url)]
        for item in trip.items.all():
            shots += [(f"Pickup · {item.name}", item.pickup_photo_url), (f"Delivery · {item.name}", item.delivery_photo_url),
                      (f"Checked · {item.name}", item.proof_image_url)]
        return gallery(shots, size=160)

    @admin.display(description="Distance")
    def distance(self, trip):
        return f"{trip.distance_meters / 1000:.1f} km" if trip.distance_meters else "—"

    @admin.display(description="Est. time")
    def duration(self, trip):
        return f"{round(trip.duration_seconds / 60)} min" if trip.duration_seconds else "—"

    @admin.display(description="Open")
    def invoice_link(self, trip):
        if not trip.invoice_url:
            return mark_safe('<span class="mob-muted">No invoice</span>')
        return format_html('<a href="{}" target="_blank" rel="noopener">Open invoice ↗</a>', trip.invoice_url)

    # -- actions -------------------------------------------------------------

    @admin.action(description="Cancel selected orders (as the company)")
    def cancel_orders(self, request, queryset):
        done, refused = 0, []
        for trip in queryset:
            try:
                TripService.cancel_trip(trip, f"Cancelled from admin by {request.user.email}", CancelledBy.COMPANY)
                done += 1
            except DomainError as exc:
                refused.append(f"{trip.order_number}: {exc.detail}")
        if done:
            self.message_user(request, f"Cancelled {done} order(s).", messages.SUCCESS)
        for line in refused[:10]:
            self.message_user(request, line, messages.WARNING)

    @admin.action(description="Retry finding a driver")
    def retry_assignment(self, request, queryset):
        assigned, other = 0, []
        for trip in queryset:
            try:
                TripService.retry_assignment(trip)
                if trip.status == TripStatus.ASSIGNED:
                    assigned += 1
                else:
                    other.append(f"{trip.order_number}: still no driver nearby")
            except DomainError as exc:
                other.append(f"{trip.order_number}: {exc.detail}")
        if assigned:
            self.message_user(request, f"Assigned {assigned} order(s).", messages.SUCCESS)
        for line in other[:10]:
            self.message_user(request, line, messages.WARNING)

    def has_delete_permission(self, request, obj=None):
        return False  # a delivery is history; cancel it instead


@admin.register(TripItem)
class TripItemAdmin(CompanyScopedAdmin):
    """Every line of every order in one searchable place — handy for "which
    orders had the TMT bars?" and for reviewing items reported missing."""

    list_display = ("picture", "name", "order", "quantity_label", "state", "photos", "driver_note", "verified_at")
    list_display_links = ("name",)
    list_filter = ("status", "trip__status", "created_at", "company")
    search_fields = ("name", "sku", "notes", "driver_note", "trip__order_number", "trip__reference_id")
    search_help_text = "Item name, SKU, notes, or the order number"
    list_select_related = ("trip",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = (
        "trip_link", "position", "name", "quantity", "unit", "sku", "notes", "picture_large", "unit_price",
        "state", "verified_at", "driver_note", "photos_large",
    )
    fields = readonly_fields
    actions = [export_csv_action(
        [("Order", "trip.order_number"), ("Item", "name"), ("SKU", "sku"), ("Qty", "quantity"), ("Unit", "unit"),
         ("Status", "status"), ("Driver note", "driver_note"), ("Checked at", "verified_at")],
        "order-items",
    )]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="")
    def picture(self, item):
        return thumb(item.image_url, 40, item.name)

    @admin.display(description="Picture")
    def picture_large(self, item):
        return thumb(item.image_url, 160, item.name)

    @admin.display(description="Order", ordering="trip__order_number")
    def order(self, item):
        return admin_link(item.trip, item.trip.order_number or str(item.trip_id)[:8])

    trip_link = order

    @admin.display(description="Qty", ordering="quantity")
    def quantity_label(self, item):
        return f"{item.quantity} {item.unit}".strip()

    @admin.display(description="Status", ordering="status")
    def state(self, item):
        return badge(item.status)

    @admin.display(description="Photos")
    def photos(self, item):
        shots = [u for u in (item.pickup_photo_url, item.delivery_photo_url, item.proof_image_url) if u]
        if not shots:
            return mark_safe('<span class="mob-muted">—</span>')
        return format_html('<div style="display:flex;gap:4px">{}</div>', mark_safe("".join(str(thumb(u, 32)) for u in shots)))

    @admin.display(description="Proof photos")
    def photos_large(self, item):
        return gallery([("Pickup", item.pickup_photo_url), ("Delivery", item.delivery_photo_url), ("Checked", item.proof_image_url)])

    def get_queryset(self, request):
        qs = super(CompanyScopedAdmin, self).get_queryset(request).select_related("trip")
        if is_platform_admin(request.user):
            return qs
        return qs.filter(trip__company_id=request.user.company_id)


def _flatten(fields):
    for f in fields:
        if isinstance(f, (list, tuple)):
            yield from f
        else:
            yield f


def _short(text, n=60):
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _when(dt):
    from django.utils import timezone

    return timezone.localtime(dt).strftime("%d %b %Y, %I:%M %p")
