"""Received: the shopper's confirmed hand-over and the owner's "got it elsewhere".

Ada owns the list, Bea shops it. Give reveals the gift to Ada as
received from Bea. "Got it elsewhere" is Ada's own report, so her
response must look the same whether or not Bea had claimed the item.
"""

import re
from datetime import datetime, timezone

from django.test import Client, TestCase
from django.utils import timezone as dj_timezone

from domain import Conflict, Invalid, OwnerItemView

from gifts.models import Item
from gifts.store import DjangoStore
from gifts.tests.harness import PASSWORD, two_people

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
_CSRF = re.compile(rb'name="csrfmiddlewaretoken" value="[^"]+"')


def _liked(owner, name: str = "Mug") -> Item:
    return Item.objects.create(
        owner=owner,
        added_by=owner,
        name=name,
        reaction="liked",
        reacted_at=dj_timezone.now(),
        created_at=dj_timezone.now(),
    )


class DjangoStoreReceivedTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_give_sets_received_at(self) -> None:
        item = _liked(self.ada)
        self.store.claim(self.bea.id, item.id)
        self.store.give(self.bea.id, item.id)
        item.refresh_from_db()
        self.assertEqual(item.given_at, NOW)
        self.assertEqual(item.received_at, NOW)

    def test_receive_releases_claim(self) -> None:
        item = _liked(self.ada)
        self.store.claim(self.bea.id, item.id)
        view = self.store.receive(self.ada.id, item.id, "Grandma")
        self.assertIsInstance(view, OwnerItemView)
        self.assertEqual(view.received_from, "Grandma")
        self.assertIsNone(view.received_from_id)
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)
        self.assertEqual(item.claim_released_at, NOW)
        with self.assertRaises(Conflict) as ctx:
            self.store.claim(self.bea.id, item.id)
        self.assertEqual(ctx.exception.reason, "received")

    def test_unreceive_and_refusals(self) -> None:
        item = _liked(self.ada)
        self.store.receive(self.ada.id, item.id)
        with self.assertRaises(Invalid):
            self.store.receive(self.ada.id, item.id)
        self.store.unreceive(self.ada.id, item.id)
        item.refresh_from_db()
        self.assertIsNone(item.received_at)
        with self.assertRaises(Invalid):
            self.store.unreceive(self.ada.id, item.id)


class ReceivedHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.owner = Client()
        self.owner.login(username="ada", password=PASSWORD)
        self.shopper = Client()
        self.shopper.login(username="bea", password=PASSWORD)

    def test_got_it_elsewhere_moves_to_received(self) -> None:
        item = _liked(self.ada)
        page = self.owner.get("/my-list/")
        self.assertContains(page, "Got it elsewhere")
        response = self.owner.post(
            f"/my-list/{item.id}/receive/", {"received_from": "Grandma"}, follow=True
        )
        self.assertContains(response, "Moved Mug to Received.")
        self.assertContains(response, 'id="received-heading"')
        self.assertContains(response, "From Grandma")
        self.assertContains(response, f'action="/my-list/{item.id}/unreceive/"')
        self.assertNotContains(response, f'action="/my-list/{item.id}/react/"')

    def test_from_is_optional(self) -> None:
        item = _liked(self.ada)
        self.owner.post(f"/my-list/{item.id}/receive/", {"received_from": ""})
        item.refresh_from_db()
        self.assertIsNotNone(item.received_at)
        self.assertIsNone(item.received_from)

    def test_undo_puts_it_back(self) -> None:
        item = _liked(self.ada)
        self.owner.post(f"/my-list/{item.id}/receive/")
        response = self.owner.post(f"/my-list/{item.id}/unreceive/", follow=True)
        self.assertContains(response, "Moved Mug back to your list.")
        self.assertContains(response, f'action="/my-list/{item.id}/react/"')

    def test_receive_twice_is_refused_quietly(self) -> None:
        item = _liked(self.ada)
        self.owner.post(f"/my-list/{item.id}/receive/")
        response = self.owner.post(f"/my-list/{item.id}/receive/", follow=True)
        self.assertContains(response, "Could not save that.")

    def test_shopper_cannot_receive(self) -> None:
        item = _liked(self.ada)
        response = self.shopper.post(f"/my-list/{item.id}/receive/")
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertIsNone(item.received_at)

    def test_receive_requires_post_and_login(self) -> None:
        item = _liked(self.ada)
        self.assertEqual(self.owner.get(f"/my-list/{item.id}/receive/").status_code, 405)
        response = Client().post(f"/my-list/{item.id}/receive/")
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertIsNone(item.received_at)

    def test_owner_response_identical_claimed_or_not(self) -> None:
        def receive_page(claim_first: bool) -> bytes:
            Item.objects.all().delete()
            item = _liked(self.ada, "Same")
            if claim_first:
                self.shopper.post(f"/their-list/{item.id}/claim/")
            response = self.owner.post(
                f"/my-list/{item.id}/receive/", {"received_from": "Grandma"}, follow=True
            )
            self.assertContains(response, "Moved Same to Received.")
            body = _CSRF.sub(b"CSRF", response.content)
            return body.replace(f"/my-list/{item.id}/".encode(), b"/my-list/ID/")

        self.assertEqual(receive_page(claim_first=False), receive_page(claim_first=True))

    def test_shopper_told_when_claim_released_by_receive(self) -> None:
        item = _liked(self.ada)
        self.shopper.post(f"/their-list/{item.id}/claim/")
        self.owner.post(f"/my-list/{item.id}/receive/", {"received_from": "Grandma"})
        page = self.shopper.get("/their-list/")
        self.assertContains(page, "Ada already has")
        self.assertContains(page, "From Grandma")
        self.assertContains(
            page,
            "Ada got this from someone else after you claimed it, "
            "so your claim was released.",
        )
        self.assertNotContains(page, f'action="/their-list/{item.id}/give/"')

    def test_stale_give_after_receive_explains(self) -> None:
        item = _liked(self.ada)
        self.shopper.post(f"/their-list/{item.id}/claim/")
        self.owner.post(f"/my-list/{item.id}/receive/")
        response = self.shopper.post(f"/their-list/{item.id}/give/", follow=True)
        self.assertContains(response, "Ada already got Mug from someone else.")
        item.refresh_from_db()
        self.assertIsNone(item.given_at)

    def test_mark_given_has_a_confirm_step(self) -> None:
        item = _liked(self.ada)
        self.shopper.post(f"/their-list/{item.id}/claim/")
        page = self.shopper.get("/their-list/")
        self.assertContains(page, '<details class="confirm">')
        self.assertContains(page, "Only confirm once Ada has it.")
        self.assertContains(page, "Ada has it</button>")

    def test_given_gift_shows_from_partner_with_no_undo(self) -> None:
        item = _liked(self.ada)
        self.shopper.post(f"/their-list/{item.id}/claim/")
        self.shopper.post(f"/their-list/{item.id}/give/")
        page = self.owner.get("/my-list/")
        self.assertContains(page, "From Bea")
        self.assertNotContains(page, f'action="/my-list/{item.id}/unreceive/"')
        response = self.owner.post(f"/my-list/{item.id}/unreceive/", follow=True)
        self.assertContains(response, "Could not save that.")
        item.refresh_from_db()
        self.assertIsNotNone(item.received_at)
