"""Synthetic master data: suppliers, products and employees.

Every name here comes from Faker (fixed seed) or from the hard-coded generic
lists below. Nothing is taken from a real company's books.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal

from faker import Faker

# Raw-material categories a supplier can trade in, with the trade words used
# to build its name.
TRADE_WORDS: dict[str, list[str]] = {
    "fabric": ["Textiles", "Fabrics", "Cloth Merchants", "Fabric House"],
    "trims": ["Trims", "Accessories", "Haberdashery"],
    "packaging": ["Packaging", "Print and Pack"],
    "services": ["Logistics", "Freight Services", "Engineering Services"],
}
SUPPLIER_MIX = {"fabric": 13, "trims": 9, "packaging": 4, "services": 4}
LEGAL_FORMS = ["Ltd", "Ltd", "Ltd", "Ltd", "Limited", "Limited", "Co. Ltd", "SARL"]
USD_SHARE = {"fabric": 0.45, "trims": 0.3, "packaging": 0.0, "services": 0.25}


@dataclass(frozen=True)
class Supplier:
    code: str
    name: str
    category: str
    currency: str  # invoicing currency: RWF or USD


@dataclass(frozen=True)
class Product:
    sku: str
    description: str
    category: str  # fabric | trims | packaging | services | finished
    uom: str
    base_price_rwf: Decimal  # purchase cost for inputs, list price for finished goods

    @property
    def is_stocked(self) -> bool:
        return self.category != "services"


@dataclass(frozen=True)
class Employee:
    code: str
    given_name: str
    surname: str
    position: str
    base_gross: int  # monthly gross in RWF at hire
    start: tuple[int, int]  # (year, month)
    end: tuple[int, int] | None = None

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.surname}"


@dataclass
class Catalog:
    suppliers: list[Supplier]
    products: list[Product]
    employees: list[Employee]
    supplier_products: dict[str, list[str]] = field(default_factory=dict)

    def product(self, sku: str) -> Product:
        return self._by_sku[sku]

    def __post_init__(self) -> None:
        self._by_sku = {p.sku: p for p in self.products}


def _one_letter_variant(rng: random.Random, word: str) -> str:
    """A different, plausible surname one substitution away (a hard negative)."""
    vowels = "aeiou"
    positions = [i for i, ch in enumerate(word) if i > 0 and ch in vowels]
    i = rng.choice(positions) if positions else len(word) - 1
    replacement = rng.choice([v for v in vowels if v != word[i]])
    return word[:i] + replacement + word[i + 1 :]


def make_suppliers(rng: random.Random, fake: Faker) -> list[Supplier]:
    """Build canonical suppliers.

    Three deliberate traps for the matcher are built in:
    * pairs of different suppliers that share a surname (different trade),
    * one pair whose surnames differ by a single letter (same trade),
    * multi-word names that abbreviate well ("International", "and Sons").
    """
    surnames: list[str] = []
    while len(surnames) < sum(SUPPLIER_MIX.values()):
        s = fake.last_name()
        if s not in surnames and len(s) >= 4:
            surnames.append(s)

    plans: list[tuple[str, str]] = []  # (category, surname)
    i = 0
    for category, count in SUPPLIER_MIX.items():
        for _ in range(count):
            plans.append((category, surnames[i]))
            i += 1

    # Shared-surname pairs: reuse an existing surname for a different trade.
    for a, b in [(0, 14), (3, 23), (7, 27)]:
        plans[b] = (plans[b][0], plans[a][1])
    # Near-identical pair in the same trade.
    plans[5] = (plans[4][0], _one_letter_variant(rng, plans[4][1]))

    suppliers: list[Supplier] = []
    used_stems: set[str] = set()  # names without the legal form must stay unique
    stems: list[str] = []
    for n, (category, surname) in enumerate(plans, start=1):
        if n == 6:
            # The near-identical pair: same wording, surname one letter apart.
            stem = stems[4].replace(plans[4][1], surname, 1)
            stems.append(stem)
            used_stems.add(stem.lower())
            name = f"{stem} {rng.choice(LEGAL_FORMS)}"
            suppliers.append(Supplier(f"SUP-{n:03d}", name, category, "RWF"))
            continue
        while True:
            trade = rng.choice(TRADE_WORDS[category])
            roll = rng.random()
            if roll < 0.12:
                stem = f"{surname} & Sons {trade}"
            elif roll < 0.24:
                stem = f"{surname} {trade} International"
            else:
                stem = f"{surname} {trade}"
            if stem.lower() not in used_stems:
                break
        used_stems.add(stem.lower())
        stems.append(stem)
        name = f"{stem} {rng.choice(LEGAL_FORMS)}"
        currency = "USD" if rng.random() < USD_SHARE[category] else "RWF"
        suppliers.append(Supplier(f"SUP-{n:03d}", name, category, currency))
    return suppliers


_FABRICS = [
    ("COT", "Cotton poplin", ["white", "black", "navy", "sky blue"], 150, 3200),
    ("TWL", "Cotton twill", ["khaki", "olive", "charcoal"], 150, 4100),
    ("KIT", "Kitenge wax print", ["design A", "design B", "design C", "design D"], 115, 5200),
    ("LIN", "Linen blend", ["natural", "sand"], 140, 7800),
    ("DEN", "Denim 12oz", ["indigo", "black"], 150, 6100),
    ("LNG", "Polyester lining", ["ivory", "black"], 150, 1800),
]
_TRIMS = [
    ("ZIP", "Zip", ["20cm black", "20cm white", "50cm metal"], "pc", 350),
    ("BTN", "Buttons 15mm", ["horn", "shell", "black resin"], "pc", 90),
    ("THR", "Sewing thread spool", ["black", "white", "assorted"], "pc", 1200),
    ("LBL", "Woven brand label", ["neck", "care"], "pc", 60),
    ("ELS", "Elastic 25mm", ["white", "black"], "m", 400),
]
_PACKAGING = [
    ("BAG", "Paper carrier bag", "pc", 450),
    ("BOX", "Gift box", "pc", 1500),
    ("TAG", "Swing tag", "pc", 80),
    ("PLY", "Poly garment bag", "pc", 150),
]
_SERVICES = [
    ("FRT", "Freight - inbound shipment", "job", 250000),
    ("CLR", "Customs clearing", "job", 120000),
    ("MNT", "Sewing machine servicing", "job", 95000),
]
_FINISHED = [
    (
        "DRS",
        "Dress",
        ["wrap kitenge", "A-line cotton", "shirt dress linen", "maxi kitenge", "midi twill"],
        84000,
    ),
    (
        "SHT",
        "Shirt",
        ["kitenge short sleeve", "poplin long sleeve", "linen camp collar", "denim overshirt"],
        56000,
    ),
    ("SKT", "Skirt", ["pleated kitenge", "pencil twill", "wrap linen"], 52000),
    ("TRS", "Trousers", ["wide leg linen", "chino twill", "denim straight"], 68000),
    ("JKT", "Jacket", ["kitenge bomber", "denim trucker", "linen blazer"], 130000),
    ("BAG", "Tote bag", ["kitenge", "denim"], 30000),
]


def make_products(rng: random.Random) -> list[Product]:
    products: list[Product] = []
    for code, material, variants, width, price in _FABRICS:
        for i, variant in enumerate(variants, start=1):
            jitter = Decimal(rng.randint(-200, 200))
            products.append(
                Product(
                    f"FAB-{code}-{i:03d}",
                    f"{material}, {variant}, {width}cm",
                    "fabric",
                    "m",
                    Decimal(price) + jitter,
                )
            )
    for code, item, variants, uom, price in _TRIMS:
        for i, variant in enumerate(variants, start=1):
            products.append(
                Product(f"TRM-{code}-{i:03d}", f"{item}, {variant}", "trims", uom, Decimal(price))
            )
    for code, item, uom, price in _PACKAGING:
        products.append(Product(f"PKG-{code}-001", item, "packaging", uom, Decimal(price)))
    for code, item, uom, price in _SERVICES:
        products.append(Product(f"SRV-{code}-001", item, "services", uom, Decimal(price)))
    for code, item, styles, price in _FINISHED:
        for i, style in enumerate(styles, start=1):
            jitter = Decimal(rng.randint(-3, 3) * 2000)
            products.append(
                Product(
                    f"FGD-{code}-{i:03d}",
                    f"{item}, {style}",
                    "finished",
                    "pc",
                    Decimal(price) + jitter,
                )
            )
    return products


POSITIONS = [
    # (position, headcount weight, monthly gross range in RWF) - synthetic figures
    ("Tailor", 8, (120_000, 220_000)),
    ("Machinist", 6, (110_000, 190_000)),
    ("Cutter", 3, (130_000, 210_000)),
    ("Finisher", 3, (90_000, 150_000)),
    ("Designer", 1, (350_000, 600_000)),
    ("Store keeper", 1, (180_000, 260_000)),
    ("Sales associate", 3, (120_000, 200_000)),
    ("Accountant", 1, (380_000, 550_000)),
    ("Driver", 1, (140_000, 200_000)),
    ("Workshop manager", 1, (450_000, 700_000)),
]


def make_employees(
    rng: random.Random, fake: Faker, start_year: int, end_year: int
) -> list[Employee]:
    """Headcount grows from ~18 to ~32 with some turnover. All people are fake."""
    weights = [w for _, w, _ in POSITIONS]
    employees: list[Employee] = []
    names: set[str] = set()

    def hire(year: int, month: int) -> None:
        position, _, (lo, hi) = rng.choices(POSITIONS, weights=weights)[0]
        while True:
            given, surname = fake.first_name(), fake.last_name()
            if f"{given} {surname}" not in names:
                break
        names.add(f"{given} {surname}")
        gross = rng.randrange(lo, hi, 5000)
        employees.append(
            Employee(f"E{len(employees) + 1:03d}", given, surname, position, gross, (year, month))
        )

    for _ in range(18):
        hire(start_year, 1)
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            if (year, month) == (start_year, 1):
                continue
            active = [e for e in employees if e.end is None]
            if rng.random() < 0.06 and len(active) > 12:
                leaver = rng.choice(active)
                employees[employees.index(leaver)] = Employee(
                    leaver.code,
                    leaver.given_name,
                    leaver.surname,
                    leaver.position,
                    leaver.base_gross,
                    leaver.start,
                    (year, month),
                )
            if rng.random() < 0.16:
                hire(year, month)
    return employees


def make_catalog(seed: int, start_year: int, end_year: int) -> Catalog:
    rng = random.Random(f"{seed}:catalog")
    fake = Faker("en_US")
    fake.seed_instance(seed)
    suppliers = make_suppliers(rng, fake)
    products = make_products(rng)
    employees = make_employees(rng, fake, start_year, end_year)
    by_category: dict[str, list[str]] = {}
    for p in products:
        by_category.setdefault(p.category, []).append(p.sku)
    supplier_products = {}
    for s in suppliers:
        skus = by_category[s.category]
        k = min(len(skus), rng.randint(3, 7))
        supplier_products[s.code] = sorted(rng.sample(skus, k))
    return Catalog(suppliers, products, employees, supplier_products)
