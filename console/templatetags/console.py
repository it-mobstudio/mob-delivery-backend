from decimal import Decimal

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timesince import timesince

from core.admin_utils import BADGE_TONES

register = template.Library()

LABELS = {
    "no_driver_available": "No driver", "arrived_at_pickup": "At pickup", "in_progress": "In transit",
    "not_delivered": "Not delivered", "locked_dl_expired": "Licence expired", "cod": "Cash on delivery",
    "trip_earning": "Trip earning", "per_item": "Every item", "order": "Whole order", "both": "Whole order + every item", "none": "Not needed",
    "profile_incomplete": "Profile incomplete", "documents_required": "Documents required",
    "under_review": "Under review", "action_required": "Action required",
}


@register.filter
def label(value):
    value = str(value or "")
    return LABELS.get(value, value.replace("_", " ").capitalize())


@register.filter
def badge(value, text=None):
    if value in (None, ""):
        return mark_safe('<span class="muted">—</span>')
    tone = BADGE_TONES.get(str(value), "grey")
    return format_html('<span class="badge badge-{}"><i></i>{}</span>', tone, text or label(value))


@register.filter
def money(value, decimals=2):
    if value in (None, ""):
        return "—"
    value = Decimal(value)
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.{int(decimals)}f}"
    if int(decimals) and text.endswith("." + "0" * int(decimals)):
        text = text[: -(int(decimals) + 1)]
    return f"{sign}₹{text}"


@register.filter
def ago(value):
    if not value:
        return "—"
    return timesince(value, timezone.now()).split(",")[0] + " ago"


@register.filter
def initials(name):
    parts = [p for p in str(name or "").split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


@register.filter
def km(meters):
    return f"{meters / 1000:.1f} km" if meters else "—"


@register.filter
def minutes(seconds):
    if not seconds:
        return "—"
    m = round(seconds / 60)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m} min"


@register.filter
def pct(part, whole):
    try:
        return round(100 * float(part) / float(whole)) if float(whole) else 0
    except (TypeError, ValueError):
        return 0


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """The current query string with `changes` applied (None/"" removes a
    key); page resets whenever anything else changes."""
    params = context["request"].GET.copy()
    if "page" not in changes:
        params.pop("page", None)
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.simple_tag
def icon(name, size=18):
    return format_html('<i data-lucide="{}" style="width:{}px;height:{}px"></i>', name, size, size)


@register.filter
def get_item(mapping, key):
    return (mapping or {}).get(key)
