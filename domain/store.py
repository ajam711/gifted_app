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
    HomeBadges,
    OwnerItemView,
    ShopperItemView,
    UpcomingDate,
    liked_open,
    needs_reaction,
    soonest_upcoming,
    to_owner_view,
    to_shopper_view,
)

_VALID_REACTIONS = frozenset({"liked", "disliked"})
RECEIVED_FROM_MAX_LENGTH = 100
PERSON_NAME_MAX_LENGTH = 100


def can_shop_pair(
    *,
    actor_id: int,
    owner_id: int,
    actor_has_login: bool,
    connected: bool,
) -> bool:
    """Whether ``actor`` may shop ``owner``'s list.

    The actor must have a login, must not be the owner, and must have a
    ``Connection`` to the owner. A person with no login has at most one
    connection, so the actor who passes this check on that list is the
    keeper. Several logged-in partners of someone who *does* have a
    login may all shop that list.
    """

    if not actor_has_login or actor_id == owner_id or not connected:
        return False
    return True


def can_rename_person(
    *,
    actor_id: int,
    person_id: int,
    actor_has_login: bool,
    person_has_login: bool,
    kept_by_id: int | None,
    actor_can_shop: bool,
) -> bool:
    """Who may change this person's display name.

    The person themselves, when they have a login: the actor is the
    owner (``actor_id == person_id``). Or the keeper. ``can_shop`` is
    enough while they have no login, because that list has one shopper.
    After a login is linked, other shoppers also pass ``can_shop``, and
    only ``kept_by_id == actor_id`` is the keeper. A stranger fails
    ``can_shop`` and is not the owner.
    """

    if not actor_has_login:
        return False
    if actor_id == person_id:
        return person_has_login
    if not actor_can_shop:
        return False
    if not person_has_login:
        return True
    return kept_by_id == actor_id


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
    owner_has_login: bool = True,
) -> dict:
    """Validated columns for a new ``Item``, minus ``id``.

    Persist ``reaction='liked'`` and ``reacted_at`` in the same insert
    when the actor is the owner, or when the owner has no login. Then
    ``reaction is None`` keeps one meaning: a suggestion waiting on
    yes/no from an owner who has a login. Persistence assigns the id.
    """

    cleaned = name.strip()
    if not cleaned:
        raise Invalid("Name is required")
    liked_on_insert = actor_id == owner_id or not owner_has_login
    return {
        "owner_id": owner_id,
        "added_by_id": actor_id,
        "name": cleaned,
        "url": url,
        "price_text": price_text,
        "tag": tag,
        "giver_note": giver_note,
        "reaction": "liked" if liked_on_insert else None,
        "reacted_at": now if liked_on_insert else None,
        "claimed_by_id": None,
        "claimed_at": None,
        "given_at": None,
        "claim_released_at": None,
        "received_at": None,
        "received_from": None,
        "received_from_person_id": None,
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

    Disliking an item that is claimed but not received releases the
    claim and stamps ``claim_released_at`` so the shopper is told. Any
    other reaction clears that stamp. Received items (given, or got
    elsewhere) keep their claim and notice: they are history, whatever
    the owner thinks of them now.
    """

    if item.owner_id != actor_id:
        raise Forbidden()
    if reaction not in _VALID_REACTIONS:
        raise Invalid()
    updated = replace(item, reaction=reaction, reacted_at=now)
    if item.received_at is not None:
        return updated
    if reaction == "disliked" and item.claimed_by_id is not None:
        return replace(
            updated, claimed_by_id=None, claimed_at=None, claim_released_at=now
        )
    return replace(updated, claim_released_at=None)


def _require_shopper(item: Item, actor_id: int, actor_can_shop: bool) -> None:
    """Owner or stranger → ``Forbidden``, before any look at claim state.

    ``actor_can_shop`` is the result of ``can_shop``. This runs first in
    every shopper action so the owner gets the same refusal whatever the
    item's state, and never reaches ``Conflict``.
    """

    if item.owner_id == actor_id or not actor_can_shop:
        raise Forbidden()


def _not_claimable_reason(item: Item, actor_id: int) -> ConflictReason | None:
    """Why the shopper cannot claim this item, or ``None`` if they can."""

    if item.given_at is not None:
        return "given"
    if item.received_at is not None:
        return "received"
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
    actor_can_shop: bool,
    now: datetime,
) -> Item:
    """Replacement row after a valid shopper claim.

    Owner, or anyone ``can_shop`` refuses → ``Forbidden``. A shopper the
    check allows, but the item is not ``liked_open`` → ``Conflict`` with
    the reason. On a no-login owner's list, ``can_shop`` is only the keeper.
    """

    _require_shopper(item, actor_id, actor_can_shop)
    reason = _not_claimable_reason(item, actor_id)
    if reason is not None:
        raise Conflict(reason)
    return replace(
        item, claimed_by_id=actor_id, claimed_at=now, claim_released_at=None
    )


def _require_own_claim(item: Item, actor_id: int, actor_can_shop: bool) -> None:
    """Unclaim / give guard: allowed shopper, holding the claim, not yet given."""

    _require_shopper(item, actor_id, actor_can_shop)
    if item.given_at is not None:
        raise Conflict("given")
    if item.received_at is not None:
        raise Conflict("received")
    if item.claimed_by_id != actor_id:
        raise Conflict("disliked" if item.reaction == "disliked" else "not_claimed")


def prepare_unclaim(item: Item, actor_id: int, actor_can_shop: bool) -> Item:
    """Replacement row after the claimer releases an item not yet given."""

    _require_own_claim(item, actor_id, actor_can_shop)
    return replace(item, claimed_by_id=None, claimed_at=None)


def prepare_give(
    item: Item, actor_id: int, actor_can_shop: bool, now: datetime
) -> Item:
    """Replacement row after the claimer hands it over. ``claimed_by_id`` stays.

    Give sets ``given_at`` and ``received_at`` together. An owner with a
    login sees the gift under Received, from the claimer. An owner with
    no login has no session, so the same write is the keeper's history.
    The UI confirms before calling this, and says so when the owner has
    a login.
    """

    _require_own_claim(item, actor_id, actor_can_shop)
    return replace(item, given_at=now, received_at=now)


def prepare_receive(
    item: Item,
    actor_id: int,
    received_from: str | None,
    now: datetime,
    received_from_person_id: int | None = None,
) -> Item:
    """Replacement row after the owner records a gift received elsewhere.

    Not the owner → ``Forbidden``. Already received → ``Invalid()``.
    Otherwise it succeeds whether or not the item is claimed, and the
    owner's view of the result is the same either way. A claim is
    released with ``claim_released_at`` so the shopper is told.

    ``received_from`` is the name as written. ``received_from_person_id``
    is set only when the caller names a person. This does not look the
    text up, so two people who share a name are not linked by accident.
    The store checks ``can_shop`` before passing an id in.
    """

    if item.owner_id != actor_id:
        raise Forbidden()
    if item.received_at is not None:
        raise Invalid()
    giver = (received_from or "").strip() or None
    if giver is not None and len(giver) > RECEIVED_FROM_MAX_LENGTH:
        raise Invalid("Name is too long")
    updated = replace(
        item,
        received_at=now,
        received_from=giver,
        received_from_person_id=received_from_person_id,
    )
    if item.claimed_by_id is not None:
        return replace(
            updated, claimed_by_id=None, claimed_at=None, claim_released_at=now
        )
    return updated


def prepare_unreceive(item: Item, actor_id: int) -> Item:
    """Undo the owner's "got it elsewhere". A gift the partner gave stays.

    A claim released by the receive stays released; the notice goes.
    """

    if item.owner_id != actor_id:
        raise Forbidden()
    if item.received_at is None or item.given_at is not None:
        raise Invalid()
    return replace(
        item,
        received_at=None,
        received_from=None,
        received_from_person_id=None,
        claim_released_at=None,
    )


def _utc_now() -> datetime:
    """Default clock. Tests replace this with a lambda that returns a fixed instant."""

    return datetime.now(timezone.utc)


class InMemoryStore:
    """All V1 mutations live here so the rules have one place to read.

    Partner means "this actor has a ``Connection`` to that owner".
    Shopper permission is ``can_shop``.
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

    def add_person(
        self, name: str, email: str, user_id: int | None = None
    ) -> Person:
        """Create a person. Does not connect them; call ``connect`` next.

        ``user_id`` null means no login. It is unique when set.
        """

        if user_id is not None and any(
            person.user_id == user_id for person in self._people.values()
        ):
            raise Invalid()
        person = Person(
            id=self._next_person_id, name=name, email=email, user_id=user_id
        )
        self._next_person_id += 1
        self._people[person.id] = person
        return person

    def keep_person(self, actor_id: int, name: str, email: str = "") -> Person:
        """A private list for someone who is not on the app.

        The actor must have a login. The new person has ``user_id`` null
        and exactly one connection: to the actor, who is the keeper.
        """

        actor = self._require_person(actor_id)
        if actor.user_id is None:
            raise Forbidden()
        cleaned = name.strip()
        if not cleaned:
            raise Invalid("Name is required")
        if len(cleaned) > PERSON_NAME_MAX_LENGTH:
            raise Invalid()
        email = email.strip()
        person = self.add_person(cleaned, email, user_id=None)
        person = replace(person, kept_by_id=actor_id)
        self._people[person.id] = person
        self.connect(actor_id, person.id)
        return person

    def link_login(self, person_id: int, user_id: int) -> Person:
        """Attach an auth user to a person. An update, not a new table.

        One-to-one: ``user_id`` cannot already belong to someone else.
        Linking again to the same user is a no-op. A different user is
        ``Invalid``.
        """

        person = self._require_person(person_id)
        if person.user_id == user_id:
            return person
        if person.user_id is not None or any(
            other.user_id == user_id for other in self._people.values()
        ):
            raise Invalid()
        updated = replace(person, user_id=user_id)
        self._people[person.id] = updated
        return updated

    def can_rename(self, actor_id: int, person_id: int) -> bool:
        """See ``can_rename_person``. Missing people are ``NotFound``."""

        actor = self._require_person(actor_id)
        person = self._require_person(person_id)
        shops = False
        if actor.id != person.id:
            shops = self.can_shop(actor.id, person.id)
        return can_rename_person(
            actor_id=actor.id,
            person_id=person.id,
            actor_has_login=actor.user_id is not None,
            person_has_login=person.user_id is not None,
            kept_by_id=person.kept_by_id,
            actor_can_shop=shops,
        )

    def rename_person(self, actor_id: int, person_id: int, name: str) -> Person:
        """Change a display name. Does not read or write any item.

        ``received_from`` on older gifts stays the name that was written
        down. The keeper, or this person once they have a login, may
        call this. A stranger and any other shopper get ``Forbidden``.
        """

        if not self.can_rename(actor_id, person_id):
            raise Forbidden()
        cleaned = name.strip()
        if not cleaned:
            raise Invalid("Name is required")
        if len(cleaned) > PERSON_NAME_MAX_LENGTH:
            raise Invalid("Name is too long")
        person = replace(self._require_person(person_id), name=cleaned)
        self._people[person.id] = person
        return person

    def connect(self, person_id: int, other_id: int) -> Connection:
        """Record one pair, ordered so ``person_a_id < person_b_id``.

        Call order does not matter: ``connect(2, 1)`` stores ``(1, 2)``.
        Connecting the same pair twice, or a person to themselves, is
        ``Invalid``. A person with no login may have only one connection,
        so their list stays private to that keeper.
        """

        self._require_person(person_id)
        self._require_person(other_id)
        if person_id == other_id:
            raise Invalid()
        for pid in (person_id, other_id):
            person = self._people[pid]
            if person.user_id is None and self._partner_ids(pid):
                raise Invalid()
        a, b = (person_id, other_id) if person_id < other_id else (other_id, person_id)
        key = (a, b)
        if key in self._connections:
            raise Invalid()
        conn = Connection(person_a_id=a, person_b_id=b, created_at=self._now())
        self._connections[key] = conn
        return conn

    def partner_ids(self, person_id: int) -> list[int]:
        """Everyone connected to this person, via ``Connection`` only."""

        self._require_person(person_id)
        return self._partner_ids(person_id)

    def partner_id(self, person_id: int) -> int:
        """The other person when this person has exactly one connection.

        Zero or several connections is ``NotFound`` — callers that need
        every list use ``partner_ids``. Walks ``Connection`` rows, never
        ``len(self._people)``.
        """

        partners = self.partner_ids(person_id)
        if len(partners) != 1:
            raise NotFound()
        return partners[0]

    def can_shop(self, actor_id: int, owner_id: int) -> bool:
        """Shopper permission for every shopper action. See ``can_shop_pair``."""

        actor = self._require_person(actor_id)
        owner = self._require_person(owner_id)
        return can_shop_pair(
            actor_id=actor.id,
            owner_id=owner.id,
            actor_has_login=actor.user_id is not None,
            connected=self._is_partner(actor.id, owner.id),
        )

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
        """Soonest upcoming date among people this viewer is connected to.

        Home badge: "Birthday in 12 days". Uses ``partner_ids``, so an
        unconnected ``Person`` row cannot steal the answer. No connection
        is ``NotFound``.
        """

        partners = set(self.partner_ids(viewer_id))
        if not partners:
            raise NotFound()
        return soonest_upcoming(
            (record for record in self._dates if record.person_id in partners),
            on,
        )

    def next_important_date(self, person_id: int, on: date) -> UpcomingDate | None:
        """Soonest upcoming date for this person. The caller already chose them."""

        self._require_person(person_id)
        return soonest_upcoming(
            (record for record in self._dates if record.person_id == person_id),
            on,
        )

    def home_badges(self, viewer_id: int, on: date) -> HomeBadges:
        """Owner Home payload: "N need you" and the soonest partner date.

        No connection means no date badge. The date is the soonest
        ``ImportantDate`` among people reached through ``Connection``.
        The count is still the viewer's own ``needs_reaction`` rows.
        Claim state is not an input.
        """

        upcoming = None
        if self.partner_ids(viewer_id):
            upcoming = self.next_partner_important_date(viewer_id, on)
        return HomeBadges(
            needs_you=self.needs_reaction_count(viewer_id),
            upcoming=upcoming,
        )

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

        * Actor == owner, or the owner has no login: persist
          ``reaction='liked'`` and ``reacted_at`` in this same insert.
          A self-add returns ``OwnerItemView``. Adding to someone else's
          list returns ``ShopperItemView``, so a giver note is visible to
          the shopper and never to the owner.
        * Actor can shop an owner who has a login: leave ``reaction``
          null (needs reaction).
        """

        actor = self._require_person(actor_id)
        owner = self._require_person(owner_id)
        if actor.user_id is None:
            raise Forbidden()
        if owner_id != actor_id and not self.can_shop(actor_id, owner_id):
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
            owner_has_login=owner.user_id is not None,
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
        owner = self._require_person(item.owner_id)
        if owner.user_id is None:
            raise Forbidden()
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
            item, actor_id, self.can_shop(actor_id, item.owner_id), self._now()
        )
        self._items[item.id] = updated
        return to_shopper_view(updated)

    def unclaim(self, actor_id: int, item_id: int) -> ShopperItemView:
        """Claimer releases an item that is not yet given."""

        item = self._require_item(item_id)
        updated = prepare_unclaim(
            item, actor_id, self.can_shop(actor_id, item.owner_id)
        )
        self._items[updated.id] = updated
        return to_shopper_view(updated)

    def give(self, actor_id: int, item_id: int) -> ShopperItemView:
        """Claimer marks the gift given. ``claimed_by_id`` stays for history."""

        item = self._require_item(item_id)
        updated = prepare_give(
            item, actor_id, self.can_shop(actor_id, item.owner_id), self._now()
        )
        self._items[updated.id] = updated
        return to_shopper_view(updated)

    def receive(
        self,
        actor_id: int,
        item_id: int,
        received_from: str | None = None,
        received_from_person_id: int | None = None,
    ) -> OwnerItemView:
        """Owner got it from someone else. Same response, claimed or not.

        Pass ``received_from_person_id`` only to link a specific person
        the actor can shop. Omitting it leaves the gift as free text.
        The text is never matched to a person.
        """

        item = self._require_item(item_id)
        if item.owner_id == actor_id and received_from_person_id is not None:
            self._require_person(received_from_person_id)
            if not self.can_shop(actor_id, received_from_person_id):
                raise Forbidden()
        updated = prepare_receive(
            item,
            actor_id,
            received_from,
            self._now(),
            received_from_person_id=received_from_person_id,
        )
        self._items[updated.id] = updated
        return to_owner_view(updated)

    def unreceive(self, actor_id: int, item_id: int) -> OwnerItemView:
        """Owner undoes a "got it elsewhere"."""

        item = self._require_item(item_id)
        updated = prepare_unreceive(item, actor_id)
        self._items[updated.id] = updated
        return to_owner_view(updated)

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

    def shopper_items(
        self, actor_id: int, owner_id: int | None = None
    ) -> list[ShopperItemView]:
        """A list this actor can shop, projected for the shopper.

        Omit ``owner_id`` only when the actor has exactly one connection.
        Anyone ``can_shop`` refuses is ``NotFound``.
        """

        if owner_id is None:
            owner_id = self.partner_id(actor_id)
        elif not self.can_shop(actor_id, owner_id):
            raise NotFound()
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
        if not self.can_shop(actor_id, item.owner_id):
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

    def _partner_ids(self, person_id: int) -> list[int]:
        """Scan connections for this id. The pair is unordered at lookup time."""

        found: list[int] = []
        for conn in self._connections.values():
            if conn.person_a_id == person_id:
                found.append(conn.person_b_id)
            elif conn.person_b_id == person_id:
                found.append(conn.person_a_id)
        found.sort()
        return found

    def _is_partner(self, person_id: int, other_id: int) -> bool:
        """True only if a ``Connection`` row exists for this unordered pair."""

        if person_id == other_id:
            return False
        a, b = (person_id, other_id) if person_id < other_id else (other_id, person_id)
        return (a, b) in self._connections
