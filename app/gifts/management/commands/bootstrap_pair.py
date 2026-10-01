from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from gifts.models import Connection, Person


class Command(BaseCommand):
    help = "Create the two operator accounts, Person rows, and their Connection."

    def add_arguments(self, parser):
        parser.add_argument("--ada-password", required=True)
        parser.add_argument("--bea-password", required=True)

    def handle(self, *args, **options):
        ada = self._person("ada", "Ada", "ada@example.com", options["ada_password"])
        bea = self._person("bea", "Bea", "bea@example.com", options["bea_password"])
        first, second = (ada, bea) if ada.id < bea.id else (bea, ada)
        Connection.objects.get_or_create(
            person_a=first,
            person_b=second,
            defaults={"created_at": timezone.now()},
        )
        self.stdout.write(self.style.SUCCESS("Ada and Bea are connected."))
        self.stdout.write("Log in as ada or bea with the passwords you passed.")

    def _person(self, username: str, name: str, email: str, password: str) -> Person:
        user, _created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "first_name": name},
        )
        user.email = email
        user.first_name = name
        user.set_password(password)
        user.save()
        person, _created = Person.objects.get_or_create(
            user=user,
            defaults={"name": name, "email": email},
        )
        if person.name != name or person.email != email:
            person.name = name
            person.email = email
            person.save(update_fields=["name", "email"])
        return person
