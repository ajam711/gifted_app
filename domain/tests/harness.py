"""Shared fixtures for domain tests.

``two_people`` is the V1 world: Ada and Bea, already connected, with
time frozen so payload comparisons are byte-stable.

``assert_silent`` is the secrecy helper: if a refusal exists to hide
claim state, the exception must not *talk* about claims either.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from unittest import TestCase

from domain.errors import DomainError
from domain.models import Person
from domain.store import InMemoryStore
from domain.views import OwnerItemView

# Frozen clock. Matches the V1.md badge example: 3 Sep → birthday on the 15th
# is "in 12 days".
NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)

# Substrings that must never appear in owner-facing error text.
SECRET_SUBSTRINGS = (
    "claim",
    "claimed",
    "claimer",
    "given",
    "giver",
    "giver_note",
    "shopper",
)

# Contract field sets. Tests compare these to ``dataclasses.fields(...)``
# so a new column on the view cannot sneak in unnoticed.
OWNER_VIEW_FIELDS = {
    "id",
    "name",
    "url",
    "price_text",
    "tag",
    "added_by_id",
    "created_at",
    "reaction",
    # The hand-over reveal. Null until the owner has the gift.
    "received_at",
    "received_from_id",
    "received_from",
}

SHOPPER_ONLY_FIELDS = {
    "giver_note",
    "claimed_by_id",
    "claimed_at",
    "given_at",
    "claim_released_at",
}


def two_people() -> tuple[InMemoryStore, Person, Person]:
    """Ada (id 1) and Bea (id 2), connected. Time is ``NOW``."""

    store = InMemoryStore(now=lambda: NOW)
    ada = store.add_person("Ada", "ada@example.com")
    bea = store.add_person("Bea", "bea@example.com")
    store.connect(ada.id, bea.id)
    return store, ada, bea


def owner_payload(views: list[OwnerItemView]) -> str:
    """Canonical JSON of an owner list.

    ``sort_keys=True`` and ISO dates mean two dumps are equal iff the
    views are equal — including key set, which is how we catch a secret
    field appearing after a claim. Byte length of this string is the
    "response size" check.
    """

    return json.dumps(
        [asdict(view) for view in views],
        default=lambda value: value.isoformat(),
        sort_keys=True,
    )


class DomainTestCase(TestCase):
    """unittest.TestCase plus the secrecy assertion."""

    def assert_silent(self, error: BaseException) -> None:
        """Fail if ``error`` could teach the owner that a claim exists."""

        text = str(error).lower()
        for needle in SECRET_SUBSTRINGS:
            self.assertNotIn(needle, text)
        self.assertIsInstance(error, DomainError)
        # The exception itself is a payload. No attached item, no claim id.
        self.assertNotIn("payload", getattr(error, "args", ()))
        self.assertFalse(hasattr(error, "item"))
        self.assertFalse(hasattr(error, "claimed_by_id"))
