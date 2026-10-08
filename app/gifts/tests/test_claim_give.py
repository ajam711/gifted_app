"""Claim / unclaim / give, through the store and over HTTP.

The secrecy tests compare the owner's real HTML before and after a
claim. Django masks the CSRF token differently on every render, so that
one value is normalized; everything else must match byte for byte.
"""

import re
from datetime import datetime, timezone
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone as dj_timezone

from domain import Conflict, Forbidden, Invalid, NotFound, ShopperItemView

from gifts.models import Item
from gifts.store import DjangoStore
from gifts.tests.harness import PASSWORD, two_people

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
_CSRF = re.compile(rb'name="csrfmiddlewaretoken" value="[^"]+"')


def _liked(owner, name: str = "Watch") -> Item:
    return Item.objects.create(
        owner=owner,
        added_by=owner,
        name=name,
        reaction="liked",
        reacted_at=dj_timezone.now(),
        created_at=dj_timezone.now(),
    )


class DjangoStoreClaimGiveTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_partner_claims_liked_open(self) -> None:
        item = _liked(self.ada)
        view = self.store.claim(self.bea.id, item.id)
        self.assertIsInstance(view, ShopperItemView)
        self.assertEqual(view.claimed_by_id, self.bea.id)
        item.refresh_from_db()
        self.assertEqual(item.claimed_by_id, self.bea.id)
        self.assertEqual(item.claimed_at, NOW)

    def test_owner_cannot_claim_own_item(self) -> None:
        item = _liked(self.ada)
        with self.assertRaises(Forbidden):
            self.store.claim(self.ada.id, item.id)
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)

    def test_cannot_claim_pending_disliked_or_twice(self) -> None:
        pending = self.store.add_item(self.bea.id, self.ada.id, "Suggestion")
        disliked = _liked(self.ada, "Scarf")
        disliked.reaction = "disliked"
        disliked.save(update_fields=["reaction"])
        liked = _liked(self.ada)
        self.store.claim(self.bea.id, liked.id)
        cases = [(pending.id, "not_liked"), (disliked.id, "disliked"), (liked.id, "yours")]
        for item_id, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(Conflict) as ctx:
                    self.store.claim(self.bea.id, item_id)
                self.assertEqual(ctx.exception.reason, reason)

    def test_missing_item_is_not_found(self) -> None:
        with self.assertRaises(NotFound):
            self.store.claim(self.bea.id, 9999)

    def test_unclaim_then_claim_again(self) -> None:
        item = _liked(self.ada)
        self.store.claim(self.bea.id, item.id)
        view = self.store.unclaim(self.bea.id, item.id)
        self.assertIsNone(view.claimed_by_id)
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)
        self.assertIsNone(item.claimed_at)
        self.store.claim(self.bea.id, item.id)

    def test_give_keeps_claimer_and_is_final(self) -> None:
        item = _liked(self.ada)
        self.store.claim(self.bea.id, item.id)
        view = self.store.give(self.bea.id, item.id)
        self.assertEqual(view.given_at, NOW)
        item.refresh_from_db()
        self.assertEqual(item.claimed_by_id, self.bea.id)
        self.assertEqual(item.given_at, NOW)
        for action in (self.store.unclaim, self.store.give):
            with self.subTest(action=action.__name__):
                with self.assertRaises(Conflict) as ctx:
                    action(self.bea.id, item.id)
                self.assertEqual(ctx.exception.reason, "given")

    def test_give_requires_claim(self) -> None:
        item = _liked(self.ada)
        with self.assertRaises(Conflict) as ctx:
            self.store.give(self.bea.id, item.id)
        self.assertEqual(ctx.exception.reason, "not_claimed")

    def test_dislike_releases_claim_and_stamps_it(self) -> None:
        item = _liked(self.ada)
        self.store.claim(self.bea.id, item.id)
        view = self.store.react(self.ada.id, item.id, "disliked")
        self.assertEqual(view.reaction, "disliked")
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)
        self.assertIsNone(item.claimed_at)
        self.assertEqual(item.claim_released_at, NOW)

    def test_owner_cannot_unclaim_or_give(self) -> None:
        item = _liked(self.ada)
        self.store.claim(self.bea.id, item.id)
        with self.assertRaises(Forbidden):
            self.store.unclaim(self.ada.id, item.id)
        with self.assertRaises(Forbidden):
            self.store.give(self.ada.id, item.id)


class ClaimGiveHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()

    def _login(self, username: str) -> None:
        self.assertTrue(self.client.login(username=username, password=PASSWORD))

    def test_login_required(self) -> None:
        for action in ("claim", "unclaim", "give"):
            with self.subTest(action=action):
                response = self.client.post(f"/their-list/1/{action}/")
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login/", response["Location"])

    def test_get_is_not_allowed(self) -> None:
        item = _liked(self.ada)
        self._login("bea")
        response = self.client.get(f"/their-list/{item.id}/claim/")
        self.assertEqual(response.status_code, 405)

    def test_their_list_shows_claim_button_on_liked(self) -> None:
        item = _liked(self.ada)
        self._login("bea")
        response = self.client.get("/their-list/")
        self.assertContains(response, "Watch")
        self.assertContains(response, f'action="/their-list/{item.id}/claim/"')
        self.assertNotContains(response, "Mark given")

    def test_claim_moves_item_to_yours(self) -> None:
        item = _liked(self.ada)
        self._login("bea")
        response = self.client.post(f"/their-list/{item.id}/claim/", follow=True)
        self.assertEqual(
            response.redirect_chain, [(f"/their-list/?item={item.id}", 302)]
        )
        self.assertContains(response, "Claimed Watch.")
        self.assertContains(response, f'class="item item-flash" id="item-{item.id}"')
        self.assertContains(response, "Yours")
        self.assertContains(response, f'action="/their-list/{item.id}/give/"')
        self.assertContains(response, f'action="/their-list/{item.id}/unclaim/"')
        self.assertNotContains(response, f'action="/their-list/{item.id}/claim/"')

    def test_unclaim_puts_it_back(self) -> None:
        item = _liked(self.ada)
        self._login("bea")
        self.client.post(f"/their-list/{item.id}/claim/")
        response = self.client.post(f"/their-list/{item.id}/unclaim/", follow=True)
        self.assertContains(response, "Released Watch.")
        self.assertContains(response, f'action="/their-list/{item.id}/claim/"')
        self.assertNotContains(response, "Yours")

    def test_give_moves_item_to_history(self) -> None:
        item = _liked(self.ada)
        self._login("bea")
        self.client.post(f"/their-list/{item.id}/claim/")
        response = self.client.post(f"/their-list/{item.id}/give/", follow=True)
        self.assertContains(
            response, "Gave Watch to Ada. It&#x27;s in Ada&#x27;s Received list now."
        )
        self.assertContains(response, "Gifts given to Ada")
        self.assertNotContains(response, "Mark given")
        self.assertNotContains(response, "Unclaim")

    def test_disliked_shows_as_not_for_partner(self) -> None:
        item = _liked(self.ada, "Scarf")
        item.reaction = "disliked"
        item.save(update_fields=["reaction"])
        self._login("bea")
        response = self.client.get("/their-list/")
        self.assertContains(response, "Not for Ada")
        self.assertContains(response, "Scarf")
        self.assertNotContains(response, f'action="/their-list/{item.id}/claim/"')

    def test_pending_suggestion_shows_as_waiting(self) -> None:
        self._login("bea")
        response = self.client.post(
            "/their-list/add/", {"name": "Suggestion"}, follow=True
        )
        self.assertContains(response, "Waiting on Ada")
        self.assertContains(response, "Suggestion")
        item = Item.objects.get(name="Suggestion")
        self.assertNotContains(response, f'action="/their-list/{item.id}/claim/"')

    def test_double_claim_says_its_already_yours(self) -> None:
        item = _liked(self.ada)
        self._login("bea")
        self.client.post(f"/their-list/{item.id}/claim/")
        response = self.client.post(f"/their-list/{item.id}/claim/", follow=True)
        self.assertEqual(
            response.redirect_chain, [(f"/their-list/?item={item.id}", 302)]
        )
        self.assertContains(response, "You already claimed Watch. It&#x27;s under Yours.")
        self.assertContains(response, 'class="warning"')
        self.assertContains(response, f'class="item item-flash" id="item-{item.id}"')

    def test_claim_after_dislike_says_not_for_partner(self) -> None:
        """Bea's page still showed Claim; Ada disliked it first. Stays unclaimed."""

        item = _liked(self.ada)
        DjangoStore().react(self.ada.id, item.id, "disliked")
        self._login("bea")
        response = self.client.post(f"/their-list/{item.id}/claim/", follow=True)
        self.assertContains(
            response, "Ada no longer wants Watch. It&#x27;s under Not for Ada."
        )
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)

    def test_released_claim_shows_notice_under_not_for(self) -> None:
        item = _liked(self.ada)
        DjangoStore().claim(self.bea.id, item.id)
        DjangoStore().react(self.ada.id, item.id, "disliked")
        self._login("bea")
        response = self.client.get("/their-list/")
        self.assertContains(response, "Not for Ada")
        self.assertContains(response, "your claim was released")
        self.assertNotContains(response, "Yours")

    def test_give_after_release_explains(self) -> None:
        item = _liked(self.ada)
        self._login("bea")
        self.client.post(f"/their-list/{item.id}/claim/")
        DjangoStore().react(self.ada.id, item.id, "disliked")
        response = self.client.post(f"/their-list/{item.id}/give/", follow=True)
        self.assertContains(response, "Ada no longer wants Watch.")
        item.refresh_from_db()
        self.assertIsNone(item.given_at)

    def test_missing_item_is_404(self) -> None:
        self._login("bea")
        response = self.client.post("/their-list/9999/claim/")
        self.assertEqual(response.status_code, 404)

    def test_claim_csrf_enforced(self) -> None:
        item = _liked(self.ada)
        client = Client(enforce_csrf_checks=True)
        client.login(username="bea", password=PASSWORD)
        response = client.post(f"/their-list/{item.id}/claim/")
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)


class StaleReadTests(TestCase):
    """Each save checks the item is unchanged since it was read.

    ``_require_item_row`` is patched so the first read returns a row read
    *before* the other person's change (the race a real request can hit)
    and the retry reads the database for real.
    """

    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def _stale_once(self, item: Item):
        stale = Item.objects.get(pk=item.pk)
        real = DjangoStore._require_item_row
        reads = iter([stale])
        return mock.patch.object(
            self.store,
            "_require_item_row",
            side_effect=lambda item_id: next(reads, None)
            or real(self.store, item_id),
        )

    def test_dislike_racing_a_claim_releases_it(self) -> None:
        item = _liked(self.ada)
        with self._stale_once(item):
            DjangoStore().claim(self.bea.id, item.id)
            view = self.store.react(self.ada.id, item.id, "disliked")
        self.assertEqual(view.reaction, "disliked")
        item.refresh_from_db()
        self.assertEqual(item.reaction, "disliked")
        self.assertIsNone(item.claimed_by_id)
        self.assertEqual(item.claim_released_at, NOW)

    def test_claim_racing_a_dislike_is_refused_with_reason(self) -> None:
        item = _liked(self.ada)
        with self._stale_once(item):
            DjangoStore().react(self.ada.id, item.id, "disliked")
            with self.assertRaises(Conflict) as ctx:
                self.store.claim(self.bea.id, item.id)
        self.assertEqual(ctx.exception.reason, "disliked")
        item.refresh_from_db()
        self.assertEqual(item.reaction, "disliked")
        self.assertIsNone(item.claimed_by_id)

    def test_give_racing_an_unclaim_is_refused_with_reason(self) -> None:
        item = _liked(self.ada)
        self.store.claim(self.bea.id, item.id)
        with self._stale_once(item):
            DjangoStore().unclaim(self.bea.id, item.id)
            with self.assertRaises(Conflict) as ctx:
                self.store.give(self.bea.id, item.id)
        self.assertEqual(ctx.exception.reason, "not_claimed")
        item.refresh_from_db()
        self.assertIsNone(item.claimed_by_id)
        self.assertIsNone(item.given_at)

    def test_gives_up_after_repeated_losses(self) -> None:
        """If every read is stale, nothing is written and the refusal is generic."""

        item = _liked(self.ada)
        stale = Item.objects.get(pk=item.pk)
        DjangoStore().claim(self.bea.id, item.id)
        with mock.patch.object(self.store, "_require_item_row", return_value=stale):
            with self.assertRaises(Invalid):
                self.store.react(self.ada.id, item.id, "disliked")
        item.refresh_from_db()
        self.assertEqual(item.reaction, "liked")
        self.assertEqual(item.claimed_by_id, self.bea.id)


class OwnerSecrecyHTTPTests(TestCase):
    """The owner's pages and refusals must not change when the partner acts."""

    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.owner = Client()
        self.owner.login(username="ada", password=PASSWORD)
        self.shopper = Client()
        self.shopper.login(username="bea", password=PASSWORD)

    def _owner_pages(self) -> dict[str, bytes]:
        pages = {}
        for path in ("/", "/my-list/"):
            response = self.owner.get(path)
            self.assertEqual(response.status_code, 200)
            pages[path] = _CSRF.sub(b"CSRF", response.content)
        return pages

    def test_owner_pages_identical_after_claim(self) -> None:
        item = _liked(self.ada)
        _liked(self.ada, "Kettle")
        before = self._owner_pages()
        self.shopper.post(f"/their-list/{item.id}/claim/")
        after_claim = self._owner_pages()
        for path in before:
            with self.subTest(path=path):
                self.assertEqual(before[path], after_claim[path])

    def test_give_is_the_hand_over_reveal(self) -> None:
        """Only once Bea confirms the hand-over does Ada see it, from Bea."""

        item = _liked(self.ada)
        self.shopper.post(f"/their-list/{item.id}/claim/")
        before = self.owner.get("/my-list/")
        self.assertNotContains(before, 'id="received-heading"')
        self.assertNotContains(before, "From Bea")
        self.shopper.post(f"/their-list/{item.id}/give/")
        after = self.owner.get("/my-list/")
        self.assertContains(after, 'id="received-heading"')
        self.assertContains(after, "From Bea")
        self.assertNotContains(after, f'action="/my-list/{item.id}/react/"')
        self.assertNotContains(after, f'action="/my-list/{item.id}/unreceive/"')

    def test_owner_refusals_do_not_depend_on_claim_state(self) -> None:
        """Ada probing her own items gets the same answer, claimed or not."""

        open_item = _liked(self.ada, "Open")
        claimed_item = _liked(self.ada, "Claimed")
        self.shopper.post(f"/their-list/{claimed_item.id}/claim/")
        for action in ("claim", "unclaim", "give"):
            with self.subTest(action=action):
                a = self.owner.post(
                    f"/their-list/{open_item.id}/{action}/", follow=True
                )
                b = self.owner.post(
                    f"/their-list/{claimed_item.id}/{action}/", follow=True
                )
                self.assertEqual(a.redirect_chain, b.redirect_chain)
                self.assertContains(a, "Could not save that.")
                self.assertEqual(
                    _CSRF.sub(b"CSRF", a.content), _CSRF.sub(b"CSRF", b.content)
                )
        claimed_item.refresh_from_db()
        self.assertEqual(claimed_item.claimed_by_id, self.bea.id)

    def test_owner_dislike_response_identical_claimed_or_not(self) -> None:
        """Disliking a claimed item looks exactly like disliking any other.

        Same one-item list both times; only the claim differs. Item ids
        differ between the runs, so they are normalized with the CSRF token.
        """

        def dislike_page(claim_first: bool) -> bytes:
            Item.objects.all().delete()
            item = _liked(self.ada, "Same")
            if claim_first:
                self.shopper.post(f"/their-list/{item.id}/claim/")
            response = self.owner.post(
                f"/my-list/{item.id}/react/", {"reaction": "disliked"}, follow=True
            )
            self.assertContains(response, "Saved Same.")
            body = _CSRF.sub(b"CSRF", response.content)
            body = body.replace(f"/my-list/{item.id}/".encode(), b"/my-list/ID/")
            return body.replace(f"-{item.id}\"".encode(), b'-ID"')

        self.assertEqual(dislike_page(claim_first=False), dislike_page(claim_first=True))
