"""Who may rename a person, and what a rename does to gift history.

``received_from`` stays the name written when the gift was recorded.
Rename does not update that column and does not guess which person an
unlinked name belongs to. A gift recorded from a specific person's list
stores that person's id. Two people who share a name stay apart, and
the UI can show the recorded name beside the current one.
"""

from dataclasses import asdict

from domain.errors import Forbidden, Invalid
from domain.tests.harness import SHOPPER_ONLY_FIELDS, DomainTestCase, owner_payload, two_people
from domain.views import gift_recorded_from, received_from_label


class CanRenameTests(DomainTestCase):
    def test_keeper_may_rename(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        self.assertTrue(store.can_shop(ada.id, grandma.id))
        self.assertNotEqual(ada.id, grandma.id)
        self.assertTrue(store.can_rename(ada.id, grandma.id))
        renamed = store.rename_person(ada.id, grandma.id, "  Nana  ")
        self.assertEqual(renamed.name, "Nana")
        self.assertEqual(renamed.kept_by_id, ada.id)
        self.assertFalse(hasattr(renamed, "claimed_by_id"))
        self.assertFalse(hasattr(renamed, "received_from"))

    def test_person_themselves_may_rename_once_they_have_a_login(self) -> None:
        """Owner check: the actor is the person. ``can_shop`` is false for that."""

        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        store.link_login(grandma.id, 1003)
        grandma = store._people[grandma.id]
        self.assertEqual(grandma.id, grandma.id)
        self.assertIsNotNone(grandma.user_id)
        self.assertFalse(store.can_shop(grandma.id, grandma.id))
        self.assertEqual(grandma.kept_by_id, ada.id)
        self.assertTrue(store.can_rename(grandma.id, grandma.id))
        self.assertEqual(store.rename_person(grandma.id, grandma.id, "Nana").name, "Nana")
        self.assertTrue(store.can_shop(ada.id, grandma.id))
        self.assertTrue(store.can_rename(ada.id, grandma.id))

    def test_stranger_is_denied(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        cal = store.add_person("Cal", "cal@example.com", user_id=1003)
        self.assertFalse(store.can_shop(cal.id, grandma.id))
        self.assertNotEqual(cal.id, grandma.id)
        self.assertFalse(store.can_rename(cal.id, grandma.id))
        with self.assertRaises(Forbidden) as ctx:
            store.rename_person(cal.id, grandma.id, "Nana")
        self.assert_silent(ctx.exception)
        self.assertEqual(store._people[grandma.id].name, "Grandma")

    def test_other_shopper_is_denied(self) -> None:
        """``can_shop`` is true and they are still not the keeper or the owner."""

        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        store.link_login(grandma.id, 1003)
        store.connect(bea.id, grandma.id)
        self.assertTrue(store.can_shop(bea.id, grandma.id))
        self.assertNotEqual(bea.id, grandma.id)
        self.assertNotEqual(store._people[grandma.id].kept_by_id, bea.id)
        self.assertFalse(store.can_rename(bea.id, grandma.id))
        with self.assertRaises(Forbidden) as ctx:
            store.rename_person(bea.id, grandma.id, "Nana")
        self.assert_silent(ctx.exception)
        self.assertEqual(store._people[grandma.id].name, "Grandma")
        self.assertTrue(store.can_shop(bea.id, ada.id))
        self.assertNotEqual(bea.id, ada.id)
        self.assertIsNone(store._people[ada.id].kept_by_id)
        self.assertFalse(store.can_rename(bea.id, ada.id))

    def test_person_without_a_login_cannot_rename(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        self.assertIsNone(grandma.user_id)
        self.assertFalse(store.can_rename(grandma.id, grandma.id))
        with self.assertRaises(Forbidden) as ctx:
            store.rename_person(grandma.id, grandma.id, "Nana")
        self.assert_silent(ctx.exception)

    def test_blank_or_long_name_is_rejected(self) -> None:
        store, ada, _bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        with self.assertRaises(Invalid) as blank:
            store.rename_person(ada.id, grandma.id, "   ")
        self.assertEqual(str(blank.exception), "Name is required")
        with self.assertRaises(Invalid) as long:
            store.rename_person(ada.id, grandma.id, "n" * 101)
        self.assertEqual(str(long.exception), "Name is too long")
        self.assertEqual(store._people[grandma.id].name, "Grandma")


class ReceivedFromHistoryTests(DomainTestCase):
    def test_rename_does_not_rewrite_gift_rows_or_owner_payloads(self) -> None:
        store, ada, _bea = two_people()
        sam = store.keep_person(ada.id, "Sam")
        scarf = store.add_item(ada.id, sam.id, "Scarf", giver_note="wool")
        store.claim(ada.id, scarf.id)
        cup = store.add_item(ada.id, ada.id, "Teacup")
        store.receive(ada.id, cup.id, "Sam", sam.id)
        old = store.add_item(ada.id, ada.id, "Book")
        store.receive(ada.id, old.id, "Sam")

        before_owner = owner_payload(store.owner_items(sam.id))
        before_keeper = owner_payload(store.owner_items(ada.id))
        before_rows = [asdict(row) for row in store._items.values()]

        store.rename_person(ada.id, sam.id, "Samantha")

        self.assertEqual(before_owner, owner_payload(store.owner_items(sam.id)))
        self.assertEqual(before_keeper, owner_payload(store.owner_items(ada.id)))
        self.assertEqual(before_rows, [asdict(row) for row in store._items.values()])
        self.assertEqual(store._items[cup.id].received_from, "Sam")
        self.assertEqual(store._items[cup.id].received_from_person_id, sam.id)
        self.assertEqual(store._items[old.id].received_from, "Sam")
        self.assertIsNone(store._items[old.id].received_from_person_id)
        self.assertEqual(store._items[scarf.id].claimed_by_id, ada.id)
        owner = store.owner_item(sam.id, scarf.id)
        self.assertTrue(SHOPPER_ONLY_FIELDS.isdisjoint(asdict(owner)))
        self.assertIsNone(owner.received_from_id)
        cup_view = store.owner_item(ada.id, cup.id)
        self.assertEqual(
            received_from_label(cup_view.received_from, "Samantha"),
            "Sam (now Samantha)",
        )
        self.assertEqual(received_from_label(cup_view.received_from, None), "Sam")

    def test_two_people_who_share_a_name_stay_apart(self) -> None:
        store, ada, _bea = two_people()
        first = store.keep_person(ada.id, "Sam")
        second = store.keep_person(ada.id, "Sam")
        cup = store.add_item(ada.id, ada.id, "Teacup")
        store.receive(ada.id, cup.id, "Sam", first.id)
        book = store.add_item(ada.id, ada.id, "Book")
        store.receive(ada.id, book.id, "Sam", second.id)
        scarf = store.add_item(ada.id, ada.id, "Scarf")
        store.receive(ada.id, scarf.id, "Sam")

        def section(person_id: int, person_name: str, unique: bool) -> list[str]:
            return [
                item.name
                for item in store.owner_items(ada.id)
                if gift_recorded_from(
                    item, person_id, person_name, name_is_unique=unique
                )
            ]

        self.assertEqual(section(first.id, "Sam", False), ["Teacup"])
        self.assertEqual(section(second.id, "Sam", False), ["Book"])

        store.rename_person(ada.id, first.id, "Samantha")
        # "Sam" is unique again, so the unlinked scarf matches whoever is
        # still named Sam. It does not follow Samantha, and the text stays.
        self.assertEqual(section(first.id, "Samantha", True), ["Teacup"])
        self.assertEqual(section(second.id, "Sam", True), ["Book", "Scarf"])
        cup_view = store.owner_item(ada.id, cup.id)
        self.assertEqual(cup_view.received_from, "Sam")
        self.assertEqual(
            received_from_label(cup_view.received_from, "Samantha"),
            "Sam (now Samantha)",
        )
        scarf_view = store.owner_item(ada.id, scarf.id)
        self.assertEqual(scarf_view.received_from, "Sam")
        self.assertIsNone(scarf_view.received_from_person_id)
        self.assertEqual(received_from_label(scarf_view.received_from, None), "Sam")

    def test_free_text_receive_does_not_link_a_shared_name(self) -> None:
        store, ada, _bea = two_people()
        sam = store.keep_person(ada.id, "Sam")
        other = store.keep_person(ada.id, "Sam")
        item = store.add_item(ada.id, ada.id, "Mug")
        view = store.receive(ada.id, item.id, "Sam")
        self.assertIsNone(view.received_from_person_id)
        self.assertFalse(
            gift_recorded_from(view, sam.id, "Sam", name_is_unique=False)
        )
        self.assertFalse(
            gift_recorded_from(view, other.id, "Sam", name_is_unique=False)
        )

    def test_cannot_link_a_gift_to_someone_you_cannot_shop(self) -> None:
        store, ada, bea = two_people()
        grandma = store.keep_person(ada.id, "Grandma")
        item = store.add_item(ada.id, ada.id, "Mug")
        cal = store.add_person("Cal", "cal@example.com", user_id=1003)
        with self.assertRaises(Forbidden) as ctx:
            store.receive(ada.id, item.id, "Cal", cal.id)
        self.assert_silent(ctx.exception)
        self.assertIsNone(store._items[item.id].received_at)
        with self.assertRaises(Forbidden) as not_owner:
            store.receive(bea.id, item.id, "Grandma", grandma.id)
        self.assert_silent(not_owner.exception)
        linked = store.receive(ada.id, item.id, "Grandma", grandma.id)
        self.assertEqual(linked.received_from_person_id, grandma.id)
        self.assertEqual(linked.received_from, "Grandma")

    def test_unreceive_clears_the_link_and_the_recorded_name(self) -> None:
        store, ada, _bea = two_people()
        sam = store.keep_person(ada.id, "Sam")
        item = store.add_item(ada.id, ada.id, "Mug")
        store.receive(ada.id, item.id, "Sam", sam.id)
        cleared = store.unreceive(ada.id, item.id)
        self.assertIsNone(cleared.received_from)
        self.assertIsNone(cleared.received_from_person_id)
        self.assertIsNone(store._items[item.id].received_from_person_id)
