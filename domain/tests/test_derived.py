"""Derived states are computed, never stored.

If ``Item`` grows a ``status`` column, or a self-add is left with
``reaction is None``, the rest of the app will lie about the loop.
"""

from dataclasses import fields

from domain.models import Item
from domain.tests.harness import NOW, DomainTestCase, two_people
from domain.views import claimed, disliked, given, liked_open, needs_reaction


class DerivedStateTests(DomainTestCase):
    def test_item_has_no_status_column(self) -> None:
        """The type itself must not have ``status`` / ``state``."""

        names = {field.name for field in fields(Item)}
        self.assertNotIn("status", names)
        self.assertNotIn("state", names)

    def test_owner_self_add_is_liked_open(self) -> None:
        """Same insert: reaction liked *and* reacted_at == created_at."""

        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Headphones")
        item = store._items[view.id]
        self.assertEqual(item.reaction, "liked")
        self.assertEqual(item.reacted_at, item.created_at)
        self.assertTrue(liked_open(item))
        self.assertFalse(needs_reaction(item))
        self.assertFalse(claimed(item))
        self.assertFalse(given(item))
        self.assertFalse(disliked(item))

    def test_partner_suggestion_needs_reaction(self) -> None:
        """``reaction is None`` means "waiting on the owner", nothing else."""

        store, ada, bea = two_people()
        view = store.add_item(bea.id, ada.id, "Cookbook")
        item = store._items[view.id]
        self.assertIsNone(item.reaction)
        self.assertIsNone(item.reacted_at)
        self.assertTrue(needs_reaction(item))
        self.assertFalse(liked_open(item))

    def test_liked_then_claimed_then_given(self) -> None:
        """The four-step loop, checking the predicates flip the way V1.md says."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        item = store._items[view.id]
        self.assertTrue(liked_open(item))

        store.claim(bea.id, view.id)
        item = store._items[view.id]
        self.assertTrue(claimed(item))
        self.assertFalse(liked_open(item))
        self.assertFalse(given(item))

        store.give(bea.id, view.id)
        item = store._items[view.id]
        self.assertTrue(given(item))
        self.assertFalse(claimed(item))
        self.assertFalse(liked_open(item))

    def test_disliked_predicate(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(bea.id, ada.id, "Novelty socks")
        store.react(ada.id, view.id, "disliked")
        item = store._items[view.id]
        self.assertTrue(disliked(item))
        self.assertFalse(needs_reaction(item))
        self.assertFalse(liked_open(item))

    def test_needs_reaction_count_is_pending_on_my_list_only(self) -> None:
        """"N need you" counts suggestions on my list, not on theirs."""

        store, ada, bea = two_people()
        store.add_item(bea.id, ada.id, "For Ada")
        store.add_item(ada.id, bea.id, "For Bea")
        store.add_item(ada.id, ada.id, "Ada already likes this")
        self.assertEqual(store.needs_reaction_count(ada.id), 1)
        self.assertEqual(store.needs_reaction_count(bea.id), 1)

    def test_created_at_uses_store_clock(self) -> None:
        """Injected ``now`` is what tests (and later, reproducibility) rely on."""

        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Clocked")
        self.assertEqual(view.created_at, NOW)
        self.assertEqual(store._items[view.id].reacted_at, NOW)
