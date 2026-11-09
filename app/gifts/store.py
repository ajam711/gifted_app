"""Django-backed store. Same method names and view return types as InMemoryStore.

Callers receive ``OwnerItemView``, ``ShopperItemView``, or ``HomeBadges``,
never a raw ``Item``. Add-item construction lives in
``domain.prepare_new_item`` so the self-add
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
from datetime import date, datetime, timezone

from django.contrib.auth import get_user_model

from domain import (
    Forbidden,
    HomeBadges,
    ImportantDate as DomainImportantDate,
    Invalid,
    NotFound,
    OwnerItemView,
    ShopperItemView,
    UpcomingDate,
    can_rename_person,
    can_shop_pair,
    needs_reaction,
    prepare_claim,
    prepare_give,
    prepare_new_item,
    prepare_reaction,
    prepare_receive,
    prepare_unclaim,
    prepare_unreceive,
    soonest_upcoming,
    to_owner_view,
    to_shopper_view,
)
from domain.store import PERSON_NAME_MAX_LENGTH
from domain.models import Item as DomainItem

from gifts.models import Connection, ImportantDate, Item, Person


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
    "received_from_person_id",
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
        received_from_person_id=row.received_from_person_id,
        created_at=row.created_at,
    )


class DjangoStore:
    """Product mutations against SQLite. Partner always means Connection."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or _utc_now

    def partner_ids(self, person_id: int) -> list[int]:
        """Everyone connected to this person, via ``Connection`` only."""

        self._require_person(person_id)
        ids = set(
            Connection.objects.filter(person_a_id=person_id).values_list(
                "person_b_id", flat=True
            )
        )
        ids.update(
            Connection.objects.filter(person_b_id=person_id).values_list(
                "person_a_id", flat=True
            )
        )
        return sorted(ids)

    def partner_id(self, person_id: int) -> int:
        """The other person when there is exactly one connection."""

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

    def keep_person(self, actor_id: int, name: str, email: str = "") -> Person:
        """A private list for someone who is not on the app."""

        actor = self._require_person(actor_id)
        if actor.user_id is None:
            raise Forbidden()
        cleaned = name.strip()
        if not cleaned:
            raise Invalid("Name is required")
        if len(cleaned) > PERSON_NAME_MAX_LENGTH:
            raise Invalid()
        person = Person.objects.create(
            name=cleaned, email=email.strip(), user=None, kept_by=actor
        )
        low, high = (actor, person) if actor.id < person.id else (person, actor)
        Connection.objects.create(
            person_a=low, person_b=high, created_at=self._now()
        )
        return person

    def link_login(self, person_id: int, user_id: int) -> Person:
        """Attach an auth user. One-to-one, and an update of a nullable column."""

        person = self._require_person(person_id)
        if person.user_id == user_id:
            return person
        if person.user_id is not None or Person.objects.filter(user_id=user_id).exists():
            raise Invalid()
        person.user_id = user_id
        person.save(update_fields=["user"])
        return person

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
        """Change a display name. Does not read or write any gift.

        When this person has a login, ``User.first_name`` is updated too,
        so admin stays in step with the name the app shows. Gift rows,
        including ``received_from``, are left as recorded.
        """

        if not self.can_rename(actor_id, person_id):
            raise Forbidden()
        cleaned = name.strip()
        if not cleaned:
            raise Invalid("Name is required")
        if len(cleaned) > PERSON_NAME_MAX_LENGTH:
            raise Invalid("Name is too long")
        person = self._require_person(person_id)
        person.name = cleaned
        person.save(update_fields=["name"])
        if person.user_id is not None:
            get_user_model().objects.filter(pk=person.user_id).update(
                first_name=cleaned
            )
        return person

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
        row = Item.objects.create(**fields)
        item = domain_item(row)
        if actor_id == owner_id:
            return to_owner_view(item)
        return to_shopper_view(item)

    def react(self, actor_id: int, item_id: int, reaction: str) -> OwnerItemView:
        def rule(item):
            owner = self._require_person(item.owner_id)
            # Nobody reacts on a list whose owner has no login. Same
            # Forbidden whether or not the row is claimed.
            if owner.user_id is None:
                raise Forbidden()
            return prepare_reaction(item, actor_id, reaction, self._now())

        updated = self._mutate(item_id, rule)
        return to_owner_view(updated)

    def claim(self, actor_id: int, item_id: int) -> ShopperItemView:
        updated = self._mutate(
            item_id,
            lambda item: prepare_claim(
                item,
                actor_id,
                self.can_shop(actor_id, item.owner_id),
                self._now(),
            ),
        )
        return to_shopper_view(updated)

    def unclaim(self, actor_id: int, item_id: int) -> ShopperItemView:
        updated = self._mutate(
            item_id,
            lambda item: prepare_unclaim(
                item, actor_id, self.can_shop(actor_id, item.owner_id)
            ),
        )
        return to_shopper_view(updated)

    def give(self, actor_id: int, item_id: int) -> ShopperItemView:
        updated = self._mutate(
            item_id,
            lambda item: prepare_give(
                item,
                actor_id,
                self.can_shop(actor_id, item.owner_id),
                self._now(),
            ),
        )
        return to_shopper_view(updated)

    def receive(
        self,
        actor_id: int,
        item_id: int,
        received_from: str | None = None,
        received_from_person_id: int | None = None,
    ) -> OwnerItemView:
        """Owner got it from someone else. Same response, claimed or not.

        ``received_from_person_id`` links a person the actor can shop.
        Free text is not matched to anyone.
        """

        def rule(item):
            if item.owner_id == actor_id and received_from_person_id is not None:
                self._require_person(received_from_person_id)
                if not self.can_shop(actor_id, received_from_person_id):
                    raise Forbidden()
            return prepare_receive(
                item,
                actor_id,
                received_from,
                self._now(),
                received_from_person_id=received_from_person_id,
            )

        updated = self._mutate(item_id, rule)
        return to_owner_view(updated)

    def unreceive(self, actor_id: int, item_id: int) -> OwnerItemView:
        """Owner undoes a "got it elsewhere"."""

        updated = self._mutate(
            item_id, lambda item: prepare_unreceive(item, actor_id)
        )
        return to_owner_view(updated)

    def shopper_item(self, actor_id: int, item_id: int) -> ShopperItemView:
        """One item on a list this actor can shop. Anything else is ``NotFound``."""

        row = self._require_item_row(item_id)
        if not self.can_shop(actor_id, row.owner_id):
            raise NotFound()
        return to_shopper_view(domain_item(row))

    def owner_items(self, actor_id: int) -> list[OwnerItemView]:
        self._require_person(actor_id)
        rows = Item.objects.filter(owner_id=actor_id).order_by("created_at", "id")
        return [to_owner_view(domain_item(row)) for row in rows]

    def shopper_items(
        self, actor_id: int, owner_id: int | None = None
    ) -> list[ShopperItemView]:
        """A list this actor can shop. Omit ``owner_id`` when there is exactly one."""

        if owner_id is None:
            owner_id = self.partner_id(actor_id)
        elif not self.can_shop(actor_id, owner_id):
            raise NotFound()
        rows = Item.objects.filter(owner_id=owner_id).order_by("created_at", "id")
        return [to_shopper_view(domain_item(row)) for row in rows]

    def needs_reaction_count(self, owner_id: int) -> int:
        """Home badge: "N need you" — pending suggestions on *my* list.

        The predicate is ``needs_reaction``, which does not read claim
        columns. The return value is the count alone.
        """

        self._require_person(owner_id)
        return sum(
            1
            for row in Item.objects.filter(owner_id=owner_id)
            if needs_reaction(domain_item(row))
        )

    def next_partner_important_date(
        self, viewer_id: int, on: date
    ) -> UpcomingDate | None:
        """Soonest upcoming date among people this viewer is connected to."""

        partners = self.partner_ids(viewer_id)
        if not partners:
            raise NotFound()
        return soonest_upcoming(self._date_records(partners), on)

    def next_important_date(self, person_id: int, on: date) -> UpcomingDate | None:
        """Soonest upcoming date for one person. The caller already chose them."""

        self._require_person(person_id)
        return soonest_upcoming(self._date_records([person_id]), on)

    def home_badges(self, viewer_id: int, on: date) -> HomeBadges:
        """Owner Home payload. No connection means no date badge."""

        upcoming = None
        if self.partner_ids(viewer_id):
            upcoming = self.next_partner_important_date(viewer_id, on)
        return HomeBadges(
            needs_you=self.needs_reaction_count(viewer_id),
            upcoming=upcoming,
        )

    def _date_records(self, person_ids: list[int]) -> list[DomainImportantDate]:
        return [
            DomainImportantDate(
                person_id=row.person_id,
                label=row.label,
                month=row.month,
                day=row.day,
                year=row.year,
            )
            for row in ImportantDate.objects.filter(person_id__in=person_ids).order_by(
                "id"
            )
        ]

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

    def _is_partner(self, person_id: int, other_id: int) -> bool:
        if person_id == other_id:
            return False
        a, b = (
            (person_id, other_id) if person_id < other_id else (other_id, person_id)
        )
        return Connection.objects.filter(person_a_id=a, person_b_id=b).exists()
