import csv
from pathlib import Path

import pytest

from erp_migration.config import Paths
from erp_migration.generate import generate


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="session")
def small_dataset(tmp_path_factory) -> Paths:
    """Three years of books: big enough to contain every kind of mess, quick to load."""
    paths = Paths(tmp_path_factory.mktemp("data"))
    generate(paths, seed=42, start_year=2018, end_year=2020)
    return paths


@pytest.fixture(scope="session")
def truth_rows(small_dataset) -> list[dict[str, str]]:
    return read_csv(small_dataset.truth / "rows.csv")


@pytest.fixture(scope="session")
def truth_by_row(truth_rows) -> dict[tuple[str, int, int], dict[str, str]]:
    return {(t["book"], int(t["sheet_index"]), int(t["row_num"])): t for t in truth_rows}


@pytest.fixture(scope="session")
def truth_aliases(small_dataset) -> list[dict[str, str]]:
    return read_csv(small_dataset.truth / "supplier_aliases.csv")
