"""Rename someone you keep a list for, without rewriting gift history."""

import importlib
from datetime import datetime, timezone

from django.apps import apps
from django.contrib.auth.models import User
from django.test import Client, TestCase

from domain import Forbidden

from gifts.models import Connection, Item, Person
from gifts.store import DjangoStore
from gifts.tests.harness import PASSWORD, two_people

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
backfill_kept_by = importlib.import_module(
    "gifts.migrations.0006_kept_by_and_received_from_person"
).backfill_kept_by


class DjangoStoreRenameTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_keeper_may_rename_and_gifts_stay_as_recorded(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        self.assertEqual(grandma.kept_by_id, self.ada.id)
        self.assertTrue(self.store.can_shop(self.ada.id, grandma.id))
        self.assertNotEqual(self.ada.id, grandma.id)
        cup = self.store.add_item(self.ada.id, self.ada.id, "Teacup")
        self.store.receive(self.ada.id, cup.id, "Grandma", grandma.id)
        self.ada.user.first_name = "Ada"
        self.ada.user.save(update_fields=["first_name"])
        before = list(Item.objects.order_by("id").values())
        renamed = self.store.rename_person(self.ada.id, grandma.id, "Nana")
        self.assertEqual(renamed.name, "Nana")
        self.assertEqual(list(Item.objects.order_by("id").values()), before)
        row = Item.objects.get(pk=cup.id)
        self.assertEqual(row.received_from, "Grandma")
        self.assertEqual(row.received_from_person_id, grandma.id)
        self.ada.user.refresh_from_db()
        self.assertEqual(self.ada.user.first_name, "Ada")

    def test_person_themselves_may_rename_once_they_have_a_login(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        user = User.objects.create_user("grandma", "g@example.com", PASSWORD)
        self.store.link_login(grandma.id, user.id)
        grandma.refresh_from_db()
        self.assertEqual(grandma.kept_by_id, self.ada.id)
        self.assertFalse(self.store.can_shop(grandma.id, grandma.id))
        self.assertTrue(self.store.can_rename(grandma.id, grandma.id))
        self.store.rename_person(grandma.id, grandma.id, "Nana")
        grandma.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(grandma.name, "Nana")
        self.assertEqual(user.first_name, "Nana")
        self.assertTrue(self.store.can_shop(self.ada.id, grandma.id))
        self.assertTrue(self.store.can_rename(self.ada.id, grandma.id))

    def test_stranger_is_denied(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        cal = Person.objects.create(user=cal_user, name="Cal", email="cal@example.com")
        self.assertFalse(self.store.can_shop(cal.id, grandma.id))
        self.assertNotEqual(cal.id, grandma.id)
        with self.assertRaises(Forbidden):
            self.store.rename_person(cal.id, grandma.id, "Nana")
        grandma.refresh_from_db()
        self.assertEqual(grandma.name, "Grandma")

    def test_other_shopper_is_denied(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        user = User.objects.create_user("grandma", "g@example.com", PASSWORD)
        self.store.link_login(grandma.id, user.id)
        low, high = (
            (self.bea, grandma) if self.bea.id < grandma.id else (grandma, self.bea)
        )
        Connection.objects.create(person_a=low, person_b=high, created_at=NOW)
        self.assertTrue(self.store.can_shop(self.bea.id, grandma.id))
        self.assertNotEqual(self.bea.id, grandma.id)
        grandma.refresh_from_db()
        self.assertNotEqual(grandma.kept_by_id, self.bea.id)
        with self.assertRaises(Forbidden):
            self.store.rename_person(self.bea.id, grandma.id, "Nana")
        grandma.refresh_from_db()
        self.assertEqual(grandma.name, "Grandma")
        self.assertTrue(self.store.can_shop(self.bea.id, self.ada.id))
        self.assertFalse(self.store.can_rename(self.bea.id, self.ada.id))


class RenameHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.keeper = Client()
        self.keeper.login(username="ada", password=PASSWORD)
        self.other = Client()
        self.other.login(username="bea", password=PASSWORD)

    def _keep(self, name: str = "Sam") -> Person:
        before = set(Person.objects.values_list("id", flat=True))
        self.keeper.post("/lists/keep/", {"name": name, "email": ""})
        return Person.objects.exclude(id__in=before).get()

    def _from_them(self, page) -> str:
        """The gifts-from section, without the toast list above it."""

        content = page.content.decode()
        marker = 'id="from-them-heading"'
        self.assertIn(marker, content)
        return content[content.index(marker) :]

    def test_keeper_renames_and_a_linked_gift_shows_both_names(self) -> None:
        sam = self._keep("Sam")
        page = self.keeper.get(f"/lists/{sam.id}/")
        self.assertContains(page, f'action="/people/{sam.id}/rename/"')
        self.assertContains(page, "Gifts you already recorded keep the name")
        self.keeper.post(f"/lists/{sam.id}/from-them/", {"name": "Teacup"})
        response = self.keeper.post(
            f"/people/{sam.id}/rename/", {"name": "  Samantha  "}, follow=True
        )
        self.assertContains(response, "Renamed to Samantha.")
        self.assertContains(response, "Samantha's list")
        self.assertContains(response, "Gifts from Samantha")
        self.assertContains(response, "Sam (now Samantha)")
        self.assertContains(response, "Teacup")
        sam.refresh_from_db()
        self.assertEqual(sam.name, "Samantha")
        item = Item.objects.get(name="Teacup")
        self.assertEqual(item.received_from, "Sam")
        self.assertEqual(item.received_from_person_id, sam.id)
        self.assertIsNone(item.given_at)
        mine = self.keeper.get("/my-list/")
        self.assertContains(mine, "Teacup")
        self.assertContains(mine, "Sam (now Samantha)")
        home = self.keeper.get("/")
        self.assertContains(home, "Samantha")
        self.assertNotContains(self.other.get("/"), "Samantha")

    def test_an_older_unlinked_gift_keeps_the_recorded_name(self) -> None:
        sam = self._keep("Sam")
        self.keeper.post("/my-list/add/", {"name": "Book"})
        book = Item.objects.get(name="Book")
        self.keeper.post(f"/my-list/{book.id}/receive/", {"received_from": "Sam"})
        self.keeper.post(f"/people/{sam.id}/rename/", {"name": "Samantha"})
        book.refresh_from_db()
        self.assertEqual(book.received_from, "Sam")
        self.assertIsNone(book.received_from_person_id)
        mine = self.keeper.get("/my-list/")
        self.assertContains(mine, "From Sam")
        self.assertNotContains(mine, "now Samantha")
        section = self._from_them(self.keeper.get(f"/lists/{sam.id}/"))
        self.assertNotIn("Book", section)
        self.assertIn("Nothing from Samantha yet.", section)

    def test_two_people_who_share_a_name_stay_apart(self) -> None:
        first = self._keep("Sam")
        second = self._keep("Sam")
        self.keeper.post(f"/lists/{first.id}/from-them/", {"name": "Teacup"})
        self.keeper.post(f"/lists/{second.id}/from-them/", {"name": "Book"})
        self.keeper.post("/my-list/add/", {"name": "Scarf"})
        scarf = Item.objects.get(name="Scarf")
        self.keeper.post(f"/my-list/{scarf.id}/receive/", {"received_from": "Sam"})

        one = self._from_them(self.keeper.get(f"/lists/{first.id}/"))
        self.assertIn("Teacup", one)
        self.assertNotIn("Book", one)
        self.assertNotIn("Scarf", one)
        two = self._from_them(self.keeper.get(f"/lists/{second.id}/"))
        self.assertIn("Book", two)
        self.assertNotIn("Teacup", two)
        self.assertNotIn("Scarf", two)

        self.keeper.post(f"/people/{first.id}/rename/", {"name": "Samantha"})
        one = self._from_them(self.keeper.get(f"/lists/{first.id}/"))
        self.assertIn("Sam (now Samantha)", one)
        self.assertIn("Teacup", one)
        self.assertNotIn("Book", one)
        self.assertNotIn("Scarf", one)
        two = self._from_them(self.keeper.get(f"/lists/{second.id}/"))
        self.assertIn("Book", two)
        self.assertIn("Scarf", two)
        self.assertNotIn("Teacup", two)
        self.assertNotIn("(now Samantha)", two)
        scarf.refresh_from_db()
        self.assertEqual(scarf.received_from, "Sam")
        self.assertIsNone(scarf.received_from_person_id)

    def test_stranger_is_denied(self) -> None:
        sam = self._keep("Sam")
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        Person.objects.create(user=cal_user, name="Cal", email="cal@example.com")
        cal = Client()
        cal.login(username="cal", password=PASSWORD)
        self.assertEqual(cal.get(f"/lists/{sam.id}/").status_code, 404)
        response = cal.post(f"/people/{sam.id}/rename/", {"name": "Nope"})
        self.assertEqual(response.status_code, 404)
        sam.refresh_from_db()
        self.assertEqual(sam.name, "Sam")

    def test_other_shopper_is_denied(self) -> None:
        sam = self._keep("Sam")
        user = User.objects.create_user("sam", "sam@example.com", PASSWORD)
        DjangoStore().link_login(sam.id, user.id)
        low, high = (self.bea, sam) if self.bea.id < sam.id else (sam, self.bea)
        Connection.objects.create(person_a=low, person_b=high, created_at=NOW)
        page = self.other.get(f"/lists/{sam.id}/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, f"/people/{sam.id}/rename/")
        response = self.other.post(f"/people/{sam.id}/rename/", {"name": "Nope"})
        self.assertEqual(response.status_code, 403)
        sam.refresh_from_db()
        self.assertEqual(sam.name, "Sam")
        self.keeper.post(f"/people/{sam.id}/rename/", {"name": "Samantha"})
        sam.refresh_from_db()
        self.assertEqual(sam.name, "Samantha")

    def test_a_partner_cannot_rename_the_other_person_on_the_app(self) -> None:
        page = self.other.get("/their-list/")
        self.assertNotContains(page, f"/people/{self.ada.id}/rename/")
        response = self.other.post(f"/people/{self.ada.id}/rename/", {"name": "Nope"})
        self.assertEqual(response.status_code, 403)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.name, "Ada")

    def test_person_themselves_renames_and_an_open_claim_stays_hidden(self) -> None:
        sam = self._keep("Sam")
        self.keeper.post(
            f"/lists/{sam.id}/add/", {"name": "Scarf", "giver_note": "soft wool"}
        )
        item = Item.objects.get(name="Scarf")
        self.keeper.post(f"/their-list/{item.id}/claim/")
        user = User.objects.create_user("sam", "sam@example.com", PASSWORD)
        DjangoStore().link_login(sam.id, user.id)
        owner = Client()
        owner.login(username="sam", password=PASSWORD)
        mine = owner.get("/my-list/")
        self.assertContains(mine, "Your name")
        self.assertContains(mine, f'action="/people/{sam.id}/rename/"')
        self.assertContains(mine, "Scarf")
        self.assertNotContains(mine, "soft wool")
        self.assertNotContains(mine, "Claimed")
        renamed = owner.post(
            f"/people/{sam.id}/rename/", {"name": "Samantha"}, follow=True
        )
        self.assertContains(renamed, "Renamed to Samantha.")
        self.assertContains(renamed, 'value="Samantha"')
        self.assertNotContains(renamed, "soft wool")
        self.assertNotContains(renamed, "Claimed")
        self.assertNotContains(renamed, "claim")
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Samantha")
        item.refresh_from_db()
        self.assertEqual(item.claimed_by_id, self.ada.id)
        self.assertEqual(item.name, "Scarf")

    def test_blank_name_is_rejected(self) -> None:
        sam = self._keep("Sam")
        response = self.keeper.post(
            f"/people/{sam.id}/rename/", {"name": "   "}, follow=True
        )
        self.assertContains(response, "Name is required")
        sam.refresh_from_db()
        self.assertEqual(sam.name, "Sam")


class KeptByBackfillTests(TestCase):
    def test_backfill_sets_the_keeper_only_when_it_is_unambiguous(self) -> None:
        ada, bea = two_people()
        grandma = Person.objects.create(name="Grandma", email="", user=None)
        low, high = (ada, grandma) if ada.id < grandma.id else (grandma, ada)
        Connection.objects.create(person_a=low, person_b=high, created_at=NOW)
        alone = Person.objects.create(name="Alone", email="", user=None)
        item = Item.objects.create(
            owner=ada,
            added_by=ada,
            name="Teacup",
            reaction="liked",
            reacted_at=NOW,
            received_from="Grandma",
            created_at=NOW,
        )
        backfill_kept_by(apps, None)
        grandma.refresh_from_db()
        ada.refresh_from_db()
        bea.refresh_from_db()
        alone.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(grandma.kept_by_id, ada.id)
        self.assertIsNone(ada.kept_by_id)
        self.assertIsNone(bea.kept_by_id)
        self.assertIsNone(alone.kept_by_id)
        self.assertEqual(item.received_from, "Grandma")
        self.assertIsNone(item.received_from_person_id)
        again = list(Item.objects.values())
        backfill_kept_by(apps, None)
        grandma.refresh_from_db()
        self.assertEqual(grandma.kept_by_id, ada.id)
        self.assertEqual(list(Item.objects.values()), again)
