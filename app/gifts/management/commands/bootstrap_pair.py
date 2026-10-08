from getpass import getpass

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from gifts.models import Connection, Person

SIDES = ("first", "second")


class Command(BaseCommand):
    help = (
        "Create the two accounts, their Person rows, and their Connection. "
        "Re-running with the same usernames updates names, emails and passwords."
    )

    def add_arguments(self, parser):
        for side in SIDES:
            parser.add_argument(f"--{side}-username", required=True)
            parser.add_argument(
                f"--{side}-name",
                required=True,
                help="Display name shown to the other person.",
            )
            parser.add_argument(f"--{side}-email", required=True)
            parser.add_argument(
                f"--{side}-password",
                help="Prompted for when omitted.",
            )

    def handle(self, *args, **options):
        people = [
            {
                "username": options[f"{side}_username"].strip(),
                "name": options[f"{side}_name"].strip(),
                "email": options[f"{side}_email"].strip(),
                "password": options[f"{side}_password"],
            }
            for side in SIDES
        ]
        for person in people:
            if not person["username"] or not person["name"]:
                raise CommandError("Usernames and names cannot be blank.")
        if people[0]["username"] == people[1]["username"]:
            raise CommandError("The two people need different usernames.")

        self._refuse_other_pair({p["username"] for p in people})

        for person in people:
            if not person["password"]:
                person["password"] = self._ask_password(person["username"])

        with transaction.atomic():
            first, second = (self._person(**p) for p in people)
            low, high = (first, second) if first.id < second.id else (second, first)
            Connection.objects.get_or_create(
                person_a=low,
                person_b=high,
                defaults={"created_at": timezone.now()},
            )

        self.stdout.write(
            self.style.SUCCESS(f"{first.name} and {second.name} are connected.")
        )
        self.stdout.write(
            f"Log in as {first.user.username} or {second.user.username}."
        )

    def _refuse_other_pair(self, usernames: set[str]) -> None:
        existing = Connection.objects.select_related(
            "person_a__user", "person_b__user"
        ).first()
        if existing is None:
            return
        connected = {existing.person_a.user.username, existing.person_b.user.username}
        if connected != usernames:
            raise CommandError(
                "A pair is already connected ("
                + " and ".join(sorted(connected))
                + "). V1 allows exactly one pair. To rename someone, edit "
                "Person.name and User.first_name in Django admin."
            )

    def _ask_password(self, username: str) -> str:
        while True:
            password = getpass(f"Password for {username}: ")
            if not password:
                self.stderr.write("Password cannot be blank.")
                continue
            if getpass(f"Password for {username} (again): ") != password:
                self.stderr.write("Passwords did not match.")
                continue
            return password

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
