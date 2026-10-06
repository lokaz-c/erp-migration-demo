"""The Python stage (extract + parse) scored against the generator's truth."""

from decimal import Decimal

import pytest

from erp_migration.etl.extract import extract_all
from erp_migration.etl.transform import transform

SQL_STAGE = {"UNKNOWN_PRODUCT", "UNKNOWN_EMPLOYEE", "CONFLICTING_DUPLICATE"}


@pytest.fixture(scope="module")
def sheets(small_dataset):
    return extract_all(small_dataset.books)


@pytest.fixture(scope="module")
def transformed(sheets):
    return transform(sheets)


def test_every_row_below_a_header_is_extracted(sheets, truth_by_row):
    rows = {(r.book, r.sheet_index, r.row_num): r.kind for s in sheets for r in s.rows}
    assert rows == {key: t["kind"] for key, t in truth_by_row.items()}


def test_python_stage_rejects_exactly_what_the_truth_expects(transformed, truth_by_row):
    rejected = {(b, si, rn): code for b, si, rn, code, _ in transformed.rejects}
    for key, t in truth_by_row.items():
        expected = t["expected_reason"] if t["expected_reason"] not in SQL_STAGE else None
        assert rejected.get(key) == (expected or None), (key, t, rejected.get(key))


def test_parsed_dates_and_amounts_match_the_truth(transformed, truth_by_row):
    for row in transformed.purchases:
        t = truth_by_row[row[:3]]
        assert row[5].isoformat() == t["true_date"]
        assert (row[11], row[12]) == (Decimal(t["true_amount"]), t["true_currency"])
    for row in transformed.movements:
        assert row[4].isoformat() == truth_by_row[row[:3]]["true_date"]
