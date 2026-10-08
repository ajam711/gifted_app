"""In-memory gift store. This is the product rules, not a database.

Later, Django models will persist the same rows. The method names and
return types should survive that swap: callers receive views, never a
raw ``Item``, so owner code cannot accidentally serialize claim fields.

Clock
    ``now`` is injected so tests freeze time. Production uses UTC now.

Ids
    Simple autoincrement. SQLite will assign them later.

Leading underscore
    ``_people``, ``_items``, ``_require_person`` are internals. Tests
    peek at ``_items`` only when they must prove a column was persisted
    (e.g. ``reacted_at`` on the same insert). Application code should
    not.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone

from domain.errors import Conflict, ConflictReason, Forbidden, Invalid, NotFound
from domain.models import Connection, ImportantDate, Item, Person, Reaction
from domain.views import (
    OwnerItemView,
    ShopperItemView,
    UpcomingDate,
    liked_open,
    needs_reaction,
    occurrence_on_or_after,
    to_owner_view,
    to_shopper_view,
)

_VALID_REACTIONS = frozenset({"liked", "disliked"})


def prepare_new_item(
    actor_id: int,
    owner_id: int,
    name: str,
    now: datetime,
    *,
    url: str | None = None,
    price_text: str | None = None,
    tag: str | None = None,
    giver_note: str | None = None,
) -> dict:
    """Validated columns for a new ``Item``, minus ``id``.

    Self-add persists ``reaction='liked'`` and ``reacted_at`` in the same
    insert so ``reaction is None`` keeps one meaning: partner suggestion
    waiting on yes/no. Persistence (memory or Django) assigns the id.
    """

    cleaned = name.strip()
    if not cleaned:
        raise Invalid("Name is required")
    own_list = actor_id == owner_id
    return {
        "owner_id": owner_id,
        "added_by_id": actor_id,
        "name": cleaned,
        "url": url,
        "price_text": price_text,
        "tag": tag,
        "giver_note": giver_note,
        "reaction": "liked" if own_list else None,
        "reacted_at": now if own_list else None,
        "claimed_by_id": None,
        "claimed_at": None,
        "given_at": None,
        "claim_released_at": None,
        "created_at": now,
    }


def prepare_reaction(
    item: Item,
    actor_id: int,
    reaction: str,
    now: datetime,
) -> Item:
    """Replacement row after a valid owner reaction.

    Not the owner → ``Forbidden``. Bad value → ``Invalid()``. Otherwise
    it always succeeds, claimed or not, so the owner's response cannot
    reveal a claim.

    Disliking an item that is claimed but not given releases the claim
    and stamps ``claim_released_at`` so the shopper is told. Any other
    reaction clears that stamp. Given items keep their claim: they are
    history, whatever the owner thinks of them now.
    """

    if item.owner_id != actor_id:
        raise Forbidden()
    if reaction not in _VALID_REACTIONS:
        raise Invalid()
    updated = replace(item, reaction=reaction, reacted_at=now)
    if item.given_at is not None:
        return updated
    if reaction == "disliked" and item.claimed_by_id is not None:
        return replace(
            updated, claimed_by_id=None, claimed_at=None, claim_released_at=now
        )
    return replace(updated, claim_released_at=None)


def _require_shopper(item: Item, actor_id: int, actor_is_partner: bool) -> None:
    """Owner or stranger → ``Forbidden``, before any look at claim state.

    This runs first in every shopper action so the owner gets the same
    refusal whatever the item's state, and never reaches ``Conflict``.
    """

    if item.owner_id == actor_id or not actor_is_partner:
        raise Forbidden()


def _not_claimable_reason(item: Item, actor_id: int) -> ConflictReason | None:
    """Why the shopper cannot claim this item, or ``None`` if they can."""

    if item.given_at is not None:
        return "given"
    if item.claimed_by_id == actor_id:
        return "yours"
    if item.claimed_by_id is not None:
        return "claimed"
    if item.reaction == "disliked":
        return "disliked"
    if item.reaction is None:
        return "not_liked"
    return None


def prepare_claim(
    item: Item,
    actor_id: int,
    actor_is_partner: bool,
    now: datetime,
) -> Item:
    """Replacement row after a valid partner claim.

    Owner or non-partner → ``Forbidden``. Partner, but the item is not
    ``liked_open`` → ``Conflict`` with the reason.
    """

    _require_shopper(item, actor_id, actor_is_partner)
    reason = _not_claimable_reason(item, actor_id)
    if reason is not None:
        raise Conflict(reason)
    return replace(
        item, claimed_by_id=actor_id, claimed_at=now, claim_released_at=None
    )


def _require_own_claim(item: Item, actor_id: int, actor_is_partner: bool) -> None:
    """Unclaim / give guard: partner, holding the claim, not yet given."""

    _require_shopper(item, actor_id, actor_is_partner)
    if item.given_at is not None:
        raise Conflict("given")
    if item.claimed_by_id != actor_id:
        raise Conflict("disliked" if item.reaction == "disliked" else "not_claimed")


def prepare_unclaim(item: Item, actor_id: int, actor_is_partner: bool) -> Item:
    """Replacement row after the claimer releases an item not yet given."""

    _require_own_claim(item, actor_id, actor_is_partner)
    return replace(item, claimed_by_id=None, claimed_at=None)


def prepare_give(
    item: Item, actor_id: int, actor_is_partner: bool, now: datetime
) -> Item:
    """Replacement row after the claimer marks it given. ``claimed_by_id`` stays."""

    _require_own_claim(item, actor_id, actor_is_partner)
    return replace(item, given_at=now)


def _utc_now() -> datetime:
    """Default clock. Tests replace this with a lambda that returns a fixed instant."""

    return datetime.now(timezone.utc)


class InMemoryStore:
    """All V1 mutations live here so the rules have one place to read.

    Partner always means "the other id on this person's ``Connection``".
    """

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or _utc_now
        self._people: dict[int, Person] = {}
        # Key is (person_a_id, person_b_id) with a < b, matching the CHECK.
        self._connections: dict[tuple[int, int], Connection] = {}
        self._dates: list[ImportantDate] = []
        self._items: dict[int, Item] = {}
        self._next_person_id = 1
        self._next_item_id = 1

    def add_person(self, name: str, email: str) -> Person:
        """Create a person. Does not connect them; call ``connect`` next."""

        person = Person(id=self._next_person_id, name=name, email=email)
        self._next_person_id += 1
        self._people[person.id] = person
        return person

    def connect(self, person_id: int, other_id: int) -> Connection:
        """Record the pair, ordered so ``person_a_id < person_b_id``.

        Call order does not matter: ``connect(2, 1)`` stores ``(1, 2)``.
        Connecting the same pair twice, or a person to themselves, is
        ``Invalid``.
        """

        self._require_person(person_id)
        self._require_person(other_id)
        if person_id == other_id:
            raise Invalid()
        a, b = (person_id, other_id) if person_id < other_id else (other_id, person_id)
        key = (a, b)
        if key in self._connections:
            raise Invalid()
        conn = Connection(person_a_id=a, person_b_id=b, created_at=self._now())
        self._connections[key] = conn
        return conn

    def partner_id(self, person_id: int) -> int:
        """The other person on this person's connection.

        Walks ``Connection`` rows, never ``len(self._people)``.
        """

        self._require_person(person_id)
        partner = self._partner_id_or_none(person_id)
        if partner is None:
            raise NotFound()
        return partner

    def add_important_date(
        self,
        person_id: int,
        label: str,
        month: int,
        day: int,
        year: int | None = None,
    ) -> ImportantDate:
        """Attach a badge date to a person. Month/day required; year optional."""

        self._require_person(person_id)
        if not label.strip():
            raise Invalid()
        if month < 1 or month > 12 or day < 1 or day > 31:
            raise Invalid()
        record = ImportantDate(
            person_id=person_id,
            label=label,
            month=month,
            day=day,
            year=year,
        )
        self._dates.append(record)
        return record

    def next_partner_important_date(self, viewer_id: int, on: date) -> UpcomingDate | None:
        """Soonest upcoming date that belongs to the *partner*, not the viewer.

        Home badge: "Birthday in 12 days". Uses ``partner_id``, so a
        third ``Person`` row cannot steal the answer.
        """

        partner = self.partner_id(viewer_id)
        best: UpcomingDate | None = None
        for record in self._dates:
            if record.person_id != partner:
                continue
            occ = occurrence_on_or_after(record.month, record.day, record.year, on)
            if occ is None:
                continue
            days = (occ - on).days
            if best is None or occ < best.on:
                best = UpcomingDate(label=record.label, on=occ, days=days)
        return best

    def add_item(
        self,
        actor_id: int,
        owner_id: int,
        name: str,
        *,
        url: str | None = None,
        price_text: str | None = None,
        tag: str | None = None,
        giver_note: str | None = None,
    ) -> OwnerItemView | ShopperItemView:
        """Add to ``owner_id``'s list. Actor is whoever is saving the form.

        List identity comes from where the screen was opened
        (``owner_id``), not from a list picker.

        * Actor == owner: persist ``reaction='liked'`` and ``reacted_at``
          in this same insert. Return ``OwnerItemView``.
        * Actor is partner: leave ``reaction`` null (needs reaction).
          Return ``ShopperItemView`` so a giver note they typed is visible
          to them and never to the owner.
        """

        self._require_person(actor_id)
        self._require_person(owner_id)
        if owner_id != actor_id and not self._is_partner(actor_id, owner_id):
            raise Forbidden()
        fields = prepare_new_item(
            actor_id,
            owner_id,
            name,
            self._now(),
            url=url,
            price_text=price_text,
            tag=tag,
            giver_note=giver_note,
        )
        item = Item(id=self._next_item_id, **fields)
        self._next_item_id += 1
        self._items[item.id] = item
        if actor_id == owner_id:
            return to_owner_view(item)
        return to_shopper_view(item)

    def react(self, actor_id: int, item_id: int, reaction: Reaction) -> OwnerItemView:
        """Owner sets liked or disliked.

        Not the owner → ``Forbidden`` (the product admits only owners react).
        ``claimed_by_id`` is set → ``Invalid()`` with no reason. Telling
        them why would reveal the claim.
        """

        item = self._require_item(item_id)
        updated = prepare_reaction(item, actor_id, reaction, self._now())
        self._items[item.id] = updated
        return to_owner_view(updated)

    def claim(self, actor_id: int, item_id: int) -> ShopperItemView:
        """Partner claims a ``liked_open`` item.

        Returns a shopper view (the owner must never receive this).
        Owner or stranger → ``Forbidden``; missing → ``NotFound``.
        Partner on a disliked, pending, claimed or given item →
        ``Conflict`` with the reason. Never an item payload.
        """

        item = self._require_item(item_id)
        updated = prepare_claim(
            item, actor_id, self._is_partner(actor_id, item.owner_id), self._now()
        )
        self._items[item.id] = updated
        return to_shopper_view(updated)

    def unclaim(self, actor_id: int, item_id: int) -> ShopperItemView:
        """Claimer releases an item that is not yet given."""

        item = self._require_item(item_id)
        updated = prepare_unclaim(
            item, actor_id, self._is_partner(actor_id, item.owner_id)
        )
        self._items[updated.id] = updated
        return to_shopper_view(updated)

    def give(self, actor_id: int, item_id: int) -> ShopperItemView:
        """Claimer marks the gift given. ``claimed_by_id`` stays for history."""

        item = self._require_item(item_id)
        updated = prepare_give(
            item, actor_id, self._is_partner(actor_id, item.owner_id), self._now()
        )
        self._items[updated.id] = updated
        return to_shopper_view(updated)

    def delete(self, actor_id: int, item_id: int) -> None:
        """Owner deletes an unclaimed item.

        Missing, not owner, or ``claimed_by_id`` set all raise the same
        ``Invalid()``. Branching into ``NotFound`` vs a "cannot delete"
        message would let the owner distinguish "claimed" from "gone".
        """

        item = self._items.get(item_id)
        if item is None or item.owner_id != actor_id or item.claimed_by_id is not None:
            raise Invalid()
        del self._items[item_id]

    def owner_items(self, actor_id: int) -> list[OwnerItemView]:
        """My list. Every row is projected; claimed items still appear, unchanged."""

        self._require_person(actor_id)
        items = [item for item in self._items.values() if item.owner_id == actor_id]
        items.sort(key=lambda item: (item.created_at, item.id))
        return [to_owner_view(item) for item in items]

    def shopper_items(self, actor_id: int) -> list[ShopperItemView]:
        """Their list. Owner is resolved through ``Connection``, then projected."""

        owner_id = self.partner_id(actor_id)
        items = [item for item in self._items.values() if item.owner_id == owner_id]
        items.sort(key=lambda item: (item.created_at, item.id))
        return [to_shopper_view(item) for item in items]

    def owner_item(self, actor_id: int, item_id: int) -> OwnerItemView:
        """Item detail for the owner. Someone else's item is ``NotFound``."""

        item = self._require_item(item_id)
        if item.owner_id != actor_id:
            raise NotFound()
        return to_owner_view(item)

    def shopper_item(self, actor_id: int, item_id: int) -> ShopperItemView:
        """Item detail for the shopper. Owner calling this is ``NotFound``."""

        item = self._require_item(item_id)
        if item.owner_id == actor_id or not self._is_partner(actor_id, item.owner_id):
            raise NotFound()
        return to_shopper_view(item)

    def needs_reaction_count(self, owner_id: int) -> int:
        """Home badge: "N need you" — pending suggestions on *my* list."""

        self._require_person(owner_id)
        return sum(
            1
            for item in self._items.values()
            if item.owner_id == owner_id and needs_reaction(item)
        )

    def _require_person(self, person_id: int) -> Person:
        person = self._people.get(person_id)
        if person is None:
            raise NotFound()
        return person

    def _require_item(self, item_id: int) -> Item:
        item = self._items.get(item_id)
        if item is None:
            raise NotFound()
        return item

    def _partner_id_or_none(self, person_id: int) -> int | None:
        """Scan connections for this id. The pair is unordered at lookup time."""

        for conn in self._connections.values():
            if conn.person_a_id == person_id:
                return conn.person_b_id
            if conn.person_b_id == person_id:
                return conn.person_a_id
        return None

    def _is_partner(self, person_id: int, other_id: int) -> bool:
        """True only if a ``Connection`` row exists for this unordered pair."""

        if person_id == other_id:
            return False
        a, b = (person_id, other_id) if person_id < other_id else (other_id, person_id)
        return (a, b) in self._connections
