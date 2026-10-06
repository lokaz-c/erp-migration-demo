import os

import pytest

from erp_migration.etl.db import connect
from erp_migration.etl.pipeline import run_etl

PG_IMAGE = "postgres:18"


@pytest.fixture(scope="session")
def pg_url():
    """A throwaway PostgreSQL 18 container for the whole test session.

    Skipped when Docker is not available, unless REQUIRE_DB_TESTS=1 (set in CI),
    in which case the missing database is a failure, not a skip.
    """
    try:
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer(PG_IMAGE, driver=None)
        container.start()
    except Exception as exc:  # Docker missing or not running
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            raise
        pytest.skip(f"PostgreSQL container unavailable: {exc}")
    yield container.get_connection_url()
    container.stop()


@pytest.fixture(scope="session")
def first_load(pg_url, small_dataset):
    with connect(pg_url) as conn:
        result = run_etl(conn, small_dataset.books)
    return result


@pytest.fixture
def conn(pg_url, first_load):
    with connect(pg_url) as c:
        yield c
