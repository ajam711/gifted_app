"""Display formatting for a gift price.

``price_text`` stays free text in the database (``"around $120"`` is
valid). Screens share :func:`format_dollars` so a plain amount is shown
as US dollars (``$24.99``) without a second copy of the formatting rules.
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_CENTS = Decimal("0.01")

# A single amount: optional minus, optional leading "$", digits with
# US thousands separators, optional cents, optional trailing "$".
_AMOUNT = re.compile(
    r"""
    (?P<sign>-)?\s*
    \$?\s*
    (?P<sign2>-)?\s*
    (?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)
    \s*\$?
    """,
    re.VERBOSE,
)

_NUMBER = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+")


def format_dollars(value: object) -> str:
    """Format a stored price for display as US dollars.

    A single amount (``24.99``, ``$24.9``, ``1,200``, ``-$3.5``) becomes
    ``$24.99``, ``$1,200.00``, or ``-$3.50``: a leading ``$``, thousands
    separators, and two decimal places. Free text that already contains
    ``$`` is left as written (``around $120``). Any other note gains a
    ``$`` before its first number, or at the front when it has none, so
    a price never shows as a unitless number.
    """

    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    amount = _parse_amount(text)
    if amount is not None:
        return _format_amount(amount)
    if "$" in text:
        return text
    match = _NUMBER.search(text)
    if match is None:
        return f"${text}"
    return f"{text[:match.start()]}${text[match.start():]}"


def _parse_amount(text: str) -> Decimal | None:
    match = _AMOUNT.fullmatch(text)
    if match is None or (match.group("sign") and match.group("sign2")):
        return None
    number = match.group("number").replace(",", "")
    try:
        amount = Decimal(number)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    if match.group("sign") or match.group("sign2"):
        return -amount
    return amount


def _format_amount(amount: Decimal) -> str:
    quantized = amount.quantize(_CENTS, rounding=ROUND_HALF_UP)
    # ``-0.00`` is not ``< 0``, but formatting it directly still prints the sign.
    sign = "-" if quantized < 0 else ""
    return f"{sign}${abs(quantized):,.2f}"
