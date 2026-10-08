from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from domain import Forbidden, Invalid, NotFound, OwnerItemView, ShopperItemView

from gifts.models import Item, Person
from gifts.store import DjangoStore
from gifts.tests.harness import two_people

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


class DjangoStoreAddTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_own_add_is_liked_on_the_same_insert(self) -> None:
        view = self.store.add_item(self.ada.id, self.ada.id, "Own idea")
        self.assertIsInstance(view, OwnerItemView)
        self.assertEqual(view.reaction, "liked")
        self.assertEqual(view.added_by_id, self.ada.id)
        self.assertEqual(view.created_at, NOW)
        self.assertFalse(hasattr(view, "giver_note"))
        self.assertFalse(hasattr(view, "claimed_by_id"))

    def test_partner_suggestion_is_pending(self) -> None:
        view = self.store.add_item(self.bea.id, self.ada.id, "Suggestion")
        self.assertIsInstance(view, ShopperItemView)
        self.assertIsNone(view.reaction)
        self.assertEqual(view.added_by_id, self.bea.id)

    def test_name_is_required(self) -> None:
        with self.assertRaises(Invalid) as ctx:
            self.store.add_item(self.ada.id, self.ada.id, "   ")
        self.assertEqual(str(ctx.exception), "Name is required")

    def test_optional_fields_persist_for_shopper(self) -> None:
        view = self.store.add_item(
            self.bea.id,
            self.ada.id,
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

    def test_stranger_cannot_add(self) -> None:
        outsider_user = User.objects.create_user(
            "cal", "cal@example.com", "test-pass-cal"
        )
        outsider = Person.objects.create(
            user=outsider_user, name="Cal", email="cal@example.com"
        )
        with self.assertRaises(Forbidden):
            self.store.add_item(outsider.id, self.ada.id, "Nope")

    def test_partner_lookup_uses_connection(self) -> None:
        self.assertEqual(self.store.partner_id(self.ada.id), self.bea.id)
        outsider_user = User.objects.create_user(
            "cal", "cal@example.com", "test-pass-cal"
        )
        outsider = Person.objects.create(
            user=outsider_user, name="Cal", email="cal@example.com"
        )
        with self.assertRaises(NotFound):
            self.store.partner_id(outsider.id)
        self.assertEqual(self.store.partner_id(self.ada.id), self.bea.id)

    def test_owner_list_omits_shopper_fields(self) -> None:
        self.store.add_item(
            self.bea.id,
            self.ada.id,
            "Coat",
            giver_note="navy, not black",
        )
        [view] = self.store.owner_items(self.ada.id)
        self.assertEqual(view.name, "Coat")
        self.assertFalse(hasattr(view, "giver_note"))
        self.assertFalse(hasattr(view, "claimed_by_id"))
        self.assertFalse(hasattr(view, "claimed_at"))
        self.assertFalse(hasattr(view, "given_at"))


class DjangoStoreReactTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_owner_likes_pending_suggestion(self) -> None:
        pending = self.store.add_item(self.bea.id, self.ada.id, "Kettle")
        view = self.store.react(self.ada.id, pending.id, "liked")
        self.assertIsInstance(view, OwnerItemView)
        self.assertEqual(view.reaction, "liked")
        self.assertEqual(view.created_at, NOW)
        self.assertFalse(hasattr(view, "claimed_by_id"))
        self.assertFalse(hasattr(view, "giver_note"))

    def test_owner_can_change_unclaimed_reaction(self) -> None:
        own = self.store.add_item(self.ada.id, self.ada.id, "Lamp")
        view = self.store.react(self.ada.id, own.id, "disliked")
        self.assertEqual(view.reaction, "disliked")

    def test_partner_cannot_react(self) -> None:
        pending = self.store.add_item(self.bea.id, self.ada.id, "Toaster")
        with self.assertRaises(Forbidden):
            self.store.react(self.bea.id, pending.id, "liked")

    def test_dislike_releases_claim(self) -> None:
        view = self.store.add_item(self.ada.id, self.ada.id, "Watch")
        row = Item.objects.get(pk=view.id)
        row.claimed_by = self.bea
        row.claimed_at = NOW
        row.save(update_fields=["claimed_by", "claimed_at"])
        owner_view = self.store.react(self.ada.id, view.id, "disliked")
        self.assertFalse(hasattr(owner_view, "claim_released_at"))
        row.refresh_from_db()
        self.assertEqual(row.reaction, "disliked")
        self.assertIsNone(row.claimed_by_id)
        self.assertEqual(row.claim_released_at, NOW)

    def test_missing_item_is_not_found(self) -> None:
        with self.assertRaises(NotFound):
            self.store.react(self.ada.id, 9999, "liked")
