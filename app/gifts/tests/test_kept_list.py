"""A private list for someone who isn't on the app.

Only the keeper sees it. Gifts received from that person are rows on
the keeper's own list, via the existing receive action.
"""

import re
from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone as dj_timezone

from domain import Forbidden, Invalid, NotFound

from gifts.models import Connection, ImportantDate, Item, Person
from gifts.store import DjangoStore
from gifts.tests.harness import PASSWORD, two_people

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
_CSRF = re.compile(rb'name="csrfmiddlewaretoken" value="[^"]+"')


class KeptPersonModelTests(TestCase):
    def test_user_is_nullable_and_one_connection_only(self) -> None:
        ada, bea = two_people()
        grandma = Person.objects.create(name="Grandma", email="", user=None)
        self.assertIsNone(grandma.user_id)
        first, second = (ada, grandma) if ada.id < grandma.id else (grandma, ada)
        Connection.objects.create(
            person_a=first, person_b=second, created_at=NOW
        )
        other_first, other_second = (
            (bea, grandma) if bea.id < grandma.id else (grandma, bea)
        )
        with self.assertRaises(ValidationError):
            Connection.objects.create(
                person_a=other_first,
                person_b=other_second,
                created_at=NOW,
            )
        self.assertEqual(
            Connection.objects.filter(person_a=grandma).count()
            + Connection.objects.filter(person_b=grandma).count(),
            1,
        )


class DjangoStoreKeptListTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_add_is_liked_and_only_the_keeper_shops(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        self.assertIsNone(grandma.user_id)
        view = self.store.add_item(
            self.ada.id, grandma.id, "Scarf", giver_note="soft wool"
        )
        self.assertEqual(view.reaction, "liked")
        self.assertEqual(view.giver_note, "soft wool")
        row = Item.objects.get(pk=view.id)
        self.assertEqual(row.reacted_at, row.created_at)
        self.assertEqual(row.reaction, "liked")

        with self.assertRaises(Forbidden):
            self.store.add_item(self.bea.id, grandma.id, "Nope")
        with self.assertRaises(Forbidden):
            self.store.react(self.ada.id, view.id, "disliked")
        with self.assertRaises(Forbidden):
            self.store.claim(self.bea.id, view.id)
        with self.assertRaises(NotFound):
            self.store.shopper_items(self.bea.id, grandma.id)

        claimed = self.store.claim(self.ada.id, view.id)
        self.assertEqual(claimed.claimed_by_id, self.ada.id)
        given = self.store.give(self.ada.id, view.id)
        self.assertIsNotNone(given.given_at)
        self.assertEqual(given.received_at, NOW)

    def test_stranger_is_denied_for_every_action(self) -> None:
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        cal = Person.objects.create(
            user=cal_user, name="Cal", email="cal@example.com"
        )
        item = self.store.add_item(self.ada.id, self.ada.id, "Record")
        self.store.claim(self.bea.id, item.id)
        denied = [
            ("add", lambda: self.store.add_item(cal.id, self.ada.id, "Nope"), Forbidden),
            ("react", lambda: self.store.react(cal.id, item.id, "disliked"), Forbidden),
            ("claim", lambda: self.store.claim(cal.id, item.id), Forbidden),
            ("unclaim", lambda: self.store.unclaim(cal.id, item.id), Forbidden),
            ("give", lambda: self.store.give(cal.id, item.id), Forbidden),
            ("receive", lambda: self.store.receive(cal.id, item.id, "X"), Forbidden),
            ("unreceive", lambda: self.store.unreceive(cal.id, item.id), Forbidden),
        ]
        for name, action, error in denied:
            with self.subTest(action=name):
                with self.assertRaises(error):
                    action()
        row = Item.objects.get(pk=item.id)
        self.assertEqual(row.claimed_by_id, self.bea.id)

    def test_link_login_hides_the_note_and_the_open_claim(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        view = self.store.add_item(
            self.ada.id, grandma.id, "Scarf", giver_note="soft wool"
        )
        self.store.claim(self.ada.id, view.id)
        user = User.objects.create_user("grandma", "g@example.com", PASSWORD)
        self.store.link_login(grandma.id, user.id)
        owner = next(
            item for item in self.store.owner_items(grandma.id) if item.id == view.id
        )
        self.assertFalse(hasattr(owner, "giver_note"))
        self.assertFalse(hasattr(owner, "claimed_by_id"))
        self.assertIsNone(owner.received_from_id)
        shopper = self.store.shopper_item(self.ada.id, view.id)
        self.assertEqual(shopper.giver_note, "soft wool")
        self.assertEqual(shopper.claimed_by_id, self.ada.id)

    def test_received_from_them_is_the_keepers_own_item(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        added = self.store.add_item(self.ada.id, self.ada.id, "Teacup")
        received = self.store.receive(self.ada.id, added.id, grandma.name)
        self.assertEqual(received.received_from, "Grandma")
        row = Item.objects.get(pk=added.id)
        self.assertEqual(row.owner_id, self.ada.id)
        self.assertEqual(Item.objects.count(), 1)


class DjangoStoreDeleteTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_keeper_deletes_a_kept_item_including_one_they_claimed(self) -> None:
        grandma = self.store.keep_person(self.ada.id, "Grandma")
        scarf = self.store.add_item(self.ada.id, grandma.id, "Scarf")
        hat = self.store.add_item(self.ada.id, grandma.id, "Hat")
        self.store.claim(self.ada.id, hat.id)
        self.store.delete(self.ada.id, scarf.id)
        self.store.delete(self.ada.id, hat.id)
        self.assertFalse(Item.objects.filter(owner=grandma).exists())

    def test_stranger_cannot_delete_a_login_owners_item(self) -> None:
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        cal = Person.objects.create(
            user=cal_user, name="Cal", email="cal@example.com"
        )
        item = self.store.add_item(self.ada.id, self.ada.id, "Record")
        with self.assertRaises(Invalid):
            self.store.delete(cal.id, item.id)
        self.assertTrue(Item.objects.filter(pk=item.id).exists())

    def test_other_shopper_cannot_delete_a_login_owners_item(self) -> None:
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        cal = Person.objects.create(
            user=cal_user, name="Cal", email="cal@example.com"
        )
        low, high = (self.ada, cal) if self.ada.id < cal.id else (cal, self.ada)
        Connection.objects.create(person_a=low, person_b=high, created_at=NOW)
        item = self.store.add_item(self.ada.id, self.ada.id, "Record")
        self.store.claim(self.bea.id, item.id)
        for actor in (self.bea, cal):
            with self.subTest(actor=actor.name):
                with self.assertRaises(Invalid):
                    self.store.delete(actor.id, item.id)
        row = Item.objects.get(pk=item.id)
        self.assertEqual(row.claimed_by_id, self.bea.id)

    def test_owner_with_a_login_still_deletes_their_own_item(self) -> None:
        item = self.store.add_item(self.ada.id, self.ada.id, "Kettle")
        self.store.delete(self.ada.id, item.id)
        self.assertFalse(Item.objects.filter(pk=item.id).exists())
        claimed = self.store.add_item(self.ada.id, self.ada.id, "Watch")
        self.store.claim(self.bea.id, claimed.id)
        with self.assertRaises(Invalid):
            self.store.delete(self.ada.id, claimed.id)
        self.assertTrue(Item.objects.filter(pk=claimed.id).exists())


class KeptListHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.keeper = Client()
        self.keeper.login(username="ada", password=PASSWORD)
        self.other = Client()
        self.other.login(username="bea", password=PASSWORD)

    def _keep(self, name: str = "Grandma", email: str = "") -> Person:
        response = self.keeper.post(
            "/lists/keep/", {"name": name, "email": email}, follow=False
        )
        person = Person.objects.get(name=name.strip())
        self.assertRedirects(
            response, f"/lists/{person.id}/", fetch_redirect_response=False
        )
        return person

    def test_home_form_creates_a_private_door(self) -> None:
        grandma = self._keep("  Grandma  ")
        self.assertIsNone(grandma.user_id)
        self.assertEqual(grandma.name, "Grandma")
        self.assertEqual(grandma.email, "")
        home = self.keeper.get("/")
        self.assertContains(home, "List for")
        self.assertContains(home, "Grandma")
        self.assertContains(home, f'href="/lists/{grandma.id}/"')
        other_home = self.other.get("/")
        self.assertNotContains(other_home, "Grandma")
        self.assertNotContains(other_home, "List for")
        self.assertEqual(self.other.get(f"/lists/{grandma.id}/").status_code, 404)
        self.assertEqual(
            self.other.post(
                f"/lists/{grandma.id}/add/", {"name": "Sneaky"}
            ).status_code,
            404,
        )
        self.assertFalse(Item.objects.filter(name="Sneaky").exists())

    def test_blank_name_is_rejected(self) -> None:
        response = self.keeper.post("/lists/keep/", {"name": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Name is required")
        self.assertFalse(Person.objects.filter(user__isnull=True).exists())

    def test_keeper_adds_claims_and_records_history(self) -> None:
        grandma = self._keep()
        added = self.keeper.post(
            f"/lists/{grandma.id}/add/",
            {"name": "Scarf", "giver_note": "soft wool"},
            follow=True,
        )
        self.assertContains(added, "Scarf")
        self.assertContains(added, "soft wool")
        self.assertContains(added, "Liked")
        item = Item.objects.get(name="Scarf")
        self.assertEqual(item.reaction, "liked")
        self.assertEqual(item.owner_id, grandma.id)
        self.assertEqual(item.added_by_id, self.ada.id)

        self.keeper.post(f"/their-list/{item.id}/claim/")
        page = self.keeper.get(f"/lists/{grandma.id}/")
        self.assertContains(page, "Only you see this list.")
        self.assertContains(
            page,
            "They aren't on the app, so they don't see it now.",
        )
        self.assertNotContains(page, "Received list")

        given = self.keeper.post(f"/their-list/{item.id}/give/", follow=True)
        self.assertContains(given, "Recorded Scarf as given to Grandma.")
        self.assertContains(given, "Gifts given to Grandma")
        item.refresh_from_db()
        self.assertIsNotNone(item.given_at)
        self.assertEqual(item.received_at, item.given_at)
        self.assertEqual(item.claimed_by_id, self.ada.id)

    def test_gift_from_them_lands_on_the_keepers_list(self) -> None:
        grandma = self._keep()
        response = self.keeper.post(
            f"/lists/{grandma.id}/from-them/",
            {"name": "Teacup", "url": "https://example.com/cup"},
            follow=True,
        )
        self.assertContains(response, "Saved Teacup as a gift from Grandma.")
        self.assertContains(response, "Gifts from Grandma")
        self.assertContains(response, "Teacup")
        item = Item.objects.get(name="Teacup")
        self.assertEqual(item.owner_id, self.ada.id)
        self.assertEqual(item.received_from, "Grandma")
        self.assertIsNotNone(item.received_at)
        self.assertIsNone(item.given_at)
        mine = self.keeper.get("/my-list/")
        self.assertContains(mine, "Teacup")
        self.assertContains(mine, "From Grandma")
        self.assertEqual(Item.objects.count(), 1)

    def test_keeper_delete_uses_a_confirm_step(self) -> None:
        grandma = self._keep()
        self.keeper.post(f"/lists/{grandma.id}/add/", {"name": "Scarf"})
        item = Item.objects.get(name="Scarf")
        page = self.keeper.get(f"/lists/{grandma.id}/")
        self.assertContains(page, '<details class="confirm">')
        self.assertContains(page, "Remove Scarf from this list?")
        self.assertContains(page, f'action="/their-list/{item.id}/delete/"')
        login_list = self.other.get("/their-list/")
        self.assertNotContains(login_list, "/delete/")
        self.keeper.post(f"/their-list/{item.id}/claim/")
        claimed = self.keeper.get(f"/lists/{grandma.id}/")
        self.assertContains(claimed, f'action="/their-list/{item.id}/delete/"')
        deleted = self.keeper.post(f"/their-list/{item.id}/delete/", follow=True)
        self.assertContains(deleted, "Deleted Scarf.")
        self.assertFalse(Item.objects.filter(pk=item.id).exists())

    def test_stranger_and_other_shopper_cannot_delete(self) -> None:
        grandma = self._keep()
        self.keeper.post(f"/lists/{grandma.id}/add/", {"name": "Scarf"})
        kept = Item.objects.get(name="Scarf")
        own = Item.objects.create(
            owner=self.ada,
            added_by=self.ada,
            name="Kettle",
            reaction="liked",
            reacted_at=NOW,
            created_at=NOW,
        )
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        cal_person = Person.objects.create(
            user=cal_user, name="Cal", email="cal@example.com"
        )
        low, high = (
            (self.ada, cal_person)
            if self.ada.id < cal_person.id
            else (cal_person, self.ada)
        )
        Connection.objects.create(person_a=low, person_b=high, created_at=NOW)
        stranger = Client()
        stranger_user = User.objects.create_user("dee", "dee@example.com", PASSWORD)
        Person.objects.create(
            user=stranger_user, name="Dee", email="dee@example.com"
        )
        stranger.login(username="dee", password=PASSWORD)
        shopper = Client()
        shopper.login(username="cal", password=PASSWORD)

        for client in (stranger, shopper, self.other):
            denied = client.post(f"/their-list/{own.id}/delete/", follow=True)
            self.assertContains(denied, "Could not delete that.")
            self.assertNotContains(denied, "claim")
        self.assertTrue(Item.objects.filter(pk=own.id).exists())
        kept_denied = self.other.post(f"/their-list/{kept.id}/delete/", follow=True)
        self.assertContains(kept_denied, "Could not delete that.")
        self.assertTrue(Item.objects.filter(pk=kept.id).exists())

    def test_stranger_cannot_claim(self) -> None:
        grandma = self._keep()
        self.keeper.post(f"/lists/{grandma.id}/add/", {"name": "Scarf"})
        item = Item.objects.get(name="Scarf")
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        Person.objects.create(user=cal_user, name="Cal", email="cal@example.com")
        cal = Client()
        cal.login(username="cal", password=PASSWORD)
        response = cal.post(f"/their-list/{item.id}/claim/")
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)
        self.assertEqual(cal.get(f"/lists/{grandma.id}/").status_code, 404)

    def test_date_badge_is_on_that_persons_door(self) -> None:
        grandma = self._keep()
        today = dj_timezone.localdate()
        soon = today + timedelta(days=4)
        later = today + timedelta(days=12)
        ImportantDate.objects.create(
            person=grandma, label="Grandma day", month=soon.month, day=soon.day
        )
        ImportantDate.objects.create(
            person=self.bea, label="Birthday", month=later.month, day=later.day
        )
        home = self.keeper.get("/")
        nav = home.content.split(b'<nav class="doors">', 1)[1].split(b"</nav>", 1)[0]

        def door_for(name: bytes) -> bytes:
            for door in nav.split(b"</a>"):
                if name in door:
                    return door
            self.fail(name.decode())

        grandma_door = door_for(b"Grandma")
        bea_door = door_for(b"Bea")
        self.assertIn(b"Grandma day in 4 days", grandma_door)
        self.assertNotIn(b"Birthday", grandma_door)
        self.assertIn(b"Birthday in 12 days", bea_door)
        self.assertNotIn(b"Grandma day", bea_door)

    def test_after_login_giver_note_and_open_claim_stay_hidden(self) -> None:
        grandma = self._keep()
        self.keeper.post(
            f"/lists/{grandma.id}/add/",
            {"name": "Scarf", "giver_note": "soft wool"},
        )
        item = Item.objects.get(name="Scarf")
        self.keeper.post(f"/their-list/{item.id}/claim/")
        user = User.objects.create_user("grandma", "g@example.com", PASSWORD)
        DjangoStore().link_login(grandma.id, user.id)
        owner = Client()
        owner.login(username="grandma", password=PASSWORD)
        before = owner.get("/my-list/")
        self.assertContains(before, "Scarf")
        self.assertNotContains(before, "soft wool")
        self.assertNotContains(before, "claim")
        self.assertNotContains(before, "Claimed")
        again = owner.get("/my-list/")
        self.assertEqual(
            _CSRF.sub(b"CSRF", before.content),
            _CSRF.sub(b"CSRF", again.content),
        )
        shopper = self.keeper.get(f"/lists/{grandma.id}/")
        self.assertContains(shopper, "soft wool")
        self.assertContains(shopper, "Yours")


class SeveralShoppersHTTPTests(TestCase):
    def test_shoppers_see_each_others_claims(self) -> None:
        ada, bea = two_people()
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        cal = Person.objects.create(
            user=cal_user, name="Cal", email="cal@example.com"
        )
        low, high = (ada, cal) if ada.id < cal.id else (cal, ada)
        Connection.objects.create(person_a=low, person_b=high, created_at=NOW)
        item = Item.objects.create(
            owner=ada,
            added_by=ada,
            name="Record",
            reaction="liked",
            reacted_at=NOW,
            created_at=NOW,
        )
        bea_client = Client()
        bea_client.login(username="bea", password=PASSWORD)
        bea_client.post(f"/their-list/{item.id}/claim/")
        cal_client = Client()
        cal_client.login(username="cal", password=PASSWORD)
        page = cal_client.get(f"/lists/{ada.id}/")
        self.assertContains(page, "Record")
        self.assertContains(page, "Claimed by Bea")
        self.assertNotContains(page, f'action="/their-list/{item.id}/claim/"')
        again = cal_client.post(f"/their-list/{item.id}/claim/", follow=True)
        self.assertContains(again, "Record is already claimed.")
