"""Domain refusals.

Three kinds, on purpose:

* ``NotFound`` — this actor cannot see that row (or it does not exist).
* ``Forbidden`` — this actor is the wrong person for the action
  (not the owner, not the partner, not the claimer).
* ``Invalid`` — the action is refused without saying why.

Error *messages* are part of the secrecy rule. The owner must never learn
that an item was claimed from ``str(error)``, from the exception class
choice leaking a special "claimed" type, or from extra attributes on the
exception (no ``error.item``, no ``error.claimed_by_id``).

When a refusal exists to hide claim state, use generic ``Invalid()`` —
the same object you would raise for any other invalid delete. Do not
write "already claimed" even in a comment that could be copied into a
message later.
"""


class DomainError(Exception):
    """Base type for every domain refusal.

    Catch this in later web code to turn domain failures into HTTP
    responses without leaking internals.
    """


class NotFound(DomainError):
    """The row is missing, *or* this actor is not allowed to see it.

    Using the same error for "does not exist" and "exists but not yours"
    avoids an existence oracle: a caller cannot probe ids they should
    not know about.
    """

    def __init__(self) -> None:
        super().__init__("Not found")


class Forbidden(DomainError):
    """Wrong actor for this action.

    Safe to use when the product already admits the action exists
    (only the owner reacts, only the partner claims). Still never
    mention claim/given state in the message.
    """

    def __init__(self) -> None:
        super().__init__("Not allowed")


class Invalid(DomainError):
    """Generic validation failure.

    Default message is ``"Invalid"``. Pass a specific message only when
    it cannot leak secrets (e.g. ``"Name is required"``).

    React-when-claimed and delete-when-claimed both raise ``Invalid()``
    with that default, so the owner cannot tell those cases apart from
    other invalid updates.
    """

    def __init__(self, message: str = "Invalid") -> None:
        super().__init__(message)
