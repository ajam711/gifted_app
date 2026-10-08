"""Django-backed store. Same method names and view return types as InMemoryStore.

Callers receive ``OwnerItemView`` / ``ShopperItemView``, never a raw ``Item``.
Add-item construction lives in ``domain.prepare_new_item`` so the self-add
liked rule cannot drift. React uses ``domain.prepare_reaction`` so claimed
refusals stay generic. Claim / unclaim / give / receive / unreceive use
the matching ``domain.prepare_*`` helpers for the same reason.

Every mutation goes through ``_mutate``: read the row, apply the domain
rule, then write only if the row is unchanged since the read. If the
other person changed it in between, the rule is re-checked against the
fresh row, so the result is always what the rules say for the current
state, never a mix of two requests.
"""

from collections.abc import Callable
from datetime import datetime, timezone

from domain import (
    Forbidden,
    Invalid,
    NotFound,
    OwnerItemView,
    ShopperItemView,
    prepare_claim,
    prepare_give,
    prepare_new_item,
    prepare_reaction,
    prepare_receive,
    prepare_unclaim,
    prepare_unreceive,
    to_owner_view,
    to_shopper_view,
)
from domain.models import Item as DomainItem

from gifts.models import Connection, Item, Person


# Columns a mutation can change. Every save checks they are unchanged since the read.
_STATE_COLUMNS = (
    "reaction",
    "reacted_at",
    "claimed_by_id",
    "claimed_at",
    "given_at",
    "claim_released_at",
    "received_at",
    "received_from",
)
_MAX_ATTEMPTS = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def domain_item(row: Item) -> DomainItem:
    """ORM row → domain dataclass so projections never see a Django model."""

    return DomainItem(
        id=row.id,
        owner_id=row.owner_id,
        added_by_id=row.added_by_id,
        name=row.name,
        url=row.url,
        price_text=row.price_text,
        tag=row.tag,
        giver_note=row.giver_note,
        reaction=row.reaction,
        reacted_at=row.reacted_at,
        claimed_by_id=row.claimed_by_id,
        claimed_at=row.claimed_at,
        given_at=row.given_at,
        claim_released_at=row.claim_released_at,
        received_at=row.received_at,
        received_from=row.received_from,
        created_at=row.created_at,
    )


class DjangoStore:
    """Product mutations against SQLite. Partner always means Connection."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or _utc_now

    def partner_id(self, person_id: int) -> int:
        self._require_person(person_id)
        partner = self._partner_id_or_none(person_id)
        if partner is None:
            raise NotFound()
        return partner

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
        row = Item.objects.create(**fields)
        item = domain_item(row)
        if actor_id == owner_id:
            return to_owner_view(item)
        return to_shopper_view(item)

    def react(self, actor_id: int, item_id: int, reaction: str) -> OwnerItemView:
        updated = self._mutate(
            item_id,
            lambda item: prepare_reaction(item, actor_id, reaction, self._now()),
        )
        return to_owner_view(updated)

    def claim(self, actor_id: int, item_id: int) -> ShopperItemView:
        updated = self._mutate(
            item_id,
            lambda item: prepare_claim(
                item,
                actor_id,
                self._is_partner(actor_id, item.owner_id),
                self._now(),
            ),
        )
        return to_shopper_view(updated)

    def unclaim(self, actor_id: int, item_id: int) -> ShopperItemView:
        updated = self._mutate(
            item_id,
            lambda item: prepare_unclaim(
                item, actor_id, self._is_partner(actor_id, item.owner_id)
            ),
        )
        return to_shopper_view(updated)

    def give(self, actor_id: int, item_id: int) -> ShopperItemView:
        updated = self._mutate(
            item_id,
            lambda item: prepare_give(
                item,
                actor_id,
                self._is_partner(actor_id, item.owner_id),
                self._now(),
            ),
        )
        return to_shopper_view(updated)

    def receive(
        self, actor_id: int, item_id: int, received_from: str | None = None
    ) -> OwnerItemView:
        """Owner got it from someone else. Same response, claimed or not."""

        updated = self._mutate(
            item_id,
            lambda item: prepare_receive(item, actor_id, received_from, self._now()),
        )
        return to_owner_view(updated)

    def unreceive(self, actor_id: int, item_id: int) -> OwnerItemView:
        """Owner undoes a "got it elsewhere"."""

        updated = self._mutate(
            item_id, lambda item: prepare_unreceive(item, actor_id)
        )
        return to_owner_view(updated)

    def shopper_item(self, actor_id: int, item_id: int) -> ShopperItemView:
        """One item on the partner's list. Anything else is ``NotFound``."""

        owner_id = self.partner_id(actor_id)
        row = self._require_item_row(item_id)
        if row.owner_id != owner_id:
            raise NotFound()
        return to_shopper_view(domain_item(row))

    def owner_items(self, actor_id: int) -> list[OwnerItemView]:
        self._require_person(actor_id)
        rows = Item.objects.filter(owner_id=actor_id).order_by("created_at", "id")
        return [to_owner_view(domain_item(row)) for row in rows]

    def shopper_items(self, actor_id: int) -> list[ShopperItemView]:
        owner_id = self.partner_id(actor_id)
        rows = Item.objects.filter(owner_id=owner_id).order_by("created_at", "id")
        return [to_shopper_view(domain_item(row)) for row in rows]

    def _require_person(self, person_id: int) -> Person:
        try:
            return Person.objects.get(pk=person_id)
        except Person.DoesNotExist:
            raise NotFound() from None

    def _require_item_row(self, item_id: int) -> Item:
        try:
            return Item.objects.get(pk=item_id)
        except Item.DoesNotExist:
            raise NotFound() from None

    def _mutate(
        self, item_id: int, rule: Callable[[DomainItem], DomainItem]
    ) -> DomainItem:
        """Read, apply ``rule``, write if unchanged; re-read and retry if not.

        ``rule`` raises the domain refusal for the state it is given, so
        a retry against the fresh row turns a race into the right answer
        (e.g. a claim that lost to a dislike becomes ``Conflict("disliked")``).
        After ``_MAX_ATTEMPTS`` losses in a row, give up with ``Invalid()``.
        """

        for _attempt in range(_MAX_ATTEMPTS):
            row = self._require_item_row(item_id)
            updated = rule(domain_item(row))
            read = {column: getattr(row, column) for column in _STATE_COLUMNS}
            changes = {column: getattr(updated, column) for column in _STATE_COLUMNS}
            if Item.objects.filter(pk=row.pk, **read).update(**changes) == 1:
                return updated
        raise Invalid()

    def _partner_id_or_none(self, person_id: int) -> int | None:
        conn = (
            Connection.objects.filter(person_a_id=person_id).first()
            or Connection.objects.filter(person_b_id=person_id).first()
        )
        if conn is None:
            return None
        if conn.person_a_id == person_id:
            return conn.person_b_id
        return conn.person_a_id

    def _is_partner(self, person_id: int, other_id: int) -> bool:
        if person_id == other_id:
            return False
        a, b = (
            (person_id, other_id) if person_id < other_id else (other_id, person_id)
        )
        return Connection.objects.filter(person_a_id=a, person_b_id=b).exists()
