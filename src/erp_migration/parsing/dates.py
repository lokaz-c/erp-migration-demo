"""Date parsing, including the ambiguous day/month case.

Values arrive as real Excel dates, Excel serial numbers, ISO strings, text
months ("5 Mar 2019") or slashed numbers ("05/03/2019"). Slashed numbers are
the hard part: "05/03/2019" is 5 March to a day-first clerk and 3 May to a
month-first clerk.

The rule, applied per date column:

1. A value is unambiguous if it is a real date, a serial, ISO, a text month,
   or a slashed date where one part is greater than 12 (or both parts are
   equal). Unambiguous slashed values are *evidence* for a convention:
   "25/03/2019" is day-first evidence, "03/25/2019" is month-first evidence.
2. An ambiguous value (both parts 12 or less, and different) takes the
   convention of its sheet when that sheet's evidence is one-sided.
3. If the sheet has no evidence, the convention of the same column across
   the whole workbook is used, again only if it is one-sided.
4. If the evidence is mixed (two clerks), the value is resolved from its
   neighbours: books are kept in date order, so the candidate that falls
   between the nearest unambiguous dates above and below it (with 7 days of
   slack) wins, provided exactly one candidate does.
5. Anything still undecided is rejected as AMBIGUOUS_DATE for a person to
   review. The pipeline never guesses silently.

Every resolved date must also fall inside the book's period (its year plus
62 days either side); otherwise it is rejected as DATE_OUT_OF_RANGE.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

EXCEL_EPOCH = date(1899, 12, 30)
SERIAL_MIN = (date(1990, 1, 1) - EXCEL_EPOCH).days
SERIAL_MAX = (date(2099, 12, 31) - EXCEL_EPOCH).days
NEIGHBOUR_WINDOW = 15  # rows to look up and down for unambiguous neighbours
NEIGHBOUR_SLACK = timedelta(days=7)

_SLASHED = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})$")
_ISO = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_TEXT_FORMATS = [
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %y",
]

DAY_FIRST, MONTH_FIRST, MIXED, NO_EVIDENCE = "DMY", "MDY", "MIXED", "NONE"


@dataclass(frozen=True)
class DateToken:
    """A single cell after the first pass. Exactly one of the fields describes it."""

    value: date | None = None  # unambiguous date
    method: str = ""  # how `value` was read, or "ambiguous" / "missing" / "invalid"
    day_first: date | None = None  # the two readings of an ambiguous value
    month_first: date | None = None


@dataclass(frozen=True)
class ParsedDate:
    value: date | None
    method: str  # excel_date | excel_serial | iso | text_month | day_first | month_first |
    #              same_day_month | sheet_convention | book_convention | neighbours
    reason: str | None = None  # reject reason when value is None


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __contains__(self, d: date) -> bool:
        return self.start <= d <= self.end

    @classmethod
    def for_year(cls, year: int | None) -> DateRange:
        if year is None:
            return cls(date(2000, 1, 1), date(2030, 12, 31))
        slack = timedelta(days=62)
        return cls(date(year, 1, 1) - slack, date(year, 12, 31) + slack)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _expand_year(y: str) -> int:
    n = int(y)
    if len(y) == 4:
        return n
    return 2000 + n if n < 70 else 1900 + n


def tokenize(value: Any) -> DateToken:
    """First pass: classify one cell without any context."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DateToken(method="missing")
    if isinstance(value, datetime):
        return DateToken(value=value.date(), method="excel_date")
    if isinstance(value, date):
        return DateToken(value=value, method="excel_date")
    if isinstance(value, bool):
        return DateToken(method="invalid")
    if isinstance(value, int | float):
        return _from_serial(value)
    text = str(value).strip()
    if text.isdigit():
        return _from_serial(int(text))
    if m := _ISO.match(text):
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        return DateToken(value=d, method="iso") if d else DateToken(method="invalid")
    if m := _SLASHED.match(text):
        a, b, y = int(m[1]), int(m[2]), _expand_year(m[3])
        if a == b:
            d = _safe_date(y, a, b)
            return DateToken(value=d, method="same_day_month") if d else DateToken(method="invalid")
        if a > 12 and b <= 12:
            d = _safe_date(y, b, a)
            return DateToken(value=d, method="day_first") if d else DateToken(method="invalid")
        if b > 12 and a <= 12:
            d = _safe_date(y, a, b)
            return DateToken(value=d, method="month_first") if d else DateToken(method="invalid")
        if a > 12 and b > 12:
            return DateToken(method="invalid")
        dmy, mdy = _safe_date(y, b, a), _safe_date(y, a, b)
        if dmy is None or mdy is None:
            return DateToken(method="invalid")
        return DateToken(method="ambiguous", day_first=dmy, month_first=mdy)
    for fmt in _TEXT_FORMATS:
        try:
            return DateToken(value=datetime.strptime(text, fmt).date(), method="text_month")
        except ValueError:
            continue
    return DateToken(method="invalid")


def _from_serial(n: float) -> DateToken:
    if SERIAL_MIN <= n <= SERIAL_MAX:
        return DateToken(value=EXCEL_EPOCH + timedelta(days=int(n)), method="excel_serial")
    return DateToken(method="invalid")


def convention(tokens: Sequence[DateToken]) -> str:
    """Which way round this set of values was typed, judged only from evidence."""
    dmy = sum(t.method == "day_first" for t in tokens)
    mdy = sum(t.method == "month_first" for t in tokens)
    if dmy and mdy:
        return MIXED
    if dmy:
        return DAY_FIRST
    if mdy:
        return MONTH_FIRST
    return NO_EVIDENCE


def _from_neighbours(tokens: Sequence[DateToken], i: int, valid: DateRange) -> date | None:
    def nearest(indices: range) -> date | None:
        for j in indices:
            t = tokens[j]
            if t.value is not None and t.value in valid:
                return t.value
        return None

    before = nearest(range(i - 1, max(-1, i - 1 - NEIGHBOUR_WINDOW), -1))
    after = nearest(range(i + 1, min(len(tokens), i + 1 + NEIGHBOUR_WINDOW)))
    anchors = [d for d in (before, after) if d is not None]
    if not anchors:
        return None
    lo, hi = min(anchors) - NEIGHBOUR_SLACK, max(anchors) + NEIGHBOUR_SLACK
    fits = [c for c in (tokens[i].day_first, tokens[i].month_first) if lo <= c <= hi]
    return fits[0] if len(fits) == 1 else None


def resolve_column(
    tokens: Sequence[DateToken], valid: DateRange, book_convention: str = NO_EVIDENCE
) -> list[ParsedDate]:
    """Second pass: resolve every token of one sheet column, in row order."""
    sheet = convention(tokens)
    decided = sheet if sheet in (DAY_FIRST, MONTH_FIRST) else None
    decided_by = "sheet_convention"
    if decided is None and sheet == NO_EVIDENCE and book_convention in (DAY_FIRST, MONTH_FIRST):
        decided, decided_by = book_convention, "book_convention"

    out: list[ParsedDate] = []
    for i, t in enumerate(tokens):
        if t.method == "missing":
            out.append(ParsedDate(None, t.method, "MISSING_FIELD"))
        elif t.method == "invalid":
            out.append(ParsedDate(None, t.method, "BAD_DATE"))
        elif t.value is not None:
            ok = t.value in valid
            out.append(
                ParsedDate(t.value if ok else None, t.method, None if ok else "DATE_OUT_OF_RANGE")
            )
        else:
            if t.day_first not in valid and t.month_first not in valid:
                out.append(ParsedDate(None, "ambiguous", "DATE_OUT_OF_RANGE"))
            elif decided == DAY_FIRST:
                out.append(ParsedDate(t.day_first, decided_by))
            elif decided == MONTH_FIRST:
                out.append(ParsedDate(t.month_first, decided_by))
            elif (d := _from_neighbours(tokens, i, valid)) is not None:
                out.append(ParsedDate(d, "neighbours"))
            else:
                out.append(ParsedDate(None, "ambiguous", "AMBIGUOUS_DATE"))
    return out


def parse_period(text: str, default_year: int | None) -> date | None:
    """Payroll sheet names: 'Jan 2019', 'JAN-19', '2019-01', 'January', '01.2019', 'Jan19'."""
    s = re.sub(r"\s*\(\d+\)\s*$", "", text.strip())  # "Dec 2018 (2)" -> "Dec 2018"
    if m := re.fullmatch(r"(\d{4})[-./](\d{1,2})", s):
        return _safe_date(int(m[1]), int(m[2]), 1)
    if m := re.fullmatch(r"(\d{1,2})[-./](\d{4})", s):
        return _safe_date(int(m[2]), int(m[1]), 1)
    if m := re.fullmatch(r"([A-Za-z]+)[\s\-]*(\d{2}|\d{4})?", s):
        month = _month_number(m[1])
        if month is None:
            return None
        year = _expand_year(m[2]) if m[2] else default_year
        return _safe_date(year, month, 1) if year else None
    return None


def _month_number(name: str) -> int | None:
    """'Mar', 'MARCH', 'Sept' -> month number; anything that isn't a month prefix -> None."""
    key = name.lower()
    if len(key) < 3:
        return None
    for i in range(1, 13):
        if calendar.month_name[i].lower().startswith(key):
            return i
    return None
