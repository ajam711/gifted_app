from django.contrib.auth.models import User
from django.utils import timezone

from gifts.models import Connection, Person

PASSWORD = "test-pass-ada-bea"


def two_people() -> tuple[Person, Person]:
    """Ada and Bea, each with a User, already connected."""

    ada_user = User.objects.create_user("ada", "ada@example.com", PASSWORD)
    bea_user = User.objects.create_user("bea", "bea@example.com", PASSWORD)
    ada = Person.objects.create(user=ada_user, name="Ada", email="ada@example.com")
    bea = Person.objects.create(user=bea_user, name="Bea", email="bea@example.com")
    first, second = (ada, bea) if ada.id < bea.id else (bea, ada)
    Connection.objects.create(
        person_a=first, person_b=second, created_at=timezone.now()
    )
    return ada, bea
