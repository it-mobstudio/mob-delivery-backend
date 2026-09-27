from django import template

from core.admin_utils import badge

register = template.Library()


@register.filter
def mob_badge(value):
    """A trip/verification status as a coloured pill."""
    return badge(value)
