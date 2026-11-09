"""Edit an item after it is saved.

Only the person who added it may edit, and only on a list they own or
can shop. The owner's form and the page after a save do not change
when the partner claims the item.
"""

import re
from datetime import datetime, timezone
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone as dj_timezone

from domain import Forbidden, OwnerItemView, ShopperItemView

from gifts.models import Connection, Item, Person
from gifts.store import DjangoStore
from gifts.tests.harness import PASSWORD, two_people

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
_CSRF = re.compile(rb'name="csrfmiddlewaretoken" value="[^"]+"')


class DjangoStoreEditTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_owner_edit_persists_text_and_hides_claim_fields(self) -> None:
        view = self.store.add_item(
            self.ada.id,
            self.ada.id,
            "Watch",
            url="https://example.com/a",
            price_text="$1",
            tag="Old",
        )
        self.store.claim(self.bea.id, view.id)
        edited = self.store.edit_item(
            self.ada.id,
            view.id,
            "Watch band",
            url="https://example.com/b",
            price_text="around $20",
            tag="Birthday",
            giver_note="should not stick",
            update_giver_note=True,
        )
        self.assertIsInstance(edited, OwnerItemView)
        self.assertEqual(edited.name, "Watch band")
        self.assertFalse(hasattr(edited, "giver_note"))
        self.assertFalse(hasattr(edited, "claimed_by_id"))
        item = Item.objects.get(pk=view.id)
        self.assertEqual(item.url, "https://example.com/b")
        self.assertEqual(item.price_text, "around $20")
        self.assertEqual(item.tag, "Birthday")
        self.assertIsNone(item.giver_note)
        self.assertEqual(item.claimed_by_id, self.bea.id)
        self.assertEqual(item.claimed_at, NOW)
        self.assertEqual(item.reaction, "liked")

    def test_shopper_edit_updates_private_note(self) -> None:
        view = self.store.add_item(
            self.bea.id, self.ada.id, "Coat", giver_note="navy"
        )
        edited = self.store.edit_item(
            self.bea.id,
            view.id,
            "Wool coat",
            giver_note="size M",
            update_giver_note=True,
        )
        self.assertIsInstance(edited, ShopperItemView)
        self.assertEqual(edited.giver_note, "size M")
        self.assertIsNone(edited.reaction)
        item = Item.objects.get(pk=view.id)
        self.assertEqual(item.name, "Wool coat")
        self.assertEqual(item.giver_note, "size M")

    def test_partner_and_owner_are_denied_when_they_did_not_add_it(self) -> None:
        own = self.store.add_item(self.ada.id, self.ada.id, "Watch")
        suggestion = self.store.add_item(
            self.bea.id, self.ada.id, "Puzzle", giver_note="secret"
        )
        with self.assertRaises(Forbidden):
            self.store.edit_item(self.bea.id, own.id, "Stolen")
        with self.assertRaises(Forbidden):
            self.store.edit_item(self.ada.id, suggestion.id, "Changed")
        self.assertEqual(Item.objects.get(pk=own.id).name, "Watch")
        row = Item.objects.get(pk=suggestion.id)
        self.assertEqual(row.name, "Puzzle")
        self.assertEqual(row.giver_note, "secret")

    def test_stranger_cannot_edit(self) -> None:
        outsider_user = User.objects.create_user("cal", "cal@example.com", "pw")
        outsider = Person.objects.create(
            user=outsider_user, name="Cal", email="cal@example.com"
        )
        view = self.store.add_item(self.ada.id, self.ada.id, "Watch")
        with self.assertRaises(Forbidden):
            self.store.edit_item(outsider.id, view.id, "Stolen")
        self.assertEqual(Item.objects.get(pk=view.id).name, "Watch")

    def test_adder_cannot_edit_after_the_connection_is_gone(self) -> None:
        """can_shop is the connection. Drop it and the adder is refused."""

        cal_user = User.objects.create_user("cal", "cal@example.com", "pw")
        cal = Person.objects.create(
            user=cal_user, name="Cal", email="cal@example.com"
        )
        first, second = (self.ada, cal) if self.ada.id < cal.id else (cal, self.ada)
        Connection.objects.create(
            person_a=first, person_b=second, created_at=dj_timezone.now()
        )
        view = self.store.add_item(
            cal.id, self.ada.id, "Suggestion", giver_note="quiet"
        )
        Connection.objects.filter(person_a=first, person_b=second).delete()
        with self.assertRaises(Forbidden):
            self.store.edit_item(
                cal.id,
                view.id,
                "Changed",
                giver_note="no",
                update_giver_note=True,
            )
        item = Item.objects.get(pk=view.id)
        self.assertEqual(item.name, "Suggestion")
        self.assertEqual(item.giver_note, "quiet")

    def test_unchanged_edit_keeps_the_claim(self) -> None:
        view = self.store.add_item(
            self.ada.id,
            self.ada.id,
            "Watch",
            url="https://example.com/w",
            price_text="$5",
            tag="Birthday",
        )
        self.store.claim(self.bea.id, view.id)
        edited = self.store.edit_item(
            self.ada.id,
            view.id,
            "Watch",
            url="https://example.com/w",
            price_text="$5",
            tag="Birthday",
        )
        self.assertEqual(edited.name, "Watch")
        self.assertFalse(hasattr(edited, "claimed_by_id"))
        item = Item.objects.get(pk=view.id)
        self.assertEqual(item.claimed_by_id, self.bea.id)

    def test_edit_racing_a_claim_keeps_the_claim(self) -> None:
        view = self.store.add_item(self.ada.id, self.ada.id, "Watch")
        item = Item.objects.get(pk=view.id)
        stale = Item.objects.get(pk=item.pk)
        real = DjangoStore._require_item_row
        reads = iter([stale])

        def read_stale_once(item_id: int) -> Item:
            return next(reads, None) or real(self.store, item_id)

        with mock.patch.object(
            self.store, "_require_item_row", side_effect=read_stale_once
        ):
            DjangoStore(now=lambda: NOW).claim(self.bea.id, item.id)
            edited = self.store.edit_item(self.ada.id, item.id, "Watch band")
        self.assertEqual(edited.name, "Watch band")
        self.assertFalse(hasattr(edited, "claimed_by_id"))
        item.refresh_from_db()
        self.assertEqual(item.name, "Watch band")
        self.assertEqual(item.claimed_by_id, self.bea.id)
        self.assertEqual(item.claimed_at, NOW)


class EditHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()

    def _login(self, username: str) -> None:
        ok = self.client.login(username=username, password=PASSWORD)
        self.assertTrue(ok)

    def test_login_required(self) -> None:
        response = self.client.get("/my-list/1/edit/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_edit_link_only_on_items_you_added(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/",
            {"name": "Suggestion", "giver_note": "hide me please"},
        )
        suggestion = Item.objects.get(name="Suggestion")
        self.client.logout()
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Kettle"})
        kettle = Item.objects.get(name="Kettle")
        mine = self.client.get("/my-list/")
        self.assertContains(mine, f'href="/my-list/{kettle.id}/edit/"')
        self.assertNotContains(mine, f"/my-list/{suggestion.id}/edit/")
        self.assertNotContains(mine, "hide me please")
        self.assertNotContains(mine, "Private note")
        self.client.logout()
        self._login("bea")
        theirs = self.client.get("/their-list/")
        self.assertContains(theirs, f'href="/their-list/{suggestion.id}/edit/"')
        self.assertNotContains(theirs, f"/their-list/{kettle.id}/edit/")

    def test_owner_form_is_prefilled_without_a_private_note(self) -> None:
        self._login("ada")
        self.client.post(
            "/my-list/add/",
            {
                "name": "Kettle",
                "url": "https://example.com/kettle",
                "price_text": "around $40",
                "tag": "Birthday",
            },
        )
        item = Item.objects.get(name="Kettle")
        response = self.client.get(f"/my-list/{item.id}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit on your list")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'value="Kettle"')
        self.assertContains(response, 'value="https://example.com/kettle"')
        self.assertContains(response, 'value="around $40"')
        self.assertContains(response, 'value="Birthday"')
        self.assertNotContains(response, "Private note")
        self.assertNotContains(response, "giver_note")
        self.assertNotContains(response, "<select")
        body = _CSRF.sub(b"CSRF", response.content).lower()
        self.assertNotIn(b"claim", body)

    def test_owner_edit_saves_and_redirects(self) -> None:
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Kettle"})
        item = Item.objects.get(name="Kettle")
        response = self.client.post(
            f"/my-list/{item.id}/edit/",
            {
                "name": "Tea kettle",
                "url": "example.com/kettle",
                "price_text": "around $40",
                "tag": "Birthday",
            },
            follow=True,
        )
        self.assertEqual(response.redirect_chain, [("/my-list/", 302)])
        self.assertContains(response, "Saved Tea kettle.")
        self.assertContains(response, "Tea kettle")
        item.refresh_from_db()
        self.assertEqual(item.name, "Tea kettle")
        self.assertEqual(item.url, "https://example.com/kettle")
        self.assertEqual(item.price_text, "around $40")
        self.assertEqual(item.tag, "Birthday")
        self.assertEqual(item.reaction, "liked")
        self.assertIsNone(item.giver_note)

    def test_own_edit_ignores_posted_giver_note(self) -> None:
        self._login("ada")
        DjangoStore(now=lambda: NOW).add_item(
            self.ada.id, self.ada.id, "Mug", giver_note="keep"
        )
        item = Item.objects.get(name="Mug")
        self.client.post(
            f"/my-list/{item.id}/edit/",
            {"name": "Cup", "giver_note": "should not persist"},
        )
        item.refresh_from_db()
        self.assertEqual(item.name, "Cup")
        self.assertEqual(item.giver_note, "keep")

    def test_shopper_form_includes_private_note_and_saves_it(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/",
            {
                "name": "Coat",
                "url": "https://example.com/coat",
                "price_text": "around $120",
                "tag": "Birthday",
                "giver_note": "navy, not black",
            },
        )
        item = Item.objects.get(name="Coat")
        form = self.client.get(f"/their-list/{item.id}/edit/")
        self.assertContains(form, "Edit on Ada")
        self.assertContains(form, "Private note")
        self.assertContains(form, "navy, not black")
        response = self.client.post(
            f"/their-list/{item.id}/edit/",
            {
                "name": "Wool coat",
                "url": "https://example.com/wool",
                "price_text": "$80",
                "tag": "Xmas",
                "giver_note": "size M",
            },
            follow=True,
        )
        self.assertEqual(response.redirect_chain, [("/their-list/", 302)])
        self.assertContains(response, "Saved Wool coat.")
        self.assertContains(response, "size M")
        item.refresh_from_db()
        self.assertEqual(item.name, "Wool coat")
        self.assertEqual(item.giver_note, "size M")
        self.assertIsNone(item.reaction)
        self.client.logout()
        self._login("ada")
        mine = self.client.get("/my-list/")
        self.assertContains(mine, "Wool coat")
        self.assertNotContains(mine, "size M")
        self.assertNotContains(mine, f"/my-list/{item.id}/edit/")

    def test_shopper_can_clear_the_private_note(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/", {"name": "Coat", "giver_note": "navy"}
        )
        item = Item.objects.get(name="Coat")
        self.client.post(
            f"/their-list/{item.id}/edit/",
            {"name": "Coat", "giver_note": "   "},
        )
        item.refresh_from_db()
        self.assertIsNone(item.giver_note)

    def test_partner_cannot_edit_the_owners_item(self) -> None:
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Watch"})
        item = Item.objects.get(name="Watch")
        self.client.logout()
        self._login("bea")
        response = self.client.post(
            f"/their-list/{item.id}/edit/", {"name": "Stolen"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"claim", response.content.lower())
        item.refresh_from_db()
        self.assertEqual(item.name, "Watch")
        wrong_list = self.client.get(f"/my-list/{item.id}/edit/")
        self.assertEqual(wrong_list.status_code, 404)

    def test_owner_cannot_edit_a_suggestion(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/",
            {"name": "Puzzle", "giver_note": "do not show ada"},
        )
        item = Item.objects.get(name="Puzzle")
        self.client.logout()
        self._login("ada")
        response = self.client.post(
            f"/my-list/{item.id}/edit/", {"name": "Changed"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"claim", response.content.lower())
        self.assertNotIn(b"do not show ada", response.content)
        item.refresh_from_db()
        self.assertEqual(item.name, "Puzzle")
        self.assertEqual(item.giver_note, "do not show ada")
        wrong_list = self.client.get(f"/their-list/{item.id}/edit/")
        self.assertEqual(wrong_list.status_code, 404)

    def test_owner_denial_is_identical_claimed_or_not(self) -> None:
        self._login("bea")
        self.client.post("/their-list/add/", {"name": "Open suggestion"})
        self.client.post("/their-list/add/", {"name": "Claimed suggestion"})
        open_item = Item.objects.get(name="Open suggestion")
        claimed_item = Item.objects.get(name="Claimed suggestion")
        self.client.logout()
        self._login("ada")
        self.client.post(
            f"/my-list/{claimed_item.id}/react/", {"reaction": "liked"}
        )
        self.client.logout()
        self._login("bea")
        self.client.post(f"/their-list/{claimed_item.id}/claim/")
        self.client.logout()
        self._login("ada")
        denied_open = self.client.post(
            f"/my-list/{open_item.id}/edit/", {"name": "Nope"}
        )
        denied_claimed = self.client.post(
            f"/my-list/{claimed_item.id}/edit/", {"name": "Nope"}
        )
        self.assertEqual(denied_open.status_code, 403)
        self.assertEqual(denied_open.content, denied_claimed.content)
        open_item.refresh_from_db()
        claimed_item.refresh_from_db()
        self.assertEqual(open_item.name, "Open suggestion")
        self.assertEqual(claimed_item.name, "Claimed suggestion")
        self.assertEqual(claimed_item.claimed_by_id, self.bea.id)
        self.assertIsNone(open_item.claimed_by_id)

    def test_owner_edit_form_identical_after_claim(self) -> None:
        self._login("ada")
        self.client.post(
            "/my-list/add/",
            {
                "name": "Watch",
                "url": "https://example.com/watch",
                "price_text": "around $40",
                "tag": "Birthday",
            },
        )
        item = Item.objects.get(name="Watch")
        # The add redirect's toast is session state, not part of the item.
        self.client.get("/my-list/")
        before = self.client.get(f"/my-list/{item.id}/edit/")
        self.client.logout()
        shopper = Client()
        shopper.login(username="bea", password=PASSWORD)
        shopper.post(f"/their-list/{item.id}/claim/")
        self._login("ada")
        after = self.client.get(f"/my-list/{item.id}/edit/")
        self.assertEqual(
            _CSRF.sub(b"CSRF", before.content),
            _CSRF.sub(b"CSRF", after.content),
        )
        self.assertNotIn(b"claim", _CSRF.sub(b"CSRF", after.content).lower())
        item.refresh_from_db()
        self.assertEqual(item.claimed_by_id, self.bea.id)

    def test_owner_edit_response_identical_claimed_or_not(self) -> None:
        """Saving a claimed item looks exactly like saving any other.

        Same one-item list both times; only the claim differs. Item ids
        differ between the runs, so they are normalized with the CSRF token.
        """

        owner = Client()
        owner.login(username="ada", password=PASSWORD)
        shopper = Client()
        shopper.login(username="bea", password=PASSWORD)

        def edit_page(claim_first: bool) -> bytes:
            Item.objects.all().delete()
            item = Item.objects.create(
                owner=self.ada,
                added_by=self.ada,
                name="Same",
                url="https://example.com/same",
                price_text="$5",
                tag="Birthday",
                reaction="liked",
                reacted_at=NOW,
                created_at=NOW,
            )
            if claim_first:
                shopper.post(f"/their-list/{item.id}/claim/")
            response = owner.post(
                f"/my-list/{item.id}/edit/",
                {
                    "name": "Renamed",
                    "url": "https://example.com/new",
                    "price_text": "about $10",
                    "tag": "Xmas",
                },
                follow=True,
            )
            self.assertEqual(response.redirect_chain, [("/my-list/", 302)])
            self.assertContains(response, "Saved Renamed.")
            item.refresh_from_db()
            self.assertEqual(item.name, "Renamed")
            self.assertEqual(item.url, "https://example.com/new")
            if claim_first:
                self.assertEqual(item.claimed_by_id, self.bea.id)
                self.assertIsNotNone(item.claimed_at)
            else:
                self.assertIsNone(item.claimed_by_id)
            body = _CSRF.sub(b"CSRF", response.content)
            body = body.replace(f"/my-list/{item.id}/".encode(), b"/my-list/ID/")
            body = body.replace(f"-{item.id}\"".encode(), b'-ID"')
            self.assertNotIn(b"claim", body.lower())
            self.assertNotIn(b"giver_note", body.lower())
            return body

        self.assertEqual(edit_page(claim_first=False), edit_page(claim_first=True))

    def test_name_required(self) -> None:
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Kettle"})
        item = Item.objects.get(name="Kettle")
        response = self.client.post(f"/my-list/{item.id}/edit/", {"name": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Name is required")
        html = response.content.decode()
        self.assertNotRegex(html, r"(?s)<p[ >](?:(?!</p>).)*<ul class=\"errorlist")
        item.refresh_from_db()
        self.assertEqual(item.name, "Kettle")

    def test_non_https_links_rejected(self) -> None:
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Coat"})
        item = Item.objects.get(name="Coat")
        response = self.client.post(
            f"/my-list/{item.id}/edit/",
            {"name": "Coat", "url": "javascript:alert(1)"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use a link that starts with https://")
        item.refresh_from_db()
        self.assertEqual(item.name, "Coat")
        self.assertIsNone(item.url)

    def test_missing_item_is_404(self) -> None:
        self._login("ada")
        response = self.client.get("/my-list/9999/edit/")
        self.assertEqual(response.status_code, 404)
        response = self.client.post("/my-list/9999/edit/", {"name": "Nope"})
        self.assertEqual(response.status_code, 404)

    def test_stranger_edit_is_404(self) -> None:
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Watch"})
        item = Item.objects.get(name="Watch")
        self.client.logout()
        User.objects.create_user("cal", "cal@example.com", PASSWORD)
        Person.objects.create(
            user=User.objects.get(username="cal"),
            name="Cal",
            email="cal@example.com",
        )
        self._login("cal")
        response = self.client.get(f"/my-list/{item.id}/edit/")
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f"/their-list/{item.id}/edit/")
        self.assertEqual(response.status_code, 404)
        item.refresh_from_db()
        self.assertEqual(item.name, "Watch")

    def test_edit_csrf_enforced(self) -> None:
        item = Item.objects.create(
            owner=self.ada,
            added_by=self.ada,
            name="Kettle",
            reaction="liked",
            reacted_at=NOW,
            created_at=NOW,
        )
        client = Client(enforce_csrf_checks=True)
        client.login(username="ada", password=PASSWORD)
        response = client.post(f"/my-list/{item.id}/edit/", {"name": "Cup"})
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.name, "Kettle")
