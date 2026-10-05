"""The Python stage (extract + parse) scored against the generator's truth."""

import csv
from decimal import Decimal

from erp_migration.etl.extract import extract_all
from erp_migration.etl.transform import transform

SQL_STAGE = {"UNKNOWN_PRODUCT", "UNKNOWN_EMPLOYEE", "CONFLICTING_DUPLICATE"}


def test_every_row_below_a_header_is_extracted(small_dataset):
    truth = list(csv.DictReader((small_dataset.truth / "rows.csv").open()))
    sheets = extract_all(small_dataset.books)
    rows = {(r.book, r.sheet_index, r.row_num): r.kind for s in sheets for r in s.rows}
    assert rows == {(t["book"], int(t["sheet_index"]), int(t["row_num"])): t["kind"] for t in truth}


def test_python_stage_rejects_exactly_what_the_truth_expects(small_dataset):
    truth = {
        (t["book"], int(t["sheet_index"]), int(t["row_num"])): t
        for t in csv.DictReader((small_dataset.truth / "rows.csv").open())
    }
    out = transform(extract_all(small_dataset.books))
    rejected = {(b, si, rn): code for b, si, rn, code, _ in out.rejects}
    for key, t in truth.items():
        expected = t["expected_reason"] if t["expected_reason"] not in SQL_STAGE else None
        assert rejected.get(key) == (expected or None), (key, t, rejected.get(key))


def test_parsed_dates_and_amounts_match_the_truth(small_dataset):
    truth = {
        (t["book"], int(t["sheet_index"]), int(t["row_num"])): t
        for t in csv.DictReader((small_dataset.truth / "rows.csv").open())
    }
    out = transform(extract_all(small_dataset.books))
    for row in out.purchases:
        t = truth[row[:3]]
        assert row[5].isoformat() == t["true_date"]
        assert (row[11], row[12]) == (Decimal(t["true_amount"]), t["true_currency"])
    for row in out.movements:
        assert row[4].isoformat() == truth[row[:3]]["true_date"]
