"""Who may do what: the mutation table in V1.md.

Each class is one action. ``assert_silent`` on owner-facing refusals
checks the error text, not just the exception type.
"""

from domain.errors import Conflict, Forbidden, Invalid, NotFound
from domain.store import InMemoryStore
from domain.tests.harness import NOW, DomainTestCase, two_people
from domain.views import OwnerItemView, ShopperItemView


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


class EditTests(DomainTestCase):
    def test_adder_edits_text_and_leaves_claim_state_alone(self) -> None:
        """Name, link, price and tag change. A posted private note does not."""

        store, ada, bea = two_people()
        view = store.add_item(
            ada.id,
            ada.id,
            "Watch",
            url="https://example.com/a",
            price_text="$1",
            tag="Old",
            giver_note="keep",
        )
        store.claim(bea.id, view.id)
        before = store._items[view.id]
        edited = store.edit_item(
            ada.id,
            view.id,
            "  Watch band  ",
            url=" https://example.com/b ",
            price_text=" around $20 ",
            tag=" Birthday ",
            giver_note="nope",
            update_giver_note=True,
        )
        after = store._items[view.id]
        self.assertIsInstance(edited, OwnerItemView)
        self.assertEqual(edited.name, "Watch band")
        self.assertEqual(after.url, "https://example.com/b")
        self.assertEqual(after.price_text, "around $20")
        self.assertEqual(after.tag, "Birthday")
        self.assertEqual(after.giver_note, "keep")
        self.assertEqual(after.reaction, before.reaction)
        self.assertEqual(after.reacted_at, before.reacted_at)
        self.assertEqual(after.claimed_by_id, bea.id)
        self.assertEqual(after.claimed_at, before.claimed_at)
        self.assertIsNone(after.given_at)
        self.assertEqual(after.owner_id, ada.id)
        self.assertEqual(after.added_by_id, ada.id)
        self.assertEqual(after.created_at, before.created_at)
        self.assertFalse(hasattr(edited, "claimed_by_id"))
        self.assertFalse(hasattr(edited, "giver_note"))

    def test_blank_optional_fields_clear(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(
            ada.id,
            ada.id,
            "Watch",
            url="https://example.com/a",
            price_text="$1",
            tag="Birthday",
        )
        store.edit_item(ada.id, view.id, "Watch", url="", price_text="  ", tag=None)
        item = store._items[view.id]
        self.assertIsNone(item.url)
        self.assertIsNone(item.price_text)
        self.assertIsNone(item.tag)

    def test_name_is_required(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Watch")
        with self.assertRaises(Invalid) as ctx:
            store.edit_item(ada.id, view.id, "   ")
        self.assertEqual(str(ctx.exception), "Name is required")
        self.assertEqual(store._items[view.id].name, "Watch")

    def test_shopper_edits_suggestion_including_private_note(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(bea.id, ada.id, "Coat", giver_note="navy")
        store.react(ada.id, view.id, "liked")
        store.claim(bea.id, view.id)
        edited = store.edit_item(
            bea.id,
            view.id,
            "Wool coat",
            url="https://example.com/coat",
            price_text="$80",
            tag="Birthday",
            giver_note=" navy, size M ",
            update_giver_note=True,
        )
        self.assertIsInstance(edited, ShopperItemView)
        self.assertEqual(edited.name, "Wool coat")
        self.assertEqual(edited.giver_note, "navy, size M")
        self.assertEqual(edited.claimed_by_id, bea.id)
        self.assertEqual(edited.reaction, "liked")
        owner = store.owner_item(ada.id, view.id)
        self.assertEqual(owner.name, "Wool coat")
        self.assertFalse(hasattr(owner, "giver_note"))
        self.assertFalse(hasattr(owner, "claimed_by_id"))

    def test_omitted_note_flag_does_not_clear_it(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(bea.id, ada.id, "Coat", giver_note="navy")
        store.edit_item(bea.id, view.id, "Wool coat", giver_note="nope")
        self.assertEqual(store._items[view.id].giver_note, "navy")
        self.assertEqual(store._items[view.id].name, "Wool coat")

    def test_partner_cannot_edit_an_item_they_did_not_add(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Watch")
        store.claim(bea.id, view.id)
        with self.assertRaises(Forbidden) as ctx:
            store.edit_item(bea.id, view.id, "Stolen")
        self.assert_silent(ctx.exception)
        self.assertEqual(store._items[view.id].name, "Watch")
        self.assertEqual(store._items[view.id].claimed_by_id, bea.id)

    def test_owner_cannot_edit_a_suggestion_they_did_not_add(self) -> None:
        """Same refusal claimed or not, and the private note stays put."""

        store, ada, bea = two_people()
        pending = store.add_item(bea.id, ada.id, "Puzzle", giver_note="secret")
        liked = store.add_item(bea.id, ada.id, "Kettle", giver_note="secret")
        store.react(ada.id, liked.id, "liked")
        store.claim(bea.id, liked.id)
        messages = []
        for item_id in (pending.id, liked.id):
            with self.assertRaises(Forbidden) as ctx:
                store.edit_item(ada.id, item_id, "Changed")
            self.assert_silent(ctx.exception)
            messages.append(str(ctx.exception))
        self.assertEqual(messages[0], messages[1])
        self.assertEqual(store._items[pending.id].name, "Puzzle")
        self.assertEqual(store._items[liked.id].name, "Kettle")
        self.assertEqual(store._items[liked.id].giver_note, "secret")
        self.assertEqual(store._items[liked.id].claimed_by_id, bea.id)

    def test_stranger_cannot_edit(self) -> None:
        store, ada, _bea = two_people()
        cal = store.add_person("Cal", "cal@example.com")
        view = store.add_item(ada.id, ada.id, "Watch")
        with self.assertRaises(Forbidden) as ctx:
            store.edit_item(cal.id, view.id, "Stolen")
        self.assert_silent(ctx.exception)
        self.assertEqual(store._items[view.id].name, "Watch")

    def test_adder_cannot_edit_once_they_can_no_longer_shop(self) -> None:
        """can_shop is re-checked. Losing the connection closes the edit."""

        store, ada, _bea = two_people()
        cal = store.add_person("Cal", "cal@example.com")
        store.connect(ada.id, cal.id)
        view = store.add_item(cal.id, ada.id, "Suggestion", giver_note="quiet")
        pair = (ada.id, cal.id) if ada.id < cal.id else (cal.id, ada.id)
        del store._connections[pair]
        with self.assertRaises(Forbidden) as ctx:
            store.edit_item(
                cal.id, view.id, "Changed", giver_note="no", update_giver_note=True
            )
        self.assert_silent(ctx.exception)
        self.assertEqual(store._items[view.id].name, "Suggestion")
        self.assertEqual(store._items[view.id].giver_note, "quiet")

    def test_owner_can_edit_with_no_partner(self) -> None:
        store = InMemoryStore(now=lambda: NOW)
        ada = store.add_person("Ada", "ada@example.com")
        view = store.add_item(ada.id, ada.id, "Lamp")
        edited = store.edit_item(ada.id, view.id, "Lampshade")
        self.assertEqual(edited.name, "Lampshade")

    def test_edit_after_give_keeps_history(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Book")
        store.claim(bea.id, view.id)
        store.give(bea.id, view.id)
        edited = store.edit_item(ada.id, view.id, "Novel")
        self.assertEqual(edited.name, "Novel")
        self.assertEqual(edited.received_from_id, bea.id)
        self.assertIsNotNone(store._items[view.id].given_at)
        self.assertEqual(store._items[view.id].claimed_by_id, bea.id)

    def test_missing_item_is_not_found(self) -> None:
        store, ada, _bea = two_people()
        with self.assertRaises(NotFound):
            store.edit_item(ada.id, 999, "Nope")

    def test_unknown_person_is_not_found(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Watch")
        with self.assertRaises(NotFound):
            store.edit_item(99, view.id, "Nope")
