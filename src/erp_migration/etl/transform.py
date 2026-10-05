"""Turn extracted rows into typed staging rows or rejects (the Python half of the ETL).

Parsing lives in Python because it is row-local and easiest to test there:
dates, amounts, currencies, codes. Anything that needs the whole data set
(de-duplication, product and employee lookups, reconciliation) happens in SQL.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from erp_migration.etl.extract import SheetData
from erp_migration.parsing.amounts import (
    ParseError,
    parse_money,
    parse_quantity,
    resolve_currency,
)
from erp_migration.parsing.dates import (
    DateRange,
    ParsedDate,
    convention,
    parse_period,
    resolve_column,
    tokenize,
)
from erp_migration.parsing.text import (
    clean,
    name_key,
    normalize_channel,
    normalize_doc,
    normalize_employee_code,
    normalize_movement_type,
    normalize_po_reference,
    normalize_sku,
)

NOISE = {"blank": "BLANK_ROW", "subtotal": "SUBTOTAL_ROW", "repeated_header": "REPEATED_HEADER"}
PRICE_PLACES = Decimal("0.0001")
CATEGORIES = {
    "fabric": "fabric",
    "trims": "trims",
    "packaging": "packaging",
    "services": "services",
    "finished goods": "finished",
    "finished": "finished",
}
UNITS = {"m": "m", "pc": "pc", "pcs": "pc", "job": "job"}


@dataclass
class Transformed:
    sheets: list[SheetData]
    products: list[tuple] = field(default_factory=list)
    purchases: list[tuple] = field(default_factory=list)
    sales: list[tuple] = field(default_factory=list)
    movements: list[tuple] = field(default_factory=list)
    payroll: list[tuple] = field(default_factory=list)
    rejects: list[tuple] = field(default_factory=list)  # (book, sheet_index, row_num, code, detail)
    supplier_rows: Counter[str] = field(default_factory=Counter)  # spelling -> parsed rows


def description_key(value: Any) -> str | None:
    """Same expression as core.products.description_key, so the join in SQL is exact."""
    text = clean(value)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() or None


def _require(value: Any, what: str) -> str:
    text = clean(value)
    if not text:
        raise ParseError("MISSING_FIELD", f"{what} is blank")
    return text


def _doc(value: Any, domain: str, what: str) -> str:
    text = _require(value, what)
    number = normalize_doc(text, domain)
    if number is None:
        raise ParseError("BAD_CODE", f"{what} {text!r} cannot be read")
    return number


def _product(cells: dict[str, Any]) -> tuple[str | None, str | None]:
    """Product reference: a code when the book has one, else the description."""
    code = clean(cells.get("sku"))
    if code:
        sku = normalize_sku(code)
        if sku is None:
            raise ParseError("BAD_CODE", f"product code {code!r} cannot be read")
        return sku, None
    key = description_key(cells.get("description"))
    if key is None:
        raise ParseError("MISSING_FIELD", "no product code or description")
    return None, key


def _date(parsed: ParsedDate, raw: Any) -> ParsedDate:
    if parsed.reason:
        raise ParseError(parsed.reason, f"date {clean(raw)!r}")
    return parsed


def _money_line(
    cells: dict[str, Any], hints: dict[str, str]
) -> tuple[Decimal, Decimal, Decimal, str, str]:
    """Quantity, unit price, amount, currency and currency source for a priced line."""
    qty = parse_quantity(cells.get("quantity"))
    if qty <= 0:
        raise ParseError("BAD_QUANTITY", f"quantity {qty}")
    amount = parse_money(cells.get("amount"))
    if amount.amount <= 0:
        raise ParseError("BAD_AMOUNT", f"amount {amount.amount}")
    price = None
    if clean(cells.get("unit_price")):
        price = parse_money(cells.get("unit_price"))
    markers = [amount.currency, price.currency if price else None]
    hint = hints.get("amount") or hints.get("unit_price")
    currency, source = resolve_currency(cells.get("currency"), markers, hint)
    if price is None:
        unit_price = (amount.amount / qty).quantize(PRICE_PLACES)
    else:
        unit_price = price.amount
        expected = qty * unit_price
        if abs(amount.amount - expected) > max(Decimal("0.01"), Decimal("0.001") * amount.amount):
            raise ParseError(
                "AMOUNT_MISMATCH", f"{qty} x {unit_price} = {expected}, book says {amount.amount}"
            )
    return qty, unit_price, amount.amount, currency, source


def parse_purchase(sheet: SheetData, cells: dict[str, Any], d: ParsedDate) -> tuple:
    po = _doc(cells.get("po_number"), "purchases", "PO number")
    supplier = _require(cells.get("supplier"), "supplier")
    sku, desc = _product(cells)
    d = _date(d, cells.get("date"))
    qty, price, amount, currency, source = _money_line(cells, sheet.layout.hints)
    return (po, supplier, d.value, d.method, sku, desc, qty, price, amount, currency, source)


def parse_sale(sheet: SheetData, cells: dict[str, Any], d: ParsedDate) -> tuple:
    receipt = _doc(cells.get("receipt_no"), "sales", "receipt number")
    raw_channel = _require(cells.get("channel"), "channel")
    channel = normalize_channel(raw_channel)
    if channel is None:
        raise ParseError("UNKNOWN_CHANNEL", f"channel {raw_channel!r}")
    sku, desc = _product(cells)
    d = _date(d, cells.get("date"))
    qty, price, amount, currency, source = _money_line(cells, sheet.layout.hints)
    return (receipt, d.value, d.method, channel, sku, desc, qty, price, amount, currency, source)


def parse_movement(sheet: SheetData, cells: dict[str, Any], d: ParsedDate) -> tuple:
    voucher = _doc(cells.get("voucher_no"), "inventory", "voucher number")
    sku, desc = _product(cells)
    raw_type = clean(cells.get("movement_type"))
    kind = normalize_movement_type(raw_type)
    if kind is None:
        raise ParseError("UNKNOWN_MOVEMENT_TYPE", f"movement type {raw_type!r}")
    d = _date(d, cells.get("date"))
    if "qty_in" in sheet.layout.columns.values():
        q_in = parse_quantity(cells["qty_in"]) if clean(cells.get("qty_in")) else None
        q_out = parse_quantity(cells["qty_out"]) if clean(cells.get("qty_out")) else None
        if q_in and q_out:
            raise ParseError("BAD_QUANTITY", f"both in ({q_in}) and out ({q_out}) filled")
        if not q_in and not q_out:
            raise ParseError("BAD_QUANTITY", "neither in nor out filled")
        if (q_in or 0) < 0 or (q_out or 0) < 0:
            raise ParseError("BAD_QUANTITY", "negative quantity in an in/out column")
        qty = q_in if q_in else -q_out
    else:
        qty = parse_quantity(cells.get("quantity"))
    if qty == 0:
        raise ParseError("BAD_QUANTITY", "quantity is zero")
    # The movement type decides the sign; clerks often left the minus off.
    if kind in ("receipt", "production"):
        qty = abs(qty)
    elif kind in ("issue", "sale"):
        qty = -abs(qty)
    reference = clean(cells.get("reference")) or None
    po = normalize_po_reference(reference) if kind == "receipt" and reference else None
    return (voucher, d.value, d.method, sku, desc, kind, qty, reference, po)


def parse_payroll(sheet: SheetData, cells: dict[str, Any], period) -> tuple:
    if period is None:
        raise ParseError("BAD_PERIOD", f"sheet name {sheet.sheet!r}")
    raw_code = clean(cells.get("employee_code"))
    code = normalize_employee_code(raw_code) if raw_code else None
    if raw_code and code is None:
        raise ParseError("BAD_CODE", f"employee number {raw_code!r}")
    name = clean(cells.get("name"))
    if not name:
        raise ParseError("MISSING_FIELD", "employee name is blank")
    amounts = {}
    for f in ("gross", "deductions", "net"):
        money = parse_money(cells.get(f))
        if money.currency not in (None, "RWF"):
            raise ParseError("CURRENCY_CONFLICT", f"{f} is in {money.currency}; payroll is in RWF")
        amounts[f] = money.amount
    if amounts["gross"] <= 0 or amounts["deductions"] < 0:
        raise ParseError(
            "BAD_AMOUNT", f"gross {amounts['gross']}, deductions {amounts['deductions']}"
        )
    if amounts["net"] != amounts["gross"] - amounts["deductions"]:
        raise ParseError(
            "NET_MISMATCH", f"{amounts['gross']} - {amounts['deductions']} != {amounts['net']}"
        )
    return (
        period,
        code,
        name,
        name_key(name),
        clean(cells.get("position")) or None,
        amounts["gross"],
        amounts["deductions"],
        amounts["net"],
    )


def parse_product(cells: dict[str, Any]) -> tuple:
    raw = _require(cells.get("sku"), "item code")
    sku = normalize_sku(raw)
    if sku is None:
        raise ParseError("BAD_CODE", f"item code {raw!r}")
    description = _require(cells.get("description"), "description")
    category = CATEGORIES.get(clean(cells.get("category")).lower())
    uom = UNITS.get(clean(cells.get("uom")).lower())
    if category is None or uom is None:
        raise ParseError("MISSING_FIELD", "category or unit not recognised")
    return (sku, description, category, uom)


def transform(sheets: list[SheetData]) -> Transformed:
    out = Transformed(sheets)
    by_book: dict[str, list[SheetData]] = defaultdict(list)
    for s in sheets:
        if s.layout is not None:
            by_book[s.book].append(s)

    for book_sheets in by_book.values():
        # Pass 1: read every date cell in the workbook without context, so the
        # workbook-wide day/month convention is known before any sheet is resolved.
        tokens = {
            s.sheet_index: [tokenize(r.cells.get("date")) for r in s.rows if r.kind == "data"]
            for s in book_sheets
            if "date" in s.layout.columns.values()
        }
        book_convention = convention([t for ts in tokens.values() for t in ts])
        for s in book_sheets:
            dates = None
            if s.sheet_index in tokens:  # pass 2: resolve this sheet's column in row order
                dates = iter(
                    resolve_column(
                        tokens[s.sheet_index], DateRange.for_year(s.year), book_convention
                    )
                )
            period = parse_period(s.sheet, s.year) if s.domain == "payroll" else None
            for row in s.rows:
                key = (row.book, row.sheet_index, row.row_num)
                if row.kind != "data":
                    out.rejects.append((*key, NOISE[row.kind], None))
                    continue
                d = next(dates) if dates is not None else None
                try:
                    if s.domain == "purchases":
                        parsed = parse_purchase(s, row.cells, d)
                        out.purchases.append((*key, *parsed))
                        out.supplier_rows[parsed[1]] += 1
                    elif s.domain == "sales":
                        out.sales.append((*key, *parse_sale(s, row.cells, d)))
                    elif s.domain == "inventory":
                        out.movements.append((*key, *parse_movement(s, row.cells, d)))
                    elif s.domain == "payroll":
                        out.payroll.append((*key, *parse_payroll(s, row.cells, period)))
                    else:
                        out.products.append((*key, *parse_product(row.cells)))
                except ParseError as err:
                    out.rejects.append((*key, err.reason, str(err)))
    return out
