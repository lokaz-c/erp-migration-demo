import pytest

from erp_migration.config import Paths
from erp_migration.generate import generate


@pytest.fixture(scope="session")
def small_dataset(tmp_path_factory) -> Paths:
    """Three years of books: big enough to contain every kind of mess, quick to load."""
    paths = Paths(tmp_path_factory.mktemp("data"))
    generate(paths, seed=42, start_year=2018, end_year=2020)
    return paths
