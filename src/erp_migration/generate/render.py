"""Render true records as the cells a clerk would have typed.

`Renderer` knows one workbook's style and turns dates, money, codes and names
into messy cell values. The row builders below add the deliberate errors that
the pipeline is expected to reject, and record what the truth is for each row.
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from erp_migration.generate.styles import (
    CHANNEL_LABELS,
    CURRENCY_LABELS,
    CURRENCY_TOKENS,
    MOVEMENT_LABELS,
    BookStyle,
)

EXCEL_EPOCH = date(1899, 12, 30)
BAD_AMOUNTS = ["TBC", "n/a", "#VALUE!", "see invoice"]
BAD_DATES = ["31/02/{y}", "{y}-13-04", "??/03/{y}", "30/02/{y}"]


@dataclass
class Row:
    """One spreadsheet row plus what the truth says about it."""

    kind: str  # data | blank | subtotal | repeated_header
    values: dict[str, Any] = field(default_factory=dict)
    formats: dict[str, str] = field(default_factory=dict)
    record_id: str | None = None
    reason: str | None = None  # reject reason the pipeline should report
    signature: str | None = None  # true values; differs on a conflicting copy
    truth: dict[str, Any] = field(default_factory=dict)
    sort_key: tuple = ()
    month: tuple[int, int] | None = None  # for monthly subtotals and sheet splits
    bucket: str | None = None  # inventory: raw materials vs finished goods


class Renderer:
    def __init__(self, rng: random.Random, style: BookStyle) -> None:
        self.rng = rng
        self.style = style

    # --- dates -----------------------------------------------------------
    def date(self, d: date) -> tuple[Any, str | None]:
        style = self.style.date
        if style == "mixed_clerks":
            # A temporary clerk keyed September and October month-first.
            style = "mdy" if d.month in (9, 10) else "dmy"
        if style == "excel_date_strays":
            style = "iso" if self.rng.random() < 0.05 else "excel_date"
        if style == "excel_date":
            fmt = self.rng.choice(["DD/MM/YYYY", "D-MMM-YY", "DD/MM/YYYY"])
            return datetime(d.year, d.month, d.day), fmt
        if style == "serial":
            return (d - EXCEL_EPOCH).days, "General"
        if style == "iso":
            sep = "/" if self.rng.random() < 0.1 else "-"
            return f"{d.year}{sep}{d.month:02d}{sep}{d.day:02d}", None
        if style == "text_month":
            fmt = self.rng.choice(
                ["{d} {b} {Y}", "{dd}-{b}-{y}", "{B} {d}, {Y}", "{d}-{B}-{Y}", "{dd} {b} {Y}"]
            )
            return fmt.format(
                d=d.day,
                dd=f"{d.day:02d}",
                b=calendar.month_abbr[d.month],
                B=calendar.month_name[d.month],
                Y=d.year,
                y=f"{d.year % 100:02d}",
            ), None
        if style == "dmy_dash":
            sep = self.rng.choice(["-", "."])
            return f"{d.day:02d}{sep}{d.month:02d}{sep}{d.year}", None
        first, second = (d.day, d.month) if style == "dmy" else (d.month, d.day)
        roll = self.rng.random()
        if roll < 0.7:
            return f"{first:02d}/{second:02d}/{d.year}", None
        if roll < 0.95:
            return f"{first}/{second}/{d.year}", None
        return f"{first:02d}/{second:02d}/{d.year % 100:02d}", None

    # --- money -----------------------------------------------------------
    def _number_text(self, value: Decimal, currency: str) -> str:
        text = f"{value:,.2f}" if currency == "USD" else f"{int(value):,}"
        if self.style.amount == "spaces":
            text = text.replace(",", " ")
        if self.style.amount == "slash_equals" and currency == "RWF":
            text += "/="
        return text

    def money(self, value: Decimal, currency: str, token: str | None) -> Any:
        if token:
            return token.format(x=self._number_text(value, currency).removesuffix("/="))
        if self.style.amount == "number":
            return float(value) if currency == "USD" else int(value)
        return self._number_text(value, currency)

    def currency_plan(self, currency: str) -> tuple[str | None, str | None, str | None]:
        """Return (currency cell, embedded token template, injected reason)."""
        mode = self.style.currency
        label = self.rng.choice(CURRENCY_LABELS[currency])
        token = self.rng.choice(CURRENCY_TOKENS[currency])
        if mode == "column":
            return label, None, None
        if mode == "column_partial":
            if self.rng.random() < 0.08:
                if self.rng.random() < 0.5:
                    return None, token, None
                return None, None, "UNKNOWN_CURRENCY"
            return label, None, None
        if mode == "header":  # header says Frw; foreign amounts carry their own marker
            return None, (token if currency != "RWF" else None), None
        return None, token, None  # embedded

    # --- codes and text ----------------------------------------------------
    def sku(self, sku: str) -> str:
        if self.style.codes == "clean" or self.rng.random() < 0.5:
            return sku
        a, b, n = sku.split("-")
        return self.rng.choice(
            [
                sku.lower(),
                f"{a} {b} {n}",
                f"{a}{b}{n}",
                f"{a}-{b}-{int(n)}",
                f"{a}/{b}/{n}",
                f" {sku} ",
            ]
        )

    def doc(self, number: str) -> str:
        if self.style.codes == "clean" or self.rng.random() < 0.5:
            return number
        prefix, year, n = number.split("-")
        return self.rng.choice(
            [
                f"{prefix.lower()} {year}/{int(n)}",
                f"{prefix}{year}-{n}",
                f"{prefix}/{year}/{n}",
                f"{prefix.title()}-{year}-{int(n)}",
            ]
        )

    def employee_code(self, code: str) -> str | None:
        n = int(code[1:])
        if self.style.codes == "clean" or self.rng.random() < 0.5:
            return code
        return self.rng.choice([f"e{n}", f"EMP-{n:03d}", f"E-{n:03d}", str(n)])

    def text(self, value: str) -> str:
        if self.style.case == "upper":
            return value.upper()
        return value

    def description(self, value: str) -> str:
        value = self.text(value)
        roll = self.rng.random()
        if roll < 0.15:
            return value.replace(",", "")
        if roll < 0.25:
            return value.replace(", ", " - ")
        if roll < 0.3:
            return value.lower()
        return value

    def quantity(self, qty: Decimal, uom: str) -> Any:
        number = int(qty) if qty == qty.to_integral_value() else float(qty)
        if self.style.qty_text and self.rng.random() < 0.6:
            unit = {"m": "m", "pc": "pcs", "job": ""}.get(uom, "")
            return f"{number:,} {unit}".strip()
        return number

    def channel(self, channel: str) -> str:
        return self.rng.choice(CHANNEL_LABELS[channel])

    def movement(self, kind: str, reference: str) -> str:
        if kind == "adjustment" and reference == "Opening balance":
            return "Opening balance"
        return self.rng.choice(MOVEMENT_LABELS[kind])

    def name(self, given: str, surname: str) -> str:
        if self.style.names == "SURNAME_given":
            return f"{surname.upper()} {given}"
        if self.style.names == "surname_comma":
            return f"{surname}, {given}"
        return f"{given} {surname}"

    def bad_date(self, d: date) -> str:
        return self.rng.choice(BAD_DATES).format(y=d.year)

    def out_of_range_date(self, d: date) -> tuple[Any, str | None]:
        # A year typo: 2019 keyed as 2091.
        typo_year = int(str(d.year)[:2] + str(d.year)[3] + str(d.year)[2])
        if typo_year == d.year:
            typo_year = d.year + 70
        day = min(d.day, calendar.monthrange(typo_year, d.month)[1])
        return self.date(date(typo_year, d.month, day))
