"""Seeded generator for synthetic, deliberately messy Excel books plus ground truth."""

from __future__ import annotations

import random
from dataclasses import dataclass

from faker import Faker

from erp_migration.config import Paths
from erp_migration.generate.books import Context, build_books
from erp_migration.generate.catalog import make_catalog
from erp_migration.generate.simulate import simulate
from erp_migration.generate.writer import write_book, write_truth


@dataclass(frozen=True)
class GenerateResult:
    workbooks: int
    rows: int
    expected: dict[str, int]


def generate(paths: Paths, seed: int, start_year: int, end_year: int) -> GenerateResult:
    if end_year < start_year:
        raise ValueError("end_year must not be before start_year")
    catalog = make_catalog(seed, start_year, end_year)
    ledger = simulate(catalog, seed, start_year, end_year)

    # Separate Faker stream for casual workers, so catalog names stay stable.
    fake = Faker("en_US")
    fake.seed_instance(seed + 1)
    ctx = Context(random.Random(f"{seed}:books"), catalog, fake, start_year, end_year)
    books = build_books(ctx, ledger)

    paths.books.mkdir(parents=True, exist_ok=True)
    for old in paths.books.glob("*.xlsx"):
        old.unlink()
    placements = []
    for book in books:
        placements.extend(write_book(book, paths.books))
    manifest = {
        "seed": seed,
        "start_year": start_year,
        "end_year": end_year,
        "workbooks": len(books),
    }
    expected = write_truth(placements, catalog.suppliers, paths.truth, manifest)
    return GenerateResult(len(books), len(placements), expected)
