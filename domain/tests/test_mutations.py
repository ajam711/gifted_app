"""Who may do what: the mutation table in V1.md.

Each class is one action. ``assert_silent`` on owner-facing refusals
checks the error text, not just the exception type.
"""

from domain.errors import Conflict, Forbidden, Invalid, NotFound
from domain.tests.harness import DomainTestCase, two_people


class AddTests(DomainTestCase):
    def test_either_person_adds_to_either_list(self) -> None:
        """Own add comes back liked; partner suggestion comes back pending."""

        store, ada, bea = two_people()
        own = store.add_item(ada.id, ada.id, "Own idea")
        suggestion = store.add_item(bea.id, ada.id, "Suggestion")
        self.assertEqual(own.added_by_id, ada.id)
        self.assertEqual(own.reaction, "liked")
        self.assertEqual(suggestion.added_by_id, bea.id)
        self.assertIsNone(suggestion.reaction)

    def test_name_is_required(self) -> None:
        """Whitespace-only names are empty after strip. Safe to explain."""

        store, ada, _bea = two_people()
        with self.assertRaises(Invalid) as ctx:
            store.add_item(ada.id, ada.id, "   ")
        self.assertEqual(str(ctx.exception), "Name is required")

    def test_optional_fields_persist_for_shopper(self) -> None:
        """Link, price_text, tag, giver_note survive on the shopper view."""

        store, ada, bea = two_people()
        view = store.add_item(
            bea.id,
            ada.id,
            "Coat",
            url="https://example.com/coat",
            price_text="around $120",
            tag="Birthday",
            giver_note="navy, not black",
        )
        self.assertEqual(view.url, "https://example.com/coat")
        self.assertEqual(view.price_text, "around $120")
        self.assertEqual(view.tag, "Birthday")
        self.assertEqual(view.giver_note, "navy, not black")


class ReactTests(DomainTestCase):
    def test_only_owner_can_react(self) -> None:
        """Partner trying to liked/disliked is Forbidden, and silent."""

        store, ada, bea = two_people()
        view = store.add_item(bea.id, ada.id, "Kettle")
        liked = store.react(ada.id, view.id, "liked")
        self.assertEqual(liked.reaction, "liked")
        other = store.add_item(bea.id, ada.id, "Toaster")
        with self.assertRaises(Forbidden) as ctx:
            store.react(bea.id, other.id, "liked")
        self.assert_silent(ctx.exception)

    def test_owner_can_change_unclaimed_reaction(self) -> None:
        """Liked → disliked on an unclaimed item. Nothing to release."""

        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Lamp")
        changed = store.react(ada.id, view.id, "disliked")
        self.assertEqual(changed.reaction, "disliked")

    def test_dislike_releases_claim(self) -> None:
        """Owner always gets a normal result. The shopper's claim is released and stamped."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Watch")
        store.claim(bea.id, view.id)
        owner = store.react(ada.id, view.id, "disliked")
        self.assertEqual(owner.reaction, "disliked")
        shopper = store.shopper_item(bea.id, view.id)
        self.assertIsNone(shopper.claimed_by_id)
        self.assertIsNone(shopper.claimed_at)
        self.assertIsNotNone(shopper.claim_released_at)

    def test_liking_again_clears_release_notice(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Watch")
        store.claim(bea.id, view.id)
        store.react(ada.id, view.id, "disliked")
        store.react(ada.id, view.id, "liked")
        shopper = store.shopper_item(bea.id, view.id)
        self.assertIsNone(shopper.claim_released_at)
        self.assertIsNone(shopper.claimed_by_id)

    def test_dislike_after_give_keeps_history(self) -> None:
        """Given is history. The reaction is saved; the claim and given_at stay."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Watch")
        store.claim(bea.id, view.id)
        store.give(bea.id, view.id)
        store.react(ada.id, view.id, "disliked")
        shopper = store.shopper_item(bea.id, view.id)
        self.assertEqual(shopper.reaction, "disliked")
        self.assertEqual(shopper.claimed_by_id, bea.id)
        self.assertIsNotNone(shopper.given_at)
        self.assertIsNone(shopper.claim_released_at)


class ClaimTests(DomainTestCase):
    def test_partner_claims_liked_open(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Record")
        claimed = store.claim(bea.id, view.id)
        self.assertEqual(claimed.claimed_by_id, bea.id)
        self.assertIsNotNone(claimed.claimed_at)
        self.assertIsNone(claimed.given_at)

    def test_owner_cannot_claim_own_item(self) -> None:
        """No item comes back on the exception — claiming is a shopper action."""

        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Record")
        with self.assertRaises(Forbidden) as ctx:
            store.claim(ada.id, view.id)
        self.assert_silent(ctx.exception)

    def test_cannot_claim_until_liked(self) -> None:
        """Pending and disliked are not ``liked_open``. The shopper is told which."""

        store, ada, bea = two_people()
        view = store.add_item(bea.id, ada.id, "Puzzle")
        with self.assertRaises(Conflict) as ctx:
            store.claim(bea.id, view.id)
        self.assertEqual(ctx.exception.reason, "not_liked")
        store.react(ada.id, view.id, "disliked")
        with self.assertRaises(Conflict) as ctx:
            store.claim(bea.id, view.id)
        self.assertEqual(ctx.exception.reason, "disliked")

    def test_cannot_claim_twice(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Record")
        store.claim(bea.id, view.id)
        with self.assertRaises(Conflict) as ctx:
            store.claim(bea.id, view.id)
        self.assertEqual(ctx.exception.reason, "yours")

    def test_cannot_claim_given(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Record")
        store.claim(bea.id, view.id)
        store.give(bea.id, view.id)
        with self.assertRaises(Conflict) as ctx:
            store.claim(bea.id, view.id)
        self.assertEqual(ctx.exception.reason, "given")


class UnclaimAndGiveTests(DomainTestCase):
    def test_claimer_unclaims_if_not_given(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        store.claim(bea.id, view.id)
        released = store.unclaim(bea.id, view.id)
        self.assertIsNone(released.claimed_by_id)
        self.assertIsNone(released.claimed_at)

    def test_owner_cannot_unclaim(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        store.claim(bea.id, view.id)
        with self.assertRaises(Forbidden) as ctx:
            store.unclaim(ada.id, view.id)
        self.assert_silent(ctx.exception)

    def test_unclaim_refused_after_give(self) -> None:
        """History is sticky. Once given, it cannot go back to the pile."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        store.claim(bea.id, view.id)
        store.give(bea.id, view.id)
        with self.assertRaises(Conflict) as ctx:
            store.unclaim(bea.id, view.id)
        self.assertEqual(ctx.exception.reason, "given")

    def test_claimer_marks_given(self) -> None:
        """``claimed_by_id`` stays; ``given_at`` is the history flag."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        store.claim(bea.id, view.id)
        done = store.give(bea.id, view.id)
        self.assertIsNotNone(done.given_at)
        self.assertEqual(done.claimed_by_id, bea.id)

    def test_owner_cannot_mark_given(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        store.claim(bea.id, view.id)
        with self.assertRaises(Forbidden) as ctx:
            store.give(ada.id, view.id)
        self.assert_silent(ctx.exception)

    def test_give_requires_claim(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        with self.assertRaises(Conflict) as ctx:
            store.give(bea.id, view.id)
        self.assertEqual(ctx.exception.reason, "not_claimed")

    def test_give_after_dislike_release_says_disliked(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        store.claim(bea.id, view.id)
        store.react(ada.id, view.id, "disliked")
        with self.assertRaises(Conflict) as ctx:
            store.give(bea.id, view.id)
        self.assertEqual(ctx.exception.reason, "disliked")


class DeleteTests(DomainTestCase):
    def test_owner_deletes_unclaimed_item(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Temp")
        store.delete(ada.id, view.id)
        self.assertEqual(store.owner_items(ada.id), [])

    def test_partner_cannot_delete(self) -> None:
        """Same generic Invalid as a claimed delete — not a special "not yours"."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Temp")
        with self.assertRaises(Invalid) as ctx:
            store.delete(bea.id, view.id)
        self.assertEqual(str(ctx.exception), "Invalid")
        self.assert_silent(ctx.exception)

    def test_delete_claimed_is_generic_invalid(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Temp")
        store.claim(bea.id, view.id)
        with self.assertRaises(Invalid) as ctx:
            store.delete(ada.id, view.id)
        self.assertEqual(str(ctx.exception), "Invalid")
        self.assert_silent(ctx.exception)
        self.assertEqual(len(store.owner_items(ada.id)), 1)

    def test_missing_item_delete_matches_claimed_delete_error(self) -> None:
        """If the messages differed, the owner could tell "claimed" from "gone"."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Temp")
        store.claim(bea.id, view.id)
        try:
            store.delete(ada.id, view.id)
            self.fail("expected Invalid")
        except Invalid as claimed_delete:
            claimed_message = str(claimed_delete)
        try:
            store.delete(ada.id, 999)
            self.fail("expected Invalid")
        except Invalid as missing_delete:
            missing_message = str(missing_delete)
        self.assertEqual(claimed_message, missing_message)
        self.assertEqual(claimed_message, "Invalid")

    def test_missing_item_get_is_not_found(self) -> None:
        """Reads may 404. Deletes of missing rows use Invalid, to match claimed."""

        store, ada, _bea = two_people()
        with self.assertRaises(NotFound):
            store.owner_item(ada.id, 999)
