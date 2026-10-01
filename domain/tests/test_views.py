"""Owner and shopper views are different types, not a dict merge.

If someone later adds a shared base class with optional claim fields,
these tests should fail before that ships.
"""

from dataclasses import fields

from domain.errors import NotFound
from domain.tests.harness import (
    OWNER_VIEW_FIELDS,
    SHOPPER_ONLY_FIELDS,
    DomainTestCase,
    two_people,
)
from domain.views import OwnerItemView, ShopperItemView, to_owner_view, to_shopper_view


class ViewProjectionTests(DomainTestCase):
    def test_owner_and_shopper_are_distinct_types(self) -> None:
        """Neither inherits from the other; both sit directly on ``object``."""

        self.assertFalse(issubclass(OwnerItemView, ShopperItemView))
        self.assertFalse(issubclass(ShopperItemView, OwnerItemView))
        self.assertIs(OwnerItemView.__mro__[1], object)
        self.assertIs(ShopperItemView.__mro__[1], object)

    def test_owner_view_fields_match_contract(self) -> None:
        """Owner field set is exactly the V1.md list — no shopper-only keys."""

        names = {field.name for field in fields(OwnerItemView)}
        self.assertEqual(names, OWNER_VIEW_FIELDS)
        self.assertTrue(SHOPPER_ONLY_FIELDS.isdisjoint(names))

    def test_shopper_view_includes_restricted_fields(self) -> None:
        names = {field.name for field in fields(ShopperItemView)}
        self.assertTrue(SHOPPER_ONLY_FIELDS.issubset(names))

    def test_projection_functions_do_not_copy_secrets_onto_owner(self) -> None:
        """``hasattr`` is False, not "attribute exists and is None"."""

        store, ada, bea = two_people()
        view = store.add_item(
            ada.id,
            ada.id,
            "Camera",
            giver_note="color: black",
        )
        store.claim(bea.id, view.id)
        item = store._items[view.id]
        owner = to_owner_view(item)
        shopper = to_shopper_view(item)
        self.assertFalse(hasattr(owner, "giver_note"))
        self.assertFalse(hasattr(owner, "claimed_by_id"))
        self.assertFalse(hasattr(owner, "claimed_at"))
        self.assertFalse(hasattr(owner, "given_at"))
        self.assertEqual(shopper.giver_note, "color: black")
        self.assertEqual(shopper.claimed_by_id, bea.id)

    def test_owner_item_lookup_hides_partner_owned_items(self) -> None:
        """Ada asking for Bea's item as owner is NotFound, not the row."""

        store, ada, bea = two_people()
        view = store.add_item(bea.id, bea.id, "Bea's thing")
        with self.assertRaises(NotFound):
            store.owner_item(ada.id, view.id)
