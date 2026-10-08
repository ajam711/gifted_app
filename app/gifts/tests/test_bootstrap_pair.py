from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from gifts.models import Connection, Person


def pair_args(**overrides: str) -> list[str]:
    values = {
        "first_username": "sam",
        "first_name": "Sam",
        "first_email": "sam@example.com",
        "first_password": "sam-secret",
        "second_username": "kit",
        "second_name": "Kit",
        "second_email": "kit@example.com",
        "second_password": "kit-secret",
    }
    values.update(overrides)
    args = []
    for key, value in values.items():
        if value is not None:
            args += ["--" + key.replace("_", "-"), value]
    return args


def run(*args: str) -> str:
    out = StringIO()
    call_command("bootstrap_pair", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


class BootstrapPairTests(TestCase):
    def test_creates_both_people_and_one_connection(self) -> None:
        output = run(*pair_args())

        sam = Person.objects.get(user__username="sam")
        kit = Person.objects.get(user__username="kit")
        self.assertEqual((sam.name, sam.email), ("Sam", "sam@example.com"))
        self.assertEqual((kit.name, kit.email), ("Kit", "kit@example.com"))
        self.assertEqual(sam.user.first_name, "Sam")
        self.assertTrue(sam.user.check_password("sam-secret"))
        self.assertTrue(kit.user.check_password("kit-secret"))

        connection = Connection.objects.get()
        self.assertLess(connection.person_a_id, connection.person_b_id)
        self.assertEqual({connection.person_a, connection.person_b}, {sam, kit})
        self.assertIn("Sam and Kit are connected.", output)

    def test_rerun_with_same_pair_updates_details(self) -> None:
        run(*pair_args())
        run(*pair_args(first_name="Samira", first_password="new-secret"))

        sam = Person.objects.get(user__username="sam")
        self.assertEqual(sam.name, "Samira")
        self.assertEqual(sam.user.first_name, "Samira")
        self.assertTrue(sam.user.check_password("new-secret"))
        self.assertEqual(Connection.objects.count(), 1)
        self.assertEqual(Person.objects.count(), 2)

    def test_rerun_accepts_the_pair_in_either_order(self) -> None:
        run(*pair_args())
        run(
            *pair_args(
                first_username="kit",
                first_name="Kit",
                first_email="kit@example.com",
                second_username="sam",
                second_name="Sam",
                second_email="sam@example.com",
            )
        )
        self.assertEqual(Connection.objects.count(), 1)

    def test_refuses_a_different_pair(self) -> None:
        run(*pair_args())
        with self.assertRaisesMessage(CommandError, "exactly one pair"):
            run(*pair_args(second_username="lee", second_name="Lee"))
        self.assertFalse(User.objects.filter(username="lee").exists())
        self.assertEqual(Connection.objects.count(), 1)

    def test_refuses_the_same_username_twice(self) -> None:
        with self.assertRaisesMessage(CommandError, "different usernames"):
            run(*pair_args(second_username="sam"))
        self.assertFalse(User.objects.exists())

    def test_refuses_a_blank_name(self) -> None:
        with self.assertRaisesMessage(CommandError, "cannot be blank"):
            run(*pair_args(first_name="  "))
        self.assertFalse(User.objects.exists())

    def test_prompts_for_missing_passwords(self) -> None:
        answers = iter(["typo", "nope", "sam-secret", "sam-secret", "kit-secret", "kit-secret"])
        with mock.patch(
            "gifts.management.commands.bootstrap_pair.getpass",
            side_effect=lambda prompt: next(answers),
        ):
            run(*pair_args(first_password=None, second_password=None))

        self.assertTrue(User.objects.get(username="sam").check_password("sam-secret"))
        self.assertTrue(User.objects.get(username="kit").check_password("kit-secret"))

    def test_rejects_a_blank_prompted_password(self) -> None:
        answers = iter(["", "sam-secret", "sam-secret"])
        with mock.patch(
            "gifts.management.commands.bootstrap_pair.getpass",
            side_effect=lambda prompt: next(answers),
        ):
            run(*pair_args(first_password=None))

        self.assertTrue(User.objects.get(username="sam").check_password("sam-secret"))
