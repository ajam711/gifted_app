"""How an ``Item`` is shown, and states we never store.

Two separate view types
    ``OwnerItemView`` and ``ShopperItemView`` are siblings, not a base
    class plus extra fields. A shared base (or a dict you ``.update()``)
    is how a secret field later grows onto the owner payload by accident.

Projection copies a list of fields
    ``to_owner_view`` never reads ``claimed_by_id``. If someone adds a
    column to ``Item``, it does not appear on the owner until this
    function is edited on purpose.

Derived states are functions
    ``needs_reaction``, ``liked_open``, ``claimed``, ``given``,
    ``received``, ``disliked`` are predicates over columns. Putting them on the row as
    ``status`` would go stale and would leak through owner serialization.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from domain.models import Item


@dataclass(frozen=True, slots=True)
class OwnerItemView:
    """What the list owner is allowed to see.

    Compare this field list with ``ShopperItemView``. The shopper-only
    five (``giver_note``, ``claimed_by_id``, ``claimed_at``, ``given_at``,
    ``claim_released_at``)
    are not present *at all* — not ``None``, not omitted at JSON dump
    time. ``asdict(owner_view)`` therefore has the same keys before and
    after a claim, which is the secrecy test.

    The received fields are the hand-over reveal. ``received_from_id``
    is the giver only once the shopper has marked it given; before that
    it is ``None`` whatever the claim state.
    """

    id: int
    name: str
    url: str | None
    price_text: str | None
    tag: str | None
    added_by_id: int
    created_at: datetime
    reaction: Literal["liked", "disliked"] | None
    received_at: datetime | None
    received_from_id: int | None
    received_from: str | None


@dataclass(frozen=True, slots=True)
class ShopperItemView:
    """What the partner sees when shopping the owner's list.

    Same public fields as the owner view, plus the five shopper-only
    columns used for claim / "yours" / given-before history and the
    "your claim was released" notice. The received fields tell them the
    owner already has it.
    """

    id: int
    name: str
    url: str | None
    price_text: str | None
    tag: str | None
    added_by_id: int
    created_at: datetime
    reaction: Literal["liked", "disliked"] | None
    giver_note: str | None
    claimed_by_id: int | None
    claimed_at: datetime | None
    given_at: datetime | None
    claim_released_at: datetime | None
    received_at: datetime | None
    received_from: str | None


@dataclass(frozen=True, slots=True)
class UpcomingDate:
    """Home badge: next ``ImportantDate`` for the other person.

    ``days`` is ``(on - today).days``, so 12 means "Birthday in 12 days".
    """

    label: str
    on: date
    days: int


def needs_reaction(item: Item) -> bool:
    """Partner suggestion waiting on the owner's liked / disliked.

    Owner self-adds never hit this: they are inserted already liked.
    """

    return (
        item.added_by_id != item.owner_id
        and item.reaction is None
        and item.received_at is None
    )


def liked_open(item: Item) -> bool:
    """Liked, not claimed, not received — the only state the partner may claim."""

    return (
        item.reaction == "liked"
        and item.claimed_by_id is None
        and item.received_at is None
    )


def claimed(item: Item) -> bool:
    """Claimed and not yet given. Shopper sees this as "yours"."""

    return item.claimed_by_id is not None and item.given_at is None


def given(item: Item) -> bool:
    """Already given. Stays on the list as history so gifts are not repeated."""

    return item.given_at is not None


def received(item: Item) -> bool:
    """The owner has it: given by the partner, or got it elsewhere."""

    return item.received_at is not None


def disliked(item: Item) -> bool:
    """Owner said no. Shopper should not claim this."""

    return item.reaction == "disliked"


def to_owner_view(item: Item) -> OwnerItemView:
    """Project a stored row for the owner. Explicit field list, no ``**dict``."""

    return OwnerItemView(
        id=item.id,
        name=item.name,
        url=item.url,
        price_text=item.price_text,
        tag=item.tag,
        added_by_id=item.added_by_id,
        created_at=item.created_at,
        reaction=item.reaction,
        received_at=item.received_at,
        # The giver is revealed at hand-over and not before.
        received_from_id=item.claimed_by_id if item.given_at is not None else None,
        received_from=item.received_from,
    )


def to_shopper_view(item: Item) -> ShopperItemView:
    """Project a stored row for the shopper, including claim/given fields."""

    return ShopperItemView(
        id=item.id,
        name=item.name,
        url=item.url,
        price_text=item.price_text,
        tag=item.tag,
        added_by_id=item.added_by_id,
        created_at=item.created_at,
        reaction=item.reaction,
        giver_note=item.giver_note,
        claimed_by_id=item.claimed_by_id,
        claimed_at=item.claimed_at,
        given_at=item.given_at,
        claim_released_at=item.claim_released_at,
        received_at=item.received_at,
        received_from=item.received_from,
    )


def occurrence_on_or_after(month: int, day: int, year: int | None, on: date) -> date | None:
    """Next calendar occurrence of an ``ImportantDate`` on or after ``on``.

    Recurring (``year is None``)
        Try this year, then later years. The +8 window exists so 29 Feb
        still resolves to the next leap year (``date(2027, 2, 29)``
        raises ``ValueError``; we skip and keep going).

    One-off (``year`` set)
        That exact date, or ``None`` if it is already in the past.
    """

    if year is not None:
        try:
            occ = date(year, month, day)
        except ValueError:
            return None
        return occ if occ >= on else None
    for candidate_year in range(on.year, on.year + 8):
        try:
            occ = date(candidate_year, month, day)
        except ValueError:
            continue
        if occ >= on:
            return occ
    return None
