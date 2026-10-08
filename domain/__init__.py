"""Gift-app domain layer: rules first, Django later.

Read in this order:

1. ``models.py`` — the four tables and what is *not* a table
2. ``views.py`` — owner vs shopper projections, and derived states
3. ``errors.py`` — refusals that must not leak claim/given data to the owner
4. ``store.py`` — mutations that enforce the rules
5. ``tests/`` — each file names the invariant it is guarding

Import from this package (``from domain import InMemoryStore``) rather than
reaching into submodules from application code later.
"""

from domain.errors import (
    Conflict,
    ConflictReason,
    DomainError,
    Forbidden,
    Invalid,
    NotFound,
)
from domain.models import Connection, ImportantDate, Item, Person, Reaction
from domain.store import (
    InMemoryStore,
    prepare_claim,
    prepare_give,
    prepare_new_item,
    prepare_reaction,
    prepare_receive,
    prepare_unclaim,
    prepare_unreceive,
)
from domain.views import (
    OwnerItemView,
    ShopperItemView,
    UpcomingDate,
    claimed,
    disliked,
    given,
    liked_open,
    needs_reaction,
    received,
    to_owner_view,
    to_shopper_view,
)

# Names that `from domain import *` will export. Also a map of the public API.
__all__ = [
    "Conflict",
    "ConflictReason",
    "Connection",
    "DomainError",
    "Forbidden",
    "ImportantDate",
    "InMemoryStore",
    "Invalid",
    "Item",
    "NotFound",
    "OwnerItemView",
    "Person",
    "Reaction",
    "prepare_claim",
    "prepare_give",
    "prepare_new_item",
    "prepare_reaction",
    "prepare_receive",
    "prepare_unclaim",
    "prepare_unreceive",
    "ShopperItemView",
    "UpcomingDate",
    "claimed",
    "disliked",
    "given",
    "liked_open",
    "needs_reaction",
    "received",
    "to_owner_view",
    "to_shopper_view",
]
