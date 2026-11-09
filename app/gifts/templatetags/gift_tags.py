from django import template

from domain.views import received_from_label as _received_from_label

register = template.Library()


@register.filter
def person_name(names, person_id):
    """Display name for an id, from a ``{id: name}`` dict passed by the view."""

    if not names or not person_id:
        return ""
    return names.get(person_id, "")


@register.filter
def received_from_label(item, names):
    """Recorded giver, with the linked person's current name beside it.

    Unlinked text is shown as stored. A missing names dict does not
    guess a person from the text.
    """

    recorded = getattr(item, "received_from", None)
    person_id = getattr(item, "received_from_person_id", None)
    current = None
    if isinstance(names, dict) and person_id:
        current = names.get(person_id) or None
    return _received_from_label(recorded, current)
