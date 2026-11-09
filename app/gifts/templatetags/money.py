from django import template

from gifts.money import format_dollars

register = template.Library()


@register.filter(name="dollars")
def dollars(value: object) -> str:
    """Template wrapper for :func:`gifts.money.format_dollars`."""

    return format_dollars(value)
