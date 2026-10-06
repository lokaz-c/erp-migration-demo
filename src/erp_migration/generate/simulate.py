"""Simulate ten years of a small garment workshop's transactions.

The output is the *true* ledger: what really happened. The book writer later
renders these records into messy workbooks, and the ground truth is derived
from the same records, so the pipeline can be scored against them.
"""

from __future__ import annotations

import bisect
import calendar
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from erp_migration.config import usd_rwf_rate
from erp_migration.generate.catalog import Catalog, Product

CENT = Decimal("0.01")
UNIT = Decimal("1")


@dataclass(frozen=True)
class PurchaseLine:
    record_id: str
    po_number: str
    order_date: date
    supplier_code: str
    sku: str
    quantity: Decimal
    unit_price: Decimal
    currency: str
    amount: Decimal


@dataclass(frozen=True)
class SaleLine:
    record_id: str
    receipt_no: str
    sale_date: date
    channel: str  # showroom | wholesale | export
    sku: str
    quantity: Decimal
    unit_price: Decimal
    currency: str
    amount: Decimal


@dataclass(frozen=True)
class Movement:
    record_id: str
    voucher_no: str
    movement_date: date
    sku: str
    movement_type: str  # receipt | issue | production | sale | adjustment
    quantity: Decimal  # signed: positive into stock, negative out of stock
    reference: str


@dataclass(frozen=True)
class PayrollLine:
    record_id: str
    period: date  # first day of the month
    employee_code: str
    gross: Decimal
    deductions: Decimal
    net: Decimal


@dataclass
class Ledger:
    purchases: list[PurchaseLine] = field(default_factory=list)
    sales: list[SaleLine] = field(default_factory=list)
    movements: list[Movement] = field(default_factory=list)
    payroll: list[PayrollLine] = field(default_factory=list)


def money(value: Decimal, currency: str) -> Decimal:
    q = CENT if currency == "USD" else UNIT
    return value.quantize(q, rounding=ROUND_HALF_UP)


class _StockLedger:
    """Dated stock per product, so an outflow never takes a balance below zero.

    Balances are end-of-day. The SQL reconciliation books inflows before
    outflows within a day, so non-negative end-of-day balances in the true data
    mean any negative balance the report finds comes from rows that did not load.
    """

    def __init__(self) -> None:
        self._moves: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)

    def add(self, sku: str, d: date, qty: Decimal) -> None:
        bisect.insort(self._moves[sku], (d, qty), key=lambda m: m[0])

    def headroom(self, sku: str, d: date) -> Decimal:
        """Largest outflow at date d that keeps every balance from d onwards >= 0."""
        balance = Decimal(0)
        end_of_day = Decimal(0)
        lowest_after: Decimal | None = None
        for when, qty in self._moves[sku]:
            balance += qty
            if when <= d:
                end_of_day = balance
            elif lowest_after is None or balance < lowest_after:
                lowest_after = balance
        lowest = end_of_day if lowest_after is None else min(end_of_day, lowest_after)
        return max(lowest, Decimal(0))


class _Sequences:
    def __init__(self) -> None:
        self._n: dict[tuple[str, int], int] = defaultdict(int)

    def next(self, prefix: str, year: int) -> str:
        self._n[(prefix, year)] += 1
        return f"{prefix}-{year}-{self._n[(prefix, year)]:05d}"


def _random_day(rng: random.Random, year: int, month: int) -> date:
    return date(year, month, rng.randint(1, calendar.monthrange(year, month)[1]))


def _price_drift(year: int, start_year: int) -> Decimal:
    return Decimal(1) + Decimal("0.04") * (year - start_year)


def _purchase_qty(rng: random.Random, product: Product) -> Decimal:
    if product.category == "fabric":
        return Decimal(rng.randrange(40, 260, 10))
    if product.category == "trims":
        return Decimal(rng.randrange(100, 1200, 50))
    if product.category == "packaging":
        return Decimal(rng.randrange(100, 800, 50))
    return Decimal(1)


def simulate(catalog: Catalog, seed: int, start_year: int, end_year: int) -> Ledger:
    rng = random.Random(f"{seed}:simulate")
    seq = _Sequences()
    ledger = Ledger()
    stock = _StockLedger()
    last_day = date(end_year, 12, 31)

    stocked = [p for p in catalog.products if p.is_stocked]
    raw = [p for p in stocked if p.category in ("fabric", "trims", "packaging")]
    fabrics = [p for p in raw if p.category == "fabric"]
    trims = [p for p in raw if p.category == "trims"]
    finished = [p for p in stocked if p.category == "finished"]
    supplier_weights = [rng.uniform(0.3, 3.0) for _ in catalog.suppliers]

    def move(d: date, sku: str, kind: str, qty: Decimal, ref: str, prefix: str) -> None:
        voucher = seq.next(prefix, d.year)
        ledger.movements.append(Movement(f"MV:{voucher}", voucher, d, sku, kind, qty, ref))
        stock.add(sku, d, qty)

    # Opening balances from a stock count on the first day.
    opening = date(start_year, 1, 1)
    for p in stocked:
        qty = (
            Decimal(rng.randrange(10, 40))
            if p.category == "finished"
            else Decimal(
                rng.randrange(200, 800, 10)
                if p.category == "fabric"
                else rng.randrange(300, 2000, 50)
            )
        )
        move(opening, p.sku, "adjustment", qty, "Opening balance", "ADJ")

    for year in range(start_year, end_year + 1):
        drift = _price_drift(year, start_year)
        for month in range(1, 13):
            fx = usd_rwf_rate(year, month)

            # Purchases and the goods receipts that follow them.
            for _ in range(rng.randint(7, 12) + (year - start_year) // 3):
                supplier = rng.choices(catalog.suppliers, weights=supplier_weights)[0]
                po = seq.next("PO", year)
                order_date = _random_day(rng, year, month)
                skus = catalog.supplier_products[supplier.code]
                for sku in sorted(rng.sample(skus, min(len(skus), rng.choice([1, 1, 2, 2, 3, 4])))):
                    product = catalog.product(sku)
                    qty = _purchase_qty(rng, product)
                    price_rwf = (
                        product.base_price_rwf * drift * Decimal(str(rng.uniform(0.95, 1.05)))
                    )
                    if supplier.currency == "USD":
                        unit_price = money(price_rwf / fx, "USD")
                    else:
                        unit_price = money(price_rwf / 10, "RWF") * 10
                    amount = money(qty * unit_price, supplier.currency)
                    ledger.purchases.append(
                        PurchaseLine(
                            f"PL:{po}:{sku}",
                            po,
                            order_date,
                            supplier.code,
                            sku,
                            qty,
                            unit_price,
                            supplier.currency,
                            amount,
                        )
                    )
                    if product.is_stocked:
                        received = order_date + timedelta(days=rng.randint(0, 12))
                        if received <= last_day:
                            short = rng.random() < 0.04
                            rqty = (qty * Decimal("0.9")).quantize(UNIT) if short else qty
                            move(received, sku, "receipt", rqty, po, "GRN")

            # Production orders: fabric and trims in, finished goods out.
            for d in sorted(_random_day(rng, year, month) for _ in range(rng.randint(6, 9))):
                product = rng.choice(finished)
                units = Decimal(rng.randint(25, 90))
                need = (units * Decimal(str(rng.uniform(1.5, 2.5)))).quantize(Decimal("0.5"))
                fabric = rng.choice(fabrics)
                trim = rng.choice(trims)
                if stock.headroom(fabric.sku, d) < need or stock.headroom(trim.sku, d) < units:
                    continue
                mo = seq.next("MO", year)
                move(d, fabric.sku, "issue", -need, mo, "ISS")
                move(d, trim.sku, "issue", -units, mo, "ISS")
                move(d, product.sku, "production", units, mo, "PRD")

            # Sales, then one stock-card issue per finished good for the month.
            month_end = date(year, month, calendar.monthrange(year, month)[1])
            sold: dict[str, Decimal] = defaultdict(Decimal)
            for _ in range(rng.randint(28, 42)):
                channel = rng.choices(["showroom", "wholesale", "export"], weights=[6, 3, 1])[0]
                receipt = seq.next("RCT", year)
                d = _random_day(rng, year, month)
                lines = rng.sample(finished, rng.choice([1, 1, 2, 3]))
                for product in sorted(lines, key=lambda p: p.sku):
                    available = stock.headroom(product.sku, month_end) - sold.get(
                        product.sku, Decimal(0)
                    )
                    want = {
                        "showroom": rng.randint(1, 2),
                        "wholesale": rng.randint(3, 12),
                        "export": rng.randint(10, 30),
                    }[channel]
                    qty = min(Decimal(want), available)
                    if qty <= 0:
                        continue
                    list_price = product.base_price_rwf * drift
                    if channel == "export":
                        currency = "USD"
                        unit_price = money(list_price * Decimal("0.75") / fx, "USD")
                    else:
                        currency = "RWF"
                        factor = Decimal("0.8") if channel == "wholesale" else Decimal(1)
                        unit_price = money(list_price * factor / 100, "RWF") * 100
                    ledger.sales.append(
                        SaleLine(
                            f"SL:{receipt}:{product.sku}",
                            receipt,
                            d,
                            channel,
                            product.sku,
                            qty,
                            unit_price,
                            currency,
                            money(qty * unit_price, currency),
                        )
                    )
                    sold[product.sku] += qty
            label = f"Sales {calendar.month_abbr[month]} {year}"
            for sku in sorted(k for k, v in sold.items() if v > 0):
                move(month_end, sku, "sale", -sold[sku], label, "SIV")

            # Year-end stock count corrections.
            if month == 12:
                for product in rng.sample(raw, 3):
                    delta = Decimal(rng.randint(-15, 15) or 5)
                    if delta > 0 or stock.headroom(product.sku, month_end) >= -delta:
                        move(month_end, product.sku, "adjustment", delta, "Stock count", "ADJ")

            # Payroll for everyone employed that month (all figures synthetic).
            period = date(year, month, 1)
            for emp in catalog.employees:
                if (year, month) < emp.start or (emp.end and (year, month) >= emp.end):
                    continue
                years_in = year - emp.start[0]
                gross = Decimal(emp.base_gross) * Decimal("1.04") ** years_in
                gross = (gross / 1000).quantize(UNIT, rounding=ROUND_HALF_UP) * 1000
                deductions = (gross * Decimal("0.12")).quantize(UNIT, rounding=ROUND_HALF_UP)
                ledger.payroll.append(
                    PayrollLine(
                        f"PY:{period:%Y-%m}:{emp.code}",
                        period,
                        emp.code,
                        gross,
                        deductions,
                        gross - deductions,
                    )
                )
    return ledger
