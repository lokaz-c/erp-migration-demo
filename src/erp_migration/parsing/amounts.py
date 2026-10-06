"""Amounts, quantities and currencies as clerks typed them.

Handles numeric cells and text such as "125,000", "125 000", "125,000/="
(East African shorthand for a whole amount), "RWF 125,000", "125,000 Frw",
"$1,250.50", "US$ 1,250.50", "(1,250)" and "1.250.000".

Separator rule: if both "," and "." appear, the right-most one is the decimal
point. A lone "," is a thousands separator when every group after it has
exactly three digits, and a decimal comma when one or two digits follow it.
A lone "." is a decimal point unless it repeats in groups of three.

Currency rule for a row: an explicit currency cell wins; otherwise a marker
embedded in the amount text; otherwise the column header ("Amount (Frw)").
A currency cell that contradicts an embedded marker is CURRENCY_CONFLICT, and
a row with no currency information at all is UNKNOWN_CURRENCY.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

CURRENCY_WORDS = {
    "rwf": "RWF",
    "frw": "RWF",
    "rf": "RWF",
    "frs": "RWF",
    "francs": "RWF",
    "usd": "USD",
    "us$": "USD",
    "$": "USD",
    "dollars": "USD",
}
_TOKEN = r"US\$|USD|RWF|FRW|RF|\$"
_MONEY = re.compile(
    rf"^(?P<pre>{_TOKEN})?\s*(?P<num>[(\-]?[\d][\d.,\s]*\)?)\s*(?P<post>{_TOKEN})?$",
    re.IGNORECASE,
)
_UNIT_SUFFIX = re.compile(r"\s*(m|mtrs?|meters?|metres?|pcs?|pieces?|units?|rolls?)\.?$", re.I)


class ParseError(ValueError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str | None  # currency marker found inside the text, if any


def normalize_currency(value: Any) -> str | None:
    """'Frw', 'FRW', 'RF' -> 'RWF'; 'US$', '$', 'usd' -> 'USD'; blank -> None."""
    if value is None or not str(value).strip():
        return None
    key = str(value).strip().lower()
    if key not in CURRENCY_WORDS:
        raise ParseError("UNKNOWN_CURRENCY", f"unrecognised currency {value!r}")
    return CURRENCY_WORDS[key]


def to_decimal(text: str) -> Decimal:
    s = text.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith("-"):
        negative, s = True, s[1:].strip()
    s = re.sub(r"(?<=\d)[\s ](?=\d)", "", s)
    if not re.fullmatch(r"\d[\d.,]*", s):
        raise ParseError("BAD_AMOUNT", f"not a number: {text!r}")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            whole, frac = s.rsplit(",", 1)
            whole = whole.replace(".", "")
        else:
            whole, frac = s.rsplit(".", 1)
            whole = whole.replace(",", "")
        s = f"{whole}.{frac}"
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        elif re.fullmatch(r"\d+,\d{1,2}", s):
            s = s.replace(",", ".")
        else:
            raise ParseError("BAD_AMOUNT", f"unclear separators: {text!r}")
    elif s.count(".") > 1:
        if not re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            raise ParseError("BAD_AMOUNT", f"unclear separators: {text!r}")
        s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation as exc:  # pragma: no cover - regex above guards this
        raise ParseError("BAD_AMOUNT", text) from exc
    return -value if negative else value


def parse_money(value: Any) -> Money:
    """Parse one amount cell. Raises ParseError(MISSING_FIELD | BAD_AMOUNT | CURRENCY_CONFLICT)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ParseError("MISSING_FIELD", "amount is blank")
    if isinstance(value, bool):
        raise ParseError("BAD_AMOUNT", repr(value))
    if isinstance(value, int | float | Decimal):
        return Money(Decimal(str(value)), None)
    text = str(value).strip().replace(" ", " ")
    text = re.sub(r"\s*/[=\-]$", "", text)  # 125,000/=
    m = _MONEY.match(text)
    if not m:
        raise ParseError("BAD_AMOUNT", f"not an amount: {value!r}")
    pre = CURRENCY_WORDS[m["pre"].lower()] if m["pre"] else None
    post = CURRENCY_WORDS[m["post"].lower()] if m["post"] else None
    if pre and post and pre != post:
        raise ParseError("CURRENCY_CONFLICT", f"two currencies in {value!r}")
    return Money(to_decimal(m["num"]), pre or post)


def parse_quantity(value: Any) -> Decimal:
    """'120 m', '1,200 pcs', 85, 170.5 -> Decimal. Sign is preserved."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ParseError("MISSING_FIELD", "quantity is blank")
    if isinstance(value, bool):
        raise ParseError("BAD_QUANTITY", repr(value))
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = _UNIT_SUFFIX.sub("", str(value).strip())
    try:
        return to_decimal(text)
    except ParseError as exc:
        raise ParseError("BAD_QUANTITY", f"not a quantity: {value!r}") from exc


def resolve_currency(
    cell: Any, markers: list[str | None], header_hint: str | None
) -> tuple[str, str]:
    """Pick the row's currency and say where it came from (column | embedded | header)."""
    found = {m for m in markers if m}
    if len(found) > 1:
        raise ParseError("CURRENCY_CONFLICT", f"amount and price disagree: {sorted(found)}")
    column = normalize_currency(cell)
    if column and found and column not in found:
        raise ParseError("CURRENCY_CONFLICT", f"column says {column}, amount says {found.pop()}")
    if column:
        return column, "column"
    if found:
        return found.pop(), "embedded"
    if header_hint:
        return header_hint, "header"
    raise ParseError("UNKNOWN_CURRENCY", "no currency in the row, the amount or the header")
