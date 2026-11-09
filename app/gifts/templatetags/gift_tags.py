from django import template

register = template.Library()


@register.filter
def person_name(names, person_id):
    """Display name for an id, from a ``{id: name}`` dict passed by the view."""

    if not names or not person_id:
        return ""
    return names.get(person_id, "")
