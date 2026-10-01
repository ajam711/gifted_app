from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import Http404

from gifts.models import Person


def person_required(view):
    """Session user must have a Person row. Missing person is not an oracle."""

    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            request.person = request.user.person
        except Person.DoesNotExist:
            raise Http404("Not found") from None
        return view(request, *args, **kwargs)

    return wrapped
