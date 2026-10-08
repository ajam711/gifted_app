"""The one rule: the owner must not observe claims.

Not in field names, not in counts, not in JSON bytes, not in error text,
not in an exception attribute. If you change projection or error
handling, this file is the one that should go red.
"""

from dataclasses import asdict

from domain.errors import Forbidden, Invalid
from domain.tests.harness import (
    SHOPPER_ONLY_FIELDS,
    DomainTestCase,
    owner_payload,
    two_people,
)


class SecrecyTests(DomainTestCase):
    def test_owner_payload_identical_before_and_after_claim(self) -> None:
        """Same ids, same count, same JSON, same byte length, same detail dict."""

        store, ada, bea = two_people()
        store.add_item(ada.id, ada.id, "One")
        store.add_item(bea.id, ada.id, "Two")
        store.react(ada.id, store.owner_items(ada.id)[1].id, "liked")
        before_list = store.owner_items(ada.id)
        before_json = owner_payload(before_list)
        before_ids = [item.id for item in before_list]
        before_detail = asdict(store.owner_item(ada.id, before_ids[0]))

        store.claim(bea.id, before_ids[0])

        after_list = store.owner_items(ada.id)
        after_json = owner_payload(after_list)
        after_ids = [item.id for item in after_list]
        after_detail = asdict(store.owner_item(ada.id, before_ids[0]))

        self.assertEqual(before_ids, after_ids)
        self.assertEqual(len(before_list), len(after_list))
        self.assertEqual(before_json, after_json)
        self.assertEqual(len(before_json.encode()), len(after_json.encode()))
        self.assertEqual(before_detail, after_detail)
        self.assertTrue(SHOPPER_ONLY_FIELDS.isdisjoint(after_detail))

    def test_owner_payload_still_identical_after_give(self) -> None:
        """Give is also shopper-only. Owner JSON must not change then either."""

        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "Scarf")
        before = owner_payload(store.owner_items(ada.id))
        store.claim(bea.id, view.id)
        store.give(bea.id, view.id)
        after = owner_payload(store.owner_items(ada.id))
        self.assertEqual(before, after)

    def test_owner_view_never_has_shopper_keys(self) -> None:
        """Keys absent, not present-and-null. Shopper still sees the note."""

        store, ada, bea = two_people()
        view = store.add_item(
            bea.id,
            ada.id,
            "Hidden note",
            giver_note="do not tell Ada",
        )
        store.react(ada.id, view.id, "liked")
        store.claim(bea.id, view.id)
        payload = asdict(store.owner_item(ada.id, view.id))
        for key in SHOPPER_ONLY_FIELDS:
            self.assertNotIn(key, payload)
        shopper = asdict(store.shopper_item(bea.id, view.id))
        self.assertEqual(shopper["giver_note"], "do not tell Ada")
        self.assertEqual(shopper["claimed_by_id"], bea.id)

    def test_claim_does_not_change_owner_counts(self) -> None:
        """A claim must not drop the item from my list or change "N need you"."""

        store, ada, bea = two_people()
        first = store.add_item(ada.id, ada.id, "A")
        store.add_item(ada.id, ada.id, "B")
        before = len(store.owner_items(ada.id))
        before_need = store.needs_reaction_count(ada.id)
        store.claim(bea.id, first.id)
        self.assertEqual(len(store.owner_items(ada.id)), before)
        self.assertEqual(store.needs_reaction_count(ada.id), before_need)

    def test_owner_claim_attempt_returns_no_item(self) -> None:
        """If claim() returned the row to the owner, secrecy is already gone."""

        store, ada, _bea = two_people()
        view = store.add_item(ada.id, ada.id, "A")
        with self.assertRaises(Forbidden) as ctx:
            result = store.claim(ada.id, view.id)
            self.fail(f"owner received {result!r}")
        self.assert_silent(ctx.exception)
        self.assertEqual(ctx.exception.args, ("Not allowed",))

    def test_dislike_result_identical_claimed_or_not(self) -> None:
        """Disliking always succeeds; the owner's result cannot tell a claim was released."""

        store, ada, bea = two_people()
        open_item = store.add_item(ada.id, ada.id, "A")
        claimed_item = store.add_item(ada.id, ada.id, "A")
        store.claim(bea.id, claimed_item.id)
        a = asdict(store.react(ada.id, open_item.id, "disliked"))
        b = asdict(store.react(ada.id, claimed_item.id, "disliked"))
        a.pop("id")
        b.pop("id")
        self.assertEqual(a, b)
        self.assertTrue(SHOPPER_ONLY_FIELDS.isdisjoint(b))

    def test_owner_gets_forbidden_never_conflict(self) -> None:
        """The owner probing shopper actions gets the same silent refusal in every state."""

        store, ada, bea = two_people()
        open_item = store.add_item(ada.id, ada.id, "Open")
        claimed_item = store.add_item(ada.id, ada.id, "Claimed")
        given_item = store.add_item(ada.id, ada.id, "Given")
        store.claim(bea.id, claimed_item.id)
        store.claim(bea.id, given_item.id)
        store.give(bea.id, given_item.id)
        for action in (store.claim, store.unclaim, store.give):
            for item in (open_item, claimed_item, given_item):
                with self.subTest(action=action.__name__, item=item.name):
                    with self.assertRaises(Forbidden) as ctx:
                        action(ada.id, item.id)
                    self.assertIs(type(ctx.exception), Forbidden)
                    self.assert_silent(ctx.exception)

    def test_delete_error_does_not_reveal_claim(self) -> None:
        store, ada, bea = two_people()
        view = store.add_item(ada.id, ada.id, "A")
        store.claim(bea.id, view.id)
        with self.assertRaises(Invalid) as delete_ctx:
            store.delete(ada.id, view.id)
        with self.assertRaises(Invalid) as missing_ctx:
            store.delete(ada.id, 9999)
        self.assertEqual(str(delete_ctx.exception), str(missing_ctx.exception))
        self.assert_silent(delete_ctx.exception)

    def test_shopper_sees_claim_and_history(self) -> None:
        """Asymmetry check: shopper views differ; owner still sees all three names."""

        store, ada, bea = two_people()
        open_item = store.add_item(ada.id, ada.id, "Open")
        mine = store.add_item(ada.id, ada.id, "Mine")
        history = store.add_item(ada.id, ada.id, "History")
        store.claim(bea.id, mine.id)
        store.claim(bea.id, history.id)
        store.give(bea.id, history.id)
        by_name = {item.name: item for item in store.shopper_items(bea.id)}
        self.assertIsNone(by_name["Open"].claimed_by_id)
        self.assertEqual(by_name["Mine"].claimed_by_id, bea.id)
        self.assertIsNone(by_name["Mine"].given_at)
        self.assertIsNotNone(by_name["History"].given_at)
        self.assertEqual(store.owner_item(ada.id, open_item.id).reaction, "liked")
        owner_names = {item.name for item in store.owner_items(ada.id)}
        self.assertEqual(owner_names, {"Open", "Mine", "History"})
