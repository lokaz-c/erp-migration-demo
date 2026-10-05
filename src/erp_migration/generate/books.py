"""Assemble workbooks: rows per domain, injected errors, duplicates and layout noise."""

from __future__ import annotations

import calendar
import copy
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from faker import Faker

from erp_migration.generate.aliases import make_alias_pool, pick_spelling
from erp_migration.generate.catalog import Catalog
from erp_migration.generate.render import Renderer, Row
from erp_migration.generate.simulate import (
    Ledger,
    Movement,
    PayrollLine,
    PurchaseLine,
    SaleLine,
    money,
)
from erp_migration.generate.styles import (
    GROUPED_HEADERS,
    HEADERS,
    INVENTORY_STYLES,
    PAYROLL_STYLES,
    PURCHASE_STYLES,
    REVISED_PURCHASE_STYLE,
    SALES_STYLES,
    TITLES,
    BookStyle,
    style_for,
)

ERROR_RATE = 0.012  # share of rows that get a deliberate, rejectable error
DUPLICATE_RATE = 0.008  # share of rows keyed twice in the same sheet
BLANK_RATE = 0.015
CARRY_OVER_FROM_DAY = 29  # Dec 29-31 rows are repeated at the top of next year's book
CONFLICTING_COPIES = 4  # re-keyed rows whose quantity disagrees with the original


@dataclass
class Column:
    field: str
    label: str
    parent: str | None = None


@dataclass
class Sheet:
    name: str
    columns: list[Column] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    layout: str = "plain"
    title: str | None = None
    free_text: list[list[Any]] | None = None  # notes / summary sheets with no data header


@dataclass
class Book:
    filename: str
    domain: str
    sheets: list[Sheet] = field(default_factory=list)


@dataclass
class Context:
    rng: random.Random
    catalog: Catalog
    fake: Faker
    start_year: int
    end_year: int
    alias_pools: dict[str, list[str]] = field(default_factory=dict)
    errored: set[str] = field(default_factory=set)  # record ids whose primary row is bad


# --- columns ---------------------------------------------------------------


def make_columns(
    rng: random.Random, header_key: str, style: BookStyle, exclude: set[str]
) -> list[Column]:
    hint = rng.choice(["Frw", "RWF"]) if style.currency == "header" else None
    money_fields = {"amount", "unit_price"}
    if style.layout == "two_level" and header_key in GROUPED_HEADERS:
        columns = []
        for parent, children in GROUPED_HEADERS[header_key]:
            for f, label in children:
                if f in exclude:
                    continue
                p = parent
                if hint and parent == "Amount":
                    p = f"Amount ({hint})"
                elif hint and f == "unit_price":
                    label = f"{label} ({hint})"
                columns.append(Column(f, label, p))
        return columns
    variants = HEADERS[header_key]
    variant = variants[style.header % len(variants)]
    return [
        Column(f, f"{label} ({hint})" if hint and f in money_fields else label)
        for f, label in variant
        if f not in exclude
    ]


def _money_exclusions(style: BookStyle) -> set[str]:
    exclude = set()
    if style.currency in ("header", "embedded"):
        exclude.add("currency")
    if not style.has_unit_price:
        exclude.add("unit_price")
    if not style.has_sku:
        exclude.add("sku")
    return exclude


# --- shared row pieces --------------------------------------------------------


def _money_cells(
    r: Renderer,
    row: Row,
    price: Decimal,
    amount: Decimal,
    currency: str,
    columns: set[str],
    true_amount: Decimal,
) -> str | None:
    cell, token, reason = r.currency_plan(currency)
    row.values["unit_price"] = r.money(price, currency, token)
    row.values["amount"] = r.money(amount, currency, token)
    if "currency" in columns:
        row.values["currency"] = cell
    row.truth.update(true_amount=str(true_amount), true_currency=currency)
    row.truth["_token"] = token
    row.truth["_sums"] = {"amount": amount}
    return reason


def _inject_money_error(
    r: Renderer,
    row: Row,
    reason: str,
    *,
    qty: Decimal,
    price: Decimal,
    amount: Decimal,
    currency: str,
    d: date,
) -> None:
    token = row.truth.get("_token")
    if reason == "BAD_DATE":
        row.values["date"] = r.bad_date(d)
        row.formats.pop("date", None)
    elif reason == "DATE_OUT_OF_RANGE":
        row.values["date"], fmt = r.out_of_range_date(d)
        if fmt:
            row.formats["date"] = fmt
    elif reason == "BAD_AMOUNT":
        row.values["amount"] = r.rng.choice(["TBC", "n/a", "#VALUE!", "see invoice"])
    elif reason == "BAD_QUANTITY":
        row.values["quantity"] = r.rng.choice([0, -int(qty) if qty >= 1 else -1])
    elif reason == "AMOUNT_MISMATCH":
        wrong = money(amount * Decimal(str(r.rng.uniform(1.12, 1.45))), currency)
        row.values["amount"] = r.money(wrong, currency, token)
    elif reason == "CURRENCY_CONFLICT":
        other = "USD" if currency == "RWF" else "RWF"
        row.values["currency"] = r.rng.choice(["RWF", "Frw"] if other == "RWF" else ["USD", "$"])
        tok = r.rng.choice(["USD {x}", "${x}"] if currency == "USD" else ["RWF {x}", "{x} Frw"])
        row.values["amount"] = r.money(amount, currency, tok)


def _pick_error(r: Renderer, options: list[tuple[str, int]]) -> str | None:
    if r.rng.random() >= ERROR_RATE:
        return None
    names, weights = zip(*options, strict=True)
    return r.rng.choices(names, weights=weights)[0]


# --- purchases ------------------------------------------------------------------


def purchase_rows(
    ctx: Context,
    r: Renderer,
    lines: list[PurchaseLine],
    columns: list[Column],
    *,
    errors: bool,
    altered: set[str] = frozenset(),
) -> list[Row]:
    cols = {c.field for c in columns}
    spelling_for_po: dict[str, str] = {}
    rows = []
    for line in lines:
        product = ctx.catalog.product(line.sku)
        if line.po_number not in spelling_for_po:
            pool = ctx.alias_pools[line.supplier_code]
            spelling_for_po[line.po_number] = r.text(pick_spelling(r.rng, pool))
        spelling = spelling_for_po[line.po_number]
        qty, amount = line.quantity, line.amount
        if line.record_id in altered:
            qty = (qty * Decimal("1.1")).quantize(Decimal(1))
            amount = money(qty * line.unit_price, line.currency)
        row = Row(
            "data",
            record_id=line.record_id,
            month=(line.order_date.year, line.order_date.month),
            sort_key=(line.order_date, line.po_number, line.sku),
        )
        row.values["date"], fmt = r.date(line.order_date)
        if fmt:
            row.formats["date"] = fmt
        row.values["po_number"] = r.doc(line.po_number)
        row.values["supplier"] = spelling
        row.values["sku"] = r.sku(line.sku)
        row.values["description"] = r.description(product.description)
        row.values["quantity"] = r.quantity(qty, product.uom)
        # The truth records what was written; an altered copy is caught by de-duplication.
        reason = _money_cells(r, row, line.unit_price, amount, line.currency, cols, amount)
        row.truth.update(
            true_date=line.order_date.isoformat(),
            supplier_code=line.supplier_code,
            supplier_raw=spelling,
        )
        row.signature = f"{line.order_date}|{qty}|{amount}|{line.currency}|{line.supplier_code}"
        if reason is None and errors:
            options = [
                ("MISSING_FIELD", 2),
                ("BAD_DATE", 2),
                ("DATE_OUT_OF_RANGE", 1),
                ("BAD_AMOUNT", 2),
                ("BAD_QUANTITY", 1),
                ("UNKNOWN_PRODUCT", 2),
            ]
            if "unit_price" in cols:
                options.append(("AMOUNT_MISMATCH", 2))
            if "currency" in cols and row.values.get("currency"):
                options.append(("CURRENCY_CONFLICT", 1))
            reason = _pick_error(r, options)
            if reason == "MISSING_FIELD":
                row.values["supplier"] = None
                row.truth["supplier_raw"] = ""
            elif reason == "UNKNOWN_PRODUCT":
                if "sku" in cols:
                    row.values["sku"] = f"{line.sku[:3]}-ZZZ-9{r.rng.randint(10, 99)}"
                else:
                    row.values["description"] = "Assorted offcuts"
            elif reason:
                _inject_money_error(
                    r,
                    row,
                    reason,
                    qty=qty,
                    price=line.unit_price,
                    amount=amount,
                    currency=line.currency,
                    d=line.order_date,
                )
        row.reason = reason
        if reason:
            ctx.errored.add(row.record_id)
        rows.append(row)
    return rows


# --- sales ------------------------------------------------------------------------


def sale_rows(
    ctx: Context, r: Renderer, lines: list[SaleLine], columns: list[Column], *, errors: bool
) -> list[Row]:
    cols = {c.field for c in columns}
    rows = []
    for line in lines:
        product = ctx.catalog.product(line.sku)
        row = Row(
            "data",
            record_id=line.record_id,
            month=(line.sale_date.year, line.sale_date.month),
            sort_key=(line.sale_date, line.receipt_no, line.sku),
        )
        row.values["date"], fmt = r.date(line.sale_date)
        if fmt:
            row.formats["date"] = fmt
        row.values["receipt_no"] = r.doc(line.receipt_no)
        row.values["channel"] = r.channel(line.channel)
        row.values["sku"] = r.sku(line.sku)
        row.values["description"] = r.description(product.description)
        row.values["quantity"] = r.quantity(line.quantity, product.uom)
        reason = _money_cells(
            r, row, line.unit_price, line.amount, line.currency, cols, line.amount
        )
        row.truth["true_date"] = line.sale_date.isoformat()
        row.signature = f"{line.sale_date}|{line.quantity}|{line.amount}|{line.currency}"
        if reason is None and errors:
            options = [
                ("MISSING_FIELD", 2),
                ("BAD_DATE", 2),
                ("BAD_AMOUNT", 2),
                ("BAD_QUANTITY", 1),
                ("UNKNOWN_PRODUCT", 2),
            ]
            if "unit_price" in cols:
                options.append(("AMOUNT_MISMATCH", 2))
            if "currency" in cols and row.values.get("currency"):
                options.append(("CURRENCY_CONFLICT", 1))
            reason = _pick_error(r, options)
            if reason == "MISSING_FIELD":
                row.values["receipt_no"] = None
            elif reason == "UNKNOWN_PRODUCT":
                row.values["sku"] = f"FGD-ZZZ-9{r.rng.randint(10, 99)}"
            elif reason:
                _inject_money_error(
                    r,
                    row,
                    reason,
                    qty=line.quantity,
                    price=line.unit_price,
                    amount=line.amount,
                    currency=line.currency,
                    d=line.sale_date,
                )
        row.reason = reason
        if reason:
            ctx.errored.add(row.record_id)
        rows.append(row)
    return rows


# --- inventory --------------------------------------------------------------------


def movement_rows(
    ctx: Context, r: Renderer, moves: list[Movement], columns: list[Column], *, errors: bool
) -> list[Row]:
    cols = {c.field for c in columns}
    rows = []
    for mv in moves:
        product = ctx.catalog.product(mv.sku)
        row = Row(
            "data",
            record_id=mv.record_id,
            month=(mv.movement_date.year, mv.movement_date.month),
            sort_key=(mv.movement_date, mv.voucher_no),
            bucket="Finished goods" if product.category == "finished" else "Raw materials",
        )
        row.values["date"], fmt = r.date(mv.movement_date)
        if fmt:
            row.formats["date"] = fmt
        row.values["voucher_no"] = r.doc(mv.voucher_no)
        row.values["sku"] = r.sku(mv.sku)
        row.values["description"] = r.description(product.description)
        row.values["movement_type"] = r.movement(mv.movement_type, mv.reference)
        reference = mv.reference
        if mv.movement_type == "receipt":
            reference = r.doc(mv.reference)
        row.values["reference"] = reference
        size = abs(mv.quantity)
        if "qty_in" in cols:
            if mv.quantity > 0:
                row.values["qty_in"] = r.quantity(size, product.uom)
            else:
                row.values["qty_out"] = r.quantity(size, product.uom)
        else:
            # Clerks often typed outgoing quantities without a minus sign.
            signed = mv.quantity
            if mv.movement_type in ("issue", "sale") and r.rng.random() < 0.4:
                signed = size
            row.values["quantity"] = r.quantity(abs(signed), product.uom)
            if signed < 0:
                q = row.values["quantity"]
                row.values["quantity"] = -q if not isinstance(q, str) else f"-{q}"
        row.truth.update(true_date=mv.movement_date.isoformat(), true_amount=str(mv.quantity))
        row.signature = f"{mv.movement_date}|{mv.sku}|{mv.movement_type}|{mv.quantity}"
        reason = None
        if errors:
            reason = _pick_error(
                r,
                [
                    ("MISSING_FIELD", 1),
                    ("BAD_DATE", 2),
                    ("UNKNOWN_MOVEMENT_TYPE", 2),
                    ("BAD_QUANTITY", 2),
                    ("UNKNOWN_PRODUCT", 2),
                ],
            )
            if reason == "MISSING_FIELD":
                row.values["voucher_no"] = None
            elif reason == "BAD_DATE":
                row.values["date"] = r.bad_date(mv.movement_date)
                row.formats.pop("date", None)
            elif reason == "UNKNOWN_MOVEMENT_TYPE":
                row.values["movement_type"] = r.rng.choice(["Transfer", "XFER", "??"])
            elif reason == "BAD_QUANTITY":
                if "qty_in" in cols:
                    row.values["qty_in"] = row.values["qty_out"] = int(size) or 1
                else:
                    row.values["quantity"] = 0
            elif reason == "UNKNOWN_PRODUCT":
                row.values["sku"] = f"{mv.sku[:3]}-ZZZ-9{r.rng.randint(10, 99)}"
        row.reason = reason
        if reason:
            ctx.errored.add(row.record_id)
        rows.append(row)
    return rows


# --- payroll ------------------------------------------------------------------------


def payroll_rows(
    ctx: Context, r: Renderer, lines: list[PayrollLine], *, errors: bool, extra_unknown: bool
) -> list[Row]:
    employees = {e.code: e for e in ctx.catalog.employees}
    rows = []
    for line in lines:
        emp = employees[line.employee_code]
        row = Row("data", record_id=line.record_id, sort_key=(line.employee_code,))
        code_missing = r.rng.random() < 0.05
        row.values["employee_code"] = None if code_missing else r.employee_code(emp.code)
        row.values["name"] = r.name(emp.given_name, emp.surname)
        row.values["position"] = emp.position
        for f in ("gross", "deductions", "net"):
            row.values[f] = r.money(getattr(line, f), "RWF", None)
        row.truth.update(
            true_date=line.period.isoformat(), true_amount=str(line.net), true_currency="RWF"
        )
        row.truth["_sums"] = {"gross": line.gross, "deductions": line.deductions, "net": line.net}
        row.signature = f"{line.gross}|{line.deductions}|{line.net}"
        reason = None
        if errors:
            reason = _pick_error(r, [("NET_MISMATCH", 3), ("BAD_AMOUNT", 1), ("MISSING_FIELD", 1)])
            if reason == "NET_MISMATCH":
                row.values["net"] = r.money(line.net + 10_000, "RWF", None)
            elif reason == "BAD_AMOUNT":
                row.values["gross"] = r.rng.choice(["see HR file", "TBC"])
            elif reason == "MISSING_FIELD":
                row.values["gross"] = None
        row.reason = reason
        rows.append(row)
    if extra_unknown:
        # A casual worker paid from the payroll sheet but never registered as staff.
        gross = Decimal(r.rng.randrange(60_000, 120_000, 5000))
        ded = (gross * Decimal("0.12")).quantize(Decimal(1))
        row = Row(
            "data",
            record_id=f"PY:extra:{lines[0].period:%Y-%m}",
            sort_key=("ZZZ",),
            reason="UNKNOWN_EMPLOYEE",
        )
        staff = {(e.given_name, e.surname) for e in ctx.catalog.employees}
        while (person := (ctx.fake.first_name(), ctx.fake.last_name())) in staff:
            pass
        row.values.update(
            employee_code=None,
            name=r.name(*person),
            position="Casual",
            gross=r.money(gross, "RWF", None),
            deductions=r.money(ded, "RWF", None),
            net=r.money(gross - ded, "RWF", None),
        )
        row.truth.update(
            true_date=lines[0].period.isoformat(), true_amount=str(gross - ded), true_currency="RWF"
        )
        row.truth["_sums"] = {"gross": gross, "deductions": ded, "net": gross - ded}
        rows.append(row)
    return rows


# --- sheet assembly ---------------------------------------------------------------


def _subtotal_row(
    r: Renderer, columns: list[Column], label: str, rows: list[Row], sum_fields: tuple[str, ...]
) -> Row:
    # Like the real books, totals add up raw figures even across currencies.
    row = Row("subtotal")
    row.values[columns[0].field] = label
    for f in sum_fields:
        total = sum((x.truth.get("_sums", {}).get(f, Decimal(0)) for x in rows), Decimal(0))
        row.values[f] = float(total) if r.style.amount == "number" else f"{total:,.0f}"
    return row


def finish_sheet(
    r: Renderer,
    sheet: Sheet,
    sum_fields: tuple[str, ...],
    *,
    monthly_subtotals: bool,
    duplicates: bool = True,
    repeated_header: bool = False,
) -> Sheet:
    """Order rows, then add duplicates, blank rows, subtotals and a stray header."""
    data = sorted(sheet.rows, key=lambda x: x.sort_key)
    if duplicates:
        out: list[Row] = []
        pending: list[tuple[int, Row]] = []
        for i, row in enumerate(data):
            out.append(row)
            for _due, dup in [p for p in pending if p[0] == i]:
                out.append(dup)
            pending = [p for p in pending if p[0] != i]
            if row.reason is None and r.rng.random() < DUPLICATE_RATE:
                dup = copy.deepcopy(row)
                pending.append((i + r.rng.randint(1, 4), dup))
        out.extend(dup for _, dup in pending)
        data = out

    rows: list[Row] = []
    group: list[Row] = []
    for i, row in enumerate(data):
        if monthly_subtotals and group and row.month != group[-1].month:
            y, m = group[-1].month
            if r.rng.random() < 0.5:
                rows.append(Row("blank"))
            rows.append(
                _subtotal_row(
                    r, sheet.columns, f"Total {calendar.month_name[m]} {y}", group, sum_fields
                )
            )
            group = []
        if r.rng.random() < BLANK_RATE:
            rows.append(Row("blank"))
        if repeated_header and i == len(data) // 2:
            rows.append(Row("repeated_header", values={c.field: c.label for c in sheet.columns}))
        rows.append(row)
        group.append(row)
    if monthly_subtotals and group:
        y, m = group[-1].month
        rows.append(
            _subtotal_row(
                r, sheet.columns, f"Total {calendar.month_name[m]} {y}", group, sum_fields
            )
        )
    rows.append(Row("blank"))
    rows.append(
        _subtotal_row(
            r,
            sheet.columns,
            r.rng.choice(["GRAND TOTAL", "TOTAL", "Total"]),
            [x for x in data if x.kind == "data"],
            sum_fields,
        )
    )
    sheet.rows = rows
    return sheet


def split_rows(rows: list[Row], mode: str, year: int) -> list[tuple[str, list[Row]]]:
    def month_of(row: Row) -> int:
        # Carry-over rows from last December belong in the first sheet.
        return 1 if row.month[0] < year else row.month[1]

    if mode == "single":
        return [("", rows)]
    if mode == "quarterly":
        return [
            (f"Q{q}", [x for x in rows if (month_of(x) - 1) // 3 + 1 == q]) for q in range(1, 5)
        ]
    if mode == "halves":
        return [
            ("Jan-Jun", [x for x in rows if month_of(x) <= 6]),
            ("Jul-Dec", [x for x in rows if month_of(x) > 6]),
        ]
    if mode == "monthly":
        return [
            (calendar.month_abbr[m], [x for x in rows if month_of(x) == m]) for m in range(1, 13)
        ]
    if mode == "by_category":
        return [
            (b, [x for x in rows if x.bucket == b]) for b in ("Raw materials", "Finished goods")
        ]
    raise ValueError(mode)


NOTES = [
    ["Notes"],
    ["Figures checked against supplier statements where available."],
    ["Corrections were keyed again at the bottom of the sheet."],
]


def data_book(
    ctx: Context,
    domain: str,
    year: int,
    style: BookStyle,
    rows_fn,
    records,
    header_key: str,
    exclude: set[str],
    sum_fields: tuple[str, ...],
    filename: str,
    single_name: str,
) -> Book:
    r = Renderer(random.Random(ctx.rng.getrandbits(64)), style)
    columns = make_columns(r.rng, header_key, style, exclude)
    rows = rows_fn(r, records, columns)
    book = Book(filename, domain)
    title = f"{TITLES[domain]} {year}"
    for suffix, part in split_rows(rows, style.sheets, year):
        if not part:
            continue
        name = suffix or single_name
        sheet = Sheet(name, columns, part, style.layout, title)
        finish_sheet(
            r,
            sheet,
            sum_fields,
            monthly_subtotals=style.sheets != "monthly",
            repeated_header=style.repeated_header and not book.sheets,
        )
        book.sheets.append(sheet)
    if style.notes_sheet:
        book.sheets.append(Sheet("Notes", free_text=NOTES))
    return book


def _payroll_sheet_name(fmt: str, year: int, month: int) -> str:
    abbr = calendar.month_abbr[month]
    return {
        "Mon YYYY": f"{abbr} {year}",
        "MON-YY": f"{abbr.upper()}-{year % 100:02d}",
        "YYYY-MM": f"{year}-{month:02d}",
        "Month": calendar.month_name[month],
        "MM.YYYY": f"{month:02d}.{year}",
        "MonYY": f"{abbr}{year % 100:02d}",
    }[fmt]


def payroll_book(
    ctx: Context, year: int, style: BookStyle, lines: list[PayrollLine], duplicate_december: bool
) -> Book:
    r = Renderer(random.Random(ctx.rng.getrandbits(64)), style)
    columns = make_columns(r.rng, "payroll", style, set())
    book = Book(f"payroll_{year}.xlsx", "payroll")
    unknown_month = r.rng.randint(1, 12)
    summary = [["Month", "Total gross", "Total net"]]
    for month in range(1, 13):
        period = date(year, month, 1)
        month_lines = [x for x in lines if x.period == period]
        if not month_lines:
            continue
        rows = payroll_rows(ctx, r, month_lines, errors=True, extra_unknown=month == unknown_month)
        name = _payroll_sheet_name(style.sheet_names, year, month)
        title = f"PAYROLL - {calendar.month_name[month].upper()} {year}"
        sheet = Sheet(name, columns, rows, style.layout, title)
        finish_sheet(r, sheet, ("gross", "deductions", "net"), monthly_subtotals=False)
        book.sheets.append(sheet)
        summary.append(
            [name, int(sum(x.gross for x in month_lines)), int(sum(x.net for x in month_lines))]
        )
        if duplicate_december and month == 12:
            # The December sheet was copied to make corrections and never deleted.
            copy_sheet = copy.deepcopy(sheet)
            copy_sheet.name = f"{name} (2)"
            copy_sheet.rows = [x for x in copy_sheet.rows if x.reason is None]
            book.sheets.append(copy_sheet)
    if style.summary_sheet:
        book.sheets.append(Sheet("Summary", free_text=summary))
    return book


def item_master_book(ctx: Context) -> Book:
    r = Renderer(random.Random(ctx.rng.getrandbits(64)), BookStyle())
    columns = [
        Column("sku", "Item Code"),
        Column("description", "Description"),
        Column("category", "Category"),
        Column("uom", "Unit"),
    ]
    labels = {
        "fabric": "Fabric",
        "trims": "Trims",
        "packaging": "Packaging",
        "services": "Services",
        "finished": "Finished goods",
    }
    rows = []
    for p in ctx.catalog.products:
        sku = p.sku if r.rng.random() > 0.15 else r.rng.choice([p.sku.lower(), f"{p.sku} "])
        rows.append(
            Row(
                "data",
                values={
                    "sku": sku,
                    "description": p.description,
                    "category": labels[p.category],
                    "uom": p.uom,
                },
                record_id=f"PR:{p.sku}",
                signature=p.description,
            )
        )
    dup = copy.deepcopy(rows[r.rng.randrange(len(rows))])
    rows.insert(len(rows) // 2, dup)
    return Book("item_master.xlsx", "products", [Sheet("Items", columns, rows, "plain")])


# --- whole run ------------------------------------------------------------------------


def build_alias_pools(ctx: Context) -> None:
    seen: dict[str, str] = {}
    for s in ctx.catalog.suppliers:
        pool = make_alias_pool(ctx.rng, s.name, ctx.rng.randint(3, 7))
        clean = []
        for alias in pool:
            keys = {alias, alias.upper()}
            if any(k in seen and seen[k] != s.code for k in keys):
                continue  # never let two suppliers share a spelling
            for k in keys:
                seen[k] = s.code
            clean.append(alias)
        ctx.alias_pools[s.code] = clean


def build_books(ctx: Context, ledger: Ledger) -> list[Book]:
    build_alias_pools(ctx)
    books = [item_master_book(ctx)]
    years = range(ctx.start_year, ctx.end_year + 1)
    revised_year = ctx.start_year + 4 if ctx.end_year - ctx.start_year >= 4 else ctx.end_year

    def in_year(items, attr, year):
        return [x for x in items if getattr(x, attr).year == year]

    def carried(items, attr, year):
        return [
            x
            for x in items
            if getattr(x, attr).year == year - 1
            and getattr(x, attr).month == 12
            and getattr(x, attr).day >= CARRY_OVER_FROM_DAY
        ]

    for year in years:
        # Purchases ------------------------------------------------------------
        style = style_for(PURCHASE_STYLES, year, ctx.start_year)
        primary = in_year(ledger.purchases, "order_date", year)
        carry = carried(ledger.purchases, "order_date", year)

        def p_rows(r, records, columns, _carry=carry):
            rows = purchase_rows(ctx, r, records, columns, errors=True)
            return rows + purchase_rows(ctx, r, _carry, columns, errors=False)

        books.append(
            data_book(
                ctx,
                "purchases",
                year,
                style,
                p_rows,
                primary,
                "purchases",
                _money_exclusions(style),
                ("amount",),
                f"purchases_{year}.xlsx",
                "Purchases",
            )
        )
        if year == revised_year:
            q4 = [x for x in primary if x.order_date.month >= 10]
            clean_q4 = [x for x in q4 if x.record_id not in ctx.errored]
            altered = {x.record_id for x in ctx.rng.sample(clean_q4, CONFLICTING_COPIES)}
            rstyle = REVISED_PURCHASE_STYLE

            def rev_rows(r, records, columns, _altered=altered):
                return purchase_rows(ctx, r, records, columns, errors=False, altered=_altered)

            books.append(
                data_book(
                    ctx,
                    "purchases",
                    year,
                    rstyle,
                    rev_rows,
                    q4,
                    "purchases",
                    _money_exclusions(rstyle),
                    ("amount",),
                    f"purchases_{year}_revised.xlsx",
                    "Q4 re-keyed",
                )
            )

        # Sales ----------------------------------------------------------------
        style = style_for(SALES_STYLES, year, ctx.start_year)
        carry_s = carried(ledger.sales, "sale_date", year)

        def s_rows(r, records, columns, _carry=carry_s):
            return sale_rows(ctx, r, records, columns, errors=True) + sale_rows(
                ctx, r, _carry, columns, errors=False
            )

        books.append(
            data_book(
                ctx,
                "sales",
                year,
                style,
                s_rows,
                in_year(ledger.sales, "sale_date", year),
                "sales",
                _money_exclusions(style),
                ("amount",),
                f"sales_{year}.xlsx",
                "Sales",
            )
        )

        # Inventory ------------------------------------------------------------
        style = style_for(INVENTORY_STYLES, year, ctx.start_year)
        carry_m = carried(ledger.movements, "movement_date", year)
        key = "inventory_card" if style.stock_card else "inventory_signed"

        def m_rows(r, records, columns, _carry=carry_m):
            return movement_rows(ctx, r, records, columns, errors=True) + movement_rows(
                ctx, r, _carry, columns, errors=False
            )

        books.append(
            data_book(
                ctx,
                "inventory",
                year,
                style,
                m_rows,
                in_year(ledger.movements, "movement_date", year),
                key,
                set(),
                (),
                f"inventory_{year}.xlsx",
                "Stock card",
            )
        )

        # Payroll --------------------------------------------------------------
        style = style_for(PAYROLL_STYLES, year, ctx.start_year)
        books.append(
            payroll_book(
                ctx,
                year,
                style,
                [x for x in ledger.payroll if x.period.year == year],
                duplicate_december=year == ctx.start_year + 3,
            )
        )
    return books
