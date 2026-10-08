"""The four domain tables.

There is no ``List`` table: a list is ``Item.owner_id == viewer``.
There is no ``Claim`` table and no ``Reaction`` table: those facts live
as columns on ``Item``. There is no stored ``status`` column: UI states
are derived in ``views.py``.

``frozen=True``
    Instances are immutable. The store never mutates a row in place; it
    builds a replacement with ``dataclasses.replace`` (same idea as an
    SQL UPDATE that writes a new version of the row).

``slots=True``
    The instance can only have the declared fields. ``item.status = ...``
    would raise ``AttributeError``, which is the point: status is not data.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Closed set of stored reaction values. None is a fourth, separate meaning
# (see Item.reaction) and is *not* a member of this alias.
Reaction = Literal["liked", "disliked"]


@dataclass(frozen=True, slots=True)
class Person:
    """Someone who can own a list and shop the other list.

    Auth (passwords, sessions) is *adjacent* — Django ``User`` later —
    not a second gift-domain user table. No ``password_hash`` here.

    Attributes:
        id: Stable identifier. Assigned by the store.
        name: Display name.
        email: Contact; not used for signup in V1.
    """

    id: int
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class Connection:
    """The pair. Every "other person" lookup goes through this table.

    Do not write ``[p for p in people if p.id != me][0]``. That breaks
    the moment a third ``Person`` row exists (tests, admin, mistakes).
    Ask the connection instead.

    Constraints the store enforces (same as the SQL we will write later):

    * ``person_a_id < person_b_id`` so (Ada, Bea) and (Bea, Ada) are one row
    * unique ``(person_a_id, person_b_id)``

    Attributes:
        person_a_id: The smaller of the two person ids.
        person_b_id: The larger of the two person ids.
        created_at: When the pair was recorded.
    """

    person_a_id: int
    person_b_id: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImportantDate:
    """A recurring or one-off date that drives the Home badge.

    Store month + day, not a single ``date`` that goes stale next year.
    ``year`` is null for birthdays/anniversaries and set only for a
    one-off event (housewarming, graduation).

    Attributes:
        person_id: Whose date this is (the badge shows the *partner's*).
        label: Free text, e.g. ``"Birthday"``.
        month: 1–12.
        day: 1–31.
        year: Null for recurring; a specific year for one-offs.
    """

    person_id: int
    label: str
    month: int
    day: int
    year: int | None


@dataclass(frozen=True, slots=True)
class Item:
    """One wish-list row. The whole loop lives on this record.

    ``owner_id`` vs ``added_by_id``
        Either person may add to either list. If they differ, this is a
        partner suggestion still waiting on a reaction.

    ``reaction is None``
        Has *one* meaning: partner suggestion, owner has not said
        liked/disliked yet. When the owner adds to their own list we
        persist ``reaction='liked'`` in the same insert so that meaning
        stays unique.

    Shopper-only columns (``giver_note``, ``claimed_by_id``,
    ``claimed_at``, ``given_at``, ``claim_released_at``) *are* stored on the row. They must
    never appear on ``OwnerItemView``. Absence from the view is the
    secrecy rule, not CSS and not ``None`` placeholders.

    Attributes:
        id: Stable identifier.
        owner_id: Whose list this sits on.
        added_by_id: Who created the row.
        name: Required.
        url: Optional outbound link. No in-app checkout.
        price_text: Optional free text (``"around $120"``), not cents.
        tag: Optional free text (occasion or category). Not a table.
        giver_note: Shopper's private note. Omit from owner views.
        reaction: ``liked``, ``disliked``, or ``None`` (pending).
        reacted_at: When reaction was last set.
        claimed_by_id: Who claimed it. Omit from owner views.
        claimed_at: When it was claimed. Omit from owner views.
        given_at: When the claimer marked it given. Omit from owner views.
        claim_released_at: When an owner dislike released the shopper's
            claim. Drives the shopper's notice. Omit from owner views.
        created_at: Insert time.
    """

    id: int
    owner_id: int
    added_by_id: int
    name: str
    url: str | None
    price_text: str | None
    tag: str | None
    giver_note: str | None
    reaction: Reaction | None
    reacted_at: datetime | None
    claimed_by_id: int | None
    claimed_at: datetime | None
    given_at: datetime | None
    claim_released_at: datetime | None
    created_at: datetime
