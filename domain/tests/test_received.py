"""Hand-over and "got it elsewhere": the Received rows in V1.md.

Give is the hand-over: the owner sees the gift as received from the
claimer. The owner can also record a gift received from someone else,
which releases any claim and tells the shopper.
"""

from domain.errors import Conflict, Forbidden, Invalid
from domain.tests.harness import NOW, DomainTestCase, two_people
from domain.views import liked_open, needs_reaction, received


class GiveTests(DomainTestCase):
    def test_give_sets_received_at_with_given_at(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Scarf")
        store.claim(bea.id, view.id)
        shopper = store.give(bea.id, view.id)
        self.assertEqual(shopper.given_at, NOW)
        self.assertEqual(shopper.received_at, NOW)
        self.assertTrue(received(store._items[view.id]))


class ReceiveTests(DomainTestCase):
    def test_owner_records_gift_from_someone_else(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        owner = store.receive(ada.id, view.id, "  Grandma  ")
        self.assertEqual(owner.received_at, NOW)
        self.assertEqual(owner.received_from, "Grandma")
        self.assertIsNone(owner.received_from_id)
        self.assertFalse(liked_open(store._items[view.id]))

    def test_from_is_optional(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        self.assertIsNone(store.receive(ada.id, view.id, "   ").received_from)

    def test_pending_suggestion_no_longer_needs_reaction(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(bea.id, ada.id, "Lamp")
        store.receive(ada.id, view.id)
        self.assertFalse(needs_reaction(store._items[view.id]))
        self.assertEqual(store.needs_reaction_count(ada.id), 0)

    def test_releases_claim_and_tells_shopper(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        store.claim(bea.id, view.id)
        store.receive(ada.id, view.id, "Grandma")
        shopper = store.shopper_item(bea.id, view.id)
        self.assertIsNone(shopper.claimed_by_id)
        self.assertIsNone(shopper.given_at)
        self.assertEqual(shopper.claim_released_at, NOW)
        self.assertEqual(shopper.received_from, "Grandma")

    def test_shopper_actions_on_received_item_say_why(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        store.claim(bea.id, view.id)
        store.receive(ada.id, view.id)
        for action in (store.claim, store.unclaim, store.give):
            with self.subTest(action=action.__name__):
                with self.assertRaises(Conflict) as ctx:
                    action(bea.id, view.id)
                self.assertEqual(ctx.exception.reason, "received")

    def test_only_owner_receives(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        with self.assertRaises(Forbidden):
            store.receive(bea.id, view.id)

    def test_cannot_receive_twice(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        store.receive(ada.id, view.id)
        with self.assertRaises(Invalid):
            store.receive(ada.id, view.id, "Someone")

    def test_from_name_length_is_checked(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        with self.assertRaises(Invalid) as ctx:
            store.receive(ada.id, view.id, "x" * 101)
        self.assertEqual(str(ctx.exception), "Name is too long")
        self.assertIsNone(store._items[view.id].received_at)

    def test_dislike_after_receive_keeps_the_notice(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        store.claim(bea.id, view.id)
        store.receive(ada.id, view.id)
        store.react(ada.id, view.id, "disliked")
        self.assertEqual(store._items[view.id].claim_released_at, NOW)


class UnreceiveTests(DomainTestCase):
    def test_undo_puts_item_back(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        store.claim(bea.id, view.id)
        store.receive(ada.id, view.id, "Grandma")
        owner = store.unreceive(ada.id, view.id)
        self.assertIsNone(owner.received_at)
        self.assertIsNone(owner.received_from)
        item = store._items[view.id]
        self.assertTrue(liked_open(item))
        self.assertIsNone(item.claim_released_at)

    def test_cannot_undo_a_gift_the_partner_gave(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Scarf")
        store.claim(bea.id, view.id)
        store.give(bea.id, view.id)
        with self.assertRaises(Invalid):
            store.unreceive(ada.id, view.id)

    def test_cannot_undo_what_was_not_received(self) -> None:
        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        with self.assertRaises(Invalid):
            store.unreceive(ada.id, view.id)

    def test_only_owner_undoes(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Mug")
        store.receive(ada.id, view.id)
        with self.assertRaises(Forbidden):
            store.unreceive(bea.id, view.id)
