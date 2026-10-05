"""Tests against a real PostgreSQL 18 (testcontainers)."""

import csv
import json
from datetime import date

import psycopg
import pytest
from psycopg import errors

from erp_migration.etl.db import apply_schema
from erp_migration.etl.pipeline import run_etl
from erp_migration.report.build import build_report

pytestmark = pytest.mark.db


def _count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def test_schema_can_be_applied_repeatedly(conn):
    apply_schema(conn)
    apply_schema(conn)
    assert _count(conn, "SELECT count(*) FROM etl.reject_reasons") > 20


def test_every_source_row_has_exactly_one_outcome(conn):
    rows_in = _count(conn, "SELECT count(*) FROM staging.rows")
    assert _count(conn, "SELECT count(*) FROM etl.row_outcomes") == rows_in
    assert (
        _count(
            conn,
            """
        SELECT count(*) FROM etl.rejects j
        JOIN etl.merged_rows m USING (book, sheet_index, row_num)""",
        )
        == 0
    )
    # "loaded" is not just "not rejected": each loaded row has a core record pointing at it.
    assert (
        _count(
            conn,
            """
        SELECT count(*) FROM etl.row_outcomes o
        WHERE o.outcome = 'loaded'
          AND NOT EXISTS (SELECT 1 FROM etl.loaded_rows l
                          WHERE l.source_ref = concat_ws('|', o.book, o.sheet_index, o.row_num))
    """,
        )
        == 0
    )
    assert _count(conn, "SELECT count(*) FROM etl.loaded_rows") == _count(
        conn, "SELECT count(*) FROM etl.row_outcomes WHERE outcome = 'loaded'"
    )


def test_load_matches_the_ground_truth(conn, small_dataset):
    truth = {
        (t["book"], int(t["sheet_index"]), int(t["row_num"])): t
        for t in csv.DictReader((small_dataset.truth / "rows.csv").open())
    }
    actual = {
        (b, si, rn): (o, rc or "")
        for b, si, rn, o, rc in conn.execute(
            "SELECT book, sheet_index, row_num, outcome, reason_code FROM etl.row_outcomes"
        )
    }
    agree = sum(
        actual[k] == (t["expected_outcome"], t["expected_reason"]) for k, t in truth.items()
    )
    assert agree / len(truth) > 0.995
    # Every miss is the documented one: a re-keyed copy with an unmerged supplier spelling.
    misses = {
        (t["expected_outcome"], actual[k])
        for k, t in truth.items()
        if actual[k] != (t["expected_outcome"], t["expected_reason"])
    }
    assert misses <= {("merged", ("rejected", "CONFLICTING_DUPLICATE"))}


def test_reload_changes_nothing(pg_url, small_dataset, first_load):
    assert first_load.changes["core.sales"]["inserted"] > 0
    counts_before = {}
    with psycopg.connect(pg_url) as c:
        for table in first_load.changes:
            counts_before[table] = _count(c, f"SELECT count(*) FROM {table}")
        second = run_etl(c, small_dataset.books)
        for table, change in second.changes.items():
            assert change == {"inserted": 0, "updated": 0}, table
            assert _count(c, f"SELECT count(*) FROM {table}") == counts_before[table]


def test_reload_updates_only_what_changed(pg_url, small_dataset):
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute("""UPDATE core.sales SET quantity = quantity + 1, amount = amount + unit_price,
                     amount_rwf = amount_rwf + 1
                     WHERE sale_id = (SELECT min(sale_id) FROM core.sales)""")
        result = run_etl(c, small_dataset.books)
    assert result.changes["core.sales"] == {"inserted": 0, "updated": 1}


def test_duplicates_were_merged_with_window_ranking(conn):
    # The kept row is always the earliest (book, sheet, row) for its key.
    assert (
        _count(
            conn,
            """
        SELECT count(*) FROM etl.merged_rows
        WHERE (kept_book COLLATE "C", kept_sheet_index, kept_row_num)
              >= (book COLLATE "C", sheet_index, row_num)""",
        )
        == 0
    )
    assert _count(conn, "SELECT count(*) FROM etl.merged_rows") > 0


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        # CHECK constraints
        (
            "INSERT INTO core.products (sku, description, category, uom, source_ref) "
            "VALUES ('fab-cot-1', 'x', 'fabric', 'm', 't')",
            errors.CheckViolation,
        ),
        (
            "INSERT INTO core.fx_rates VALUES ('USD', '2019-03-15', 1000, 't')",
            errors.CheckViolation,
        ),
        ("INSERT INTO core.fx_rates VALUES ('RWF', '2031-01-01', 2, 't')", errors.CheckViolation),
        (
            "UPDATE core.purchase_order_lines SET quantity = 0 "
            "WHERE po_id = (SELECT min(po_id) FROM core.purchase_order_lines)",
            errors.CheckViolation,
        ),
        (
            "UPDATE core.payroll_lines SET net = net + 1 "
            "WHERE run_id = (SELECT min(run_id) FROM core.payroll_lines)",
            errors.CheckViolation,
        ),
        (
            "UPDATE core.inventory_movements SET quantity = abs(quantity) "
            "WHERE movement_type = 'issue'",
            errors.CheckViolation,
        ),
        (
            "UPDATE core.sales SET amount = amount * 2 "
            "WHERE sale_id = (SELECT min(sale_id) FROM core.sales)",
            errors.CheckViolation,
        ),
        # Foreign keys, including the composite (po_id, currency) one
        (
            "INSERT INTO core.supplier_aliases VALUES ('Nobody Ltd', -1, 'exact', 100, 1)",
            errors.ForeignKeyViolation,
        ),
        (
            "UPDATE core.purchase_order_lines SET currency = "
            "CASE currency WHEN 'RWF' THEN 'USD' ELSE 'RWF' END "
            "WHERE po_id = (SELECT min(po_id) FROM core.purchase_order_lines)",
            errors.ForeignKeyViolation,
        ),
        (
            "INSERT INTO core.sales (receipt_no, product_id, sale_date, channel, quantity,"
            " unit_price, amount, currency, amount_rwf, source_ref)"
            " SELECT 'RCT-2040-00001', product_id, '2040-01-10', 'export', 1, 10, 10, 'USD',"
            " 10000, 't' FROM core.products LIMIT 1",
            errors.ForeignKeyViolation,
        ),
        # Uniqueness
        (
            "INSERT INTO core.purchase_orders (po_number, supplier_id, order_date, currency, "
            "source_ref) SELECT po_number, supplier_id, order_date, currency, 'dup' "
            "FROM core.purchase_orders LIMIT 1",
            errors.UniqueViolation,
        ),
        (
            "INSERT INTO etl.rejects (book, sheet_index, row_num, reason_code) "
            "SELECT book, sheet_index, row_num, 'BAD_DATE' FROM etl.rejects LIMIT 1",
            errors.UniqueViolation,
        ),
    ],
)
def test_constraints_reject_bad_rows(conn, sql, error):
    with pytest.raises(error), conn.transaction():
        conn.execute(sql)


def test_amounts_are_converted_with_the_month_rate(conn):
    row = conn.execute("""
        SELECT s.amount, s.amount_rwf, f.rate_to_rwf
        FROM core.sales s JOIN core.fx_rates f ON (f.currency, f.month) = (s.currency, s.fx_month)
        WHERE s.currency = 'USD' ORDER BY s.sale_id LIMIT 1""").fetchone()
    amount, amount_rwf, rate = row
    assert amount_rwf == round(amount * rate, 2)


def test_report_is_built_from_the_database(conn, small_dataset, tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("intro\n<!-- results:start -->\nold\n<!-- results:end -->\nrest\n")
    out = tmp_path / "docs" / "index.html"
    written = build_report(conn, small_dataset.truth, out, readme)
    assert out in written and readme in written
    metrics = json.loads((tmp_path / "docs" / "metrics.json").read_text())
    assert metrics["rows_in"] == _count(conn, "SELECT count(*) FROM staging.rows")
    assert metrics["core_rows"]["core.sales"] == _count(conn, "SELECT count(*) FROM core.sales")
    assert metrics["loaded_rows_not_found_in_core"] == 0
    html = out.read_text()
    assert f"{metrics['rows_in']:,}" in html
    text = readme.read_text()
    assert "old" not in text and "intro" in text and "rest" in text
    assert f"{metrics['rows_loaded']:,}" in text
    # Deterministic: building again gives the same bytes.
    first = out.read_bytes()
    build_report(conn, small_dataset.truth, out, None)
    assert out.read_bytes() == first


def test_payroll_rows_without_employee_number_are_resolved_by_name(conn):
    resolved = _count(
        conn,
        """
        SELECT count(*) FROM staging.payroll_rows p
        JOIN etl.row_outcomes o USING (book, sheet_index, row_num)
        WHERE p.employee_code IS NULL AND o.outcome = 'loaded'""",
    )
    assert resolved > 0
    assert (
        _count(
            conn, "SELECT count(*) FROM core.payroll_runs WHERE period = %s", (date(2019, 6, 1),)
        )
        == 1
    )
