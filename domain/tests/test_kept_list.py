"""A private list for someone who is not on the app.

The keeper is the only person who can see it. That person never signs
in. Gifts the keeper got from them are rows on the keeper's own list,
via the existing receive action, with ``received_from`` naming them.
"""

from dataclasses import asdict
from datetime import date

from domain.errors import Conflict, Forbidden, Invalid, NotFound
from domain.tests.harness import SHOPPER_ONLY_FIELDS, DomainTestCase, owner_payload, two_people


class KeptListTests(DomainTestCase):
    def test_keep_person_has_no_login_and_one_connection(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, " Grandma ", email="")
        self.assertIsNone(grandma.user_id)
        self.assertEqual(grandma.name, "Grandma")
        self.assertEqual(grandma.email, "")
        self.assertEqual(store.partner_ids(grandma.id), [ada.id])
        self.assertIn(grandma.id, store.partner_ids(ada.id))
        self.assertTrue(store.can_shop(ada.id, grandma.id))
        self.assertFalse(store.can_shop(grandma.id, ada.id))

    def test_name_is_required(self) -> None:
        store, ada, _bea = two_people()
        with self.assertRaises(Invalid) as ctx:
            store.keep_person(ada.id, "   ")
        self.assertEqual(str(ctx.exception), "Name is required")

    def test_person_without_a_login_cannot_keep_a_list(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        with self.assertRaises(Forbidden):
            store.keep_person(grandma.id, "Someone else")

    def test_no_login_person_cannot_gain_a_second_connection(self) -> None:
        """The list stays private to the keeper."""

        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        with self.assertRaises(Invalid):
            store.connect(bea.id, grandma.id)
        self.assertEqual(store.partner_ids(grandma.id), [ada.id])
        self.assertNotIn(grandma.id, store.partner_ids(bea.id))

    def test_app_partner_is_not_replaced_by_a_kept_person(self) -> None:
        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        self.assertEqual(set(store.partner_ids(ada.id)), {bea.id, grandma.id})
        self.assertEqual(store.partner_id(bea.id), ada.id)
        with self.assertRaises(NotFound):
            store.partner_id(ada.id)

    def test_items_are_liked_when_added(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        view = store.add_item(
            ada.id,
            grandma.id,
            "Scarf",
            giver_note="soft wool",
        )
        self.assertEqual(view.reaction, "liked")
        self.assertEqual(view.giver_note, "soft wool")
        self.assertEqual(view.added_by_id, ada.id)
        row = store._items[view.id]
        self.assertEqual(row.reaction, "liked")
        self.assertEqual(row.reacted_at, row.created_at)

    def test_nobody_can_react(self) -> None:
        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        view = store.add_item(ada.id, grandma.id, "Scarf")
        for actor in (ada, bea, grandma):
            with self.subTest(actor=actor.name):
                with self.assertRaises(Forbidden) as ctx:
                    store.react(actor.id, view.id, "disliked")
                self.assert_silent(ctx.exception)
        self.assertEqual(store._items[view.id].reaction, "liked")

    def test_only_the_keeper_claims_and_mark_given_records_history(self) -> None:
        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        view = store.add_item(ada.id, grandma.id, "Scarf")
        with self.assertRaises(Forbidden) as ctx:
            store.claim(bea.id, view.id)
        self.assert_silent(ctx.exception)

        claimed = store.claim(ada.id, view.id)
        self.assertEqual(claimed.claimed_by_id, ada.id)
        given = store.give(ada.id, view.id)
        self.assertIsNotNone(given.given_at)
        self.assertEqual(given.received_at, given.given_at)
        self.assertEqual(given.claimed_by_id, ada.id)
        history = store.shopper_items(ada.id, grandma.id)
        self.assertEqual(history[0].id, view.id)
        self.assertIsNotNone(history[0].given_at)

    def test_gift_received_from_them_uses_receive_on_the_keepers_list(self) -> None:
        """No new table: the keeper's own item, received_from naming them."""

        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        added = store.add_item(ada.id, ada.id, "Teacup")
        received = store.receive(ada.id, added.id, "Grandma")
        self.assertEqual(received.received_from, "Grandma")
        self.assertIsNone(received.received_from_id)
        self.assertEqual(store._items[added.id].owner_id, ada.id)
        self.assertEqual(len(store._items), 1)
        self.assertNotIn(added.id, {item.id for item in store.shopper_items(ada.id, grandma.id)})

    def test_linking_a_login_hides_open_claims_and_giver_notes(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        noted = store.add_item(ada.id, grandma.id, "Scarf", giver_note="soft wool")
        before_claim = owner_payload(store.owner_items(grandma.id))
        store.claim(ada.id, noted.id)
        self.assertEqual(before_claim, owner_payload(store.owner_items(grandma.id)))
        store.link_login(grandma.id, 1003)

        owner = store.owner_item(grandma.id, noted.id)
        payload = asdict(owner)
        self.assertTrue(SHOPPER_ONLY_FIELDS.isdisjoint(payload))
        self.assertIsNone(owner.received_from_id)
        self.assertEqual(owner.reaction, "liked")
        self.assertEqual(before_claim, owner_payload(store.owner_items(grandma.id)))

        shopper = store.shopper_item(ada.id, noted.id)
        self.assertEqual(shopper.giver_note, "soft wool")
        self.assertEqual(shopper.claimed_by_id, ada.id)

        store.give(ada.id, noted.id)
        revealed = store.owner_item(grandma.id, noted.id)
        self.assertEqual(revealed.received_from_id, ada.id)
        self.assertTrue(SHOPPER_ONLY_FIELDS.isdisjoint(asdict(revealed)))
        self.assertFalse(hasattr(revealed, "giver_note"))

    def test_after_a_login_new_suggestions_wait_for_a_reaction(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        store.link_login(grandma.id, 1003)
        pending = store.add_item(ada.id, grandma.id, "Later")
        self.assertIsNone(pending.reaction)
        liked = store.react(grandma.id, pending.id, "liked")
        self.assertEqual(liked.reaction, "liked")
        self.assertFalse(hasattr(liked, "giver_note"))

    def test_several_shoppers_see_each_others_claims(self) -> None:
        store, ada, bea = two_people()
        cal = store.add_person("Cal", "cal@example.com", user_id=1003)
        store.connect(ada.id, cal.id)
        view = store.add_item(ada.id, ada.id, "Record")
        store.claim(bea.id, view.id)
        seen = store.shopper_item(cal.id, view.id)
        self.assertEqual(seen.claimed_by_id, bea.id)
        with self.assertRaises(Conflict) as ctx:
            store.claim(cal.id, view.id)
        self.assertEqual(ctx.exception.reason, "claimed")

    def test_unconnected_date_cannot_steal_the_badge_when_another_list_exists(self) -> None:
        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        cal = store.add_person("Cal", "cal@example.com", user_id=1003)
        store.add_important_date(cal.id, "Cal day", month=9, day=4)
        store.add_important_date(grandma.id, "Grandma day", month=9, day=5)
        store.add_important_date(bea.id, "Birthday", month=9, day=15)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Grandma day")
        self.assertEqual(upcoming.days, 2)


class StrangerDeniedTests(DomainTestCase):
    """Every action refuses someone with no Connection. The keeper's list too."""

    def test_stranger_is_denied_for_every_action(self) -> None:
        store, ada, bea = two_people()
        cal = store.add_person("Cal", "cal@example.com", user_id=1003)
        item = store.add_item(ada.id, ada.id, "Record")
        store.claim(bea.id, item.id)

        denied: list[tuple[str, object, type[BaseException]]] = [
            ("add", lambda: store.add_item(cal.id, ada.id, "Nope"), Forbidden),
            ("react", lambda: store.react(cal.id, item.id, "disliked"), Forbidden),
            ("claim", lambda: store.claim(cal.id, item.id), Forbidden),
            ("unclaim", lambda: store.unclaim(cal.id, item.id), Forbidden),
            ("give", lambda: store.give(cal.id, item.id), Forbidden),
            ("receive", lambda: store.receive(cal.id, item.id, "X"), Forbidden),
            ("unreceive", lambda: store.unreceive(cal.id, item.id), Forbidden),
            ("delete", lambda: store.delete(cal.id, item.id), Invalid),
        ]
        for name, action, error in denied:
            with self.subTest(action=name):
                with self.assertRaises(error) as ctx:
                    action()
                self.assert_silent(ctx.exception)
        self.assertEqual(store._items[item.id].claimed_by_id, bea.id)
        with self.assertRaises(NotFound):
            store.shopper_item(cal.id, item.id)
        with self.assertRaises(NotFound):
            store.shopper_items(cal.id, ada.id)

    def test_the_other_app_user_cannot_touch_a_kept_list(self) -> None:
        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        item = store.add_item(ada.id, grandma.id, "Scarf")
        store.claim(ada.id, item.id)

        denied: list[tuple[str, object, type[BaseException]]] = [
            ("add", lambda: store.add_item(bea.id, grandma.id, "Nope"), Forbidden),
            ("react", lambda: store.react(bea.id, item.id, "disliked"), Forbidden),
            ("claim", lambda: store.claim(bea.id, item.id), Forbidden),
            ("unclaim", lambda: store.unclaim(bea.id, item.id), Forbidden),
            ("give", lambda: store.give(bea.id, item.id), Forbidden),
            ("receive", lambda: store.receive(bea.id, item.id, "X"), Forbidden),
            ("unreceive", lambda: store.unreceive(bea.id, item.id), Forbidden),
            ("delete", lambda: store.delete(bea.id, item.id), Invalid),
        ]
        for name, action, error in denied:
            with self.subTest(action=name):
                with self.assertRaises(error) as ctx:
                    action()
                self.assert_silent(ctx.exception)
        self.assertEqual(store._items[item.id].name, "Scarf")
        self.assertEqual(store._items[item.id].claimed_by_id, ada.id)
        with self.assertRaises(NotFound):
            store.shopper_item(bea.id, item.id)
        with self.assertRaises(NotFound):
            store.shopper_items(bea.id, grandma.id)
