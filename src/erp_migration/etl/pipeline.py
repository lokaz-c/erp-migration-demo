"""Run the whole load: extract, parse, match suppliers, stage, then load in SQL.

Everything after extraction runs in one transaction, so a failed run leaves
the previous load untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg

from erp_migration.config import FX_SOURCE, usd_rwf_rate
from erp_migration.etl.db import apply_schema, copy_rows, load_scripts
from erp_migration.etl.extract import extract_all
from erp_migration.etl.transform import Transformed, transform
from erp_migration.matching.suppliers import MatchResult, match_suppliers, normalize

STAGING_TABLES = [
    "staging.sheets",
    "staging.rows",
    "staging.product_rows",
    "staging.purchase_rows",
    "staging.sale_rows",
    "staging.movement_rows",
    "staging.payroll_rows",
    "staging.supplier_matches",
    "staging.supplier_review",
    "etl.rejects",
    "etl.merged_rows",
]
KEY = ["book", "sheet_index", "row_num"]


@dataclass(frozen=True)
class EtlResult:
    run_id: int
    workbooks: int
    rows_in: int
    changes: dict[str, dict[str, int]]


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def fx_rows(years: range) -> list[tuple]:
    rows = []
    for y in years:
        for m in range(1, 13):
            rows.append(("USD", date(y, m, 1), usd_rwf_rate(y, m), FX_SOURCE))
            rows.append(("RWF", date(y, m, 1), 1, "identity"))
    return rows


def _stage(conn: psycopg.Connection, tf: Transformed, matches: MatchResult) -> int:
    sheets = tf.sheets
    copy_rows(
        conn,
        "staging.sheets",
        [
            "book",
            "sheet_index",
            "sheet",
            "domain",
            "header_row",
            "columns",
            "currency_hint",
            "skipped_reason",
        ],
        [
            (
                s.book,
                s.sheet_index,
                s.sheet,
                s.domain,
                s.layout.header_row if s.layout else None,
                json.dumps({str(i): f for i, f in s.layout.columns.items()}) if s.layout else None,
                (s.layout.hints.get("amount") if s.layout else None),
                s.skipped_reason,
            )
            for s in sheets
        ],
    )
    rows_in = copy_rows(
        conn,
        "staging.rows",
        [*KEY, "sheet", "domain", "kind", "cells"],
        [
            (
                r.book,
                r.sheet_index,
                r.row_num,
                r.sheet,
                r.domain,
                r.kind,
                json.dumps(r.cells, default=_json_default),
            )
            for s in sheets
            for r in s.rows
        ],
    )
    copy_rows(
        conn, "staging.product_rows", [*KEY, "sku", "description", "category", "uom"], tf.products
    )
    copy_rows(
        conn,
        "staging.purchase_rows",
        [
            *KEY,
            "po_number",
            "supplier_raw",
            "order_date",
            "date_method",
            "sku",
            "description_key",
            "quantity",
            "unit_price",
            "amount",
            "currency",
            "currency_source",
        ],
        tf.purchases,
    )
    copy_rows(
        conn,
        "staging.sale_rows",
        [
            *KEY,
            "receipt_no",
            "sale_date",
            "date_method",
            "channel",
            "sku",
            "description_key",
            "quantity",
            "unit_price",
            "amount",
            "currency",
            "currency_source",
        ],
        tf.sales,
    )
    copy_rows(
        conn,
        "staging.movement_rows",
        [
            *KEY,
            "voucher_no",
            "movement_date",
            "date_method",
            "sku",
            "description_key",
            "movement_type",
            "quantity",
            "reference",
            "po_number",
        ],
        tf.movements,
    )
    copy_rows(
        conn,
        "staging.payroll_rows",
        [
            *KEY,
            "period",
            "employee_code",
            "name_raw",
            "name_key",
            "position",
            "gross",
            "deductions",
            "net",
        ],
        tf.payroll,
    )
    copy_rows(conn, "etl.rejects", [*KEY, "reason_code", "detail"], tf.rejects)
    copy_rows(
        conn,
        "staging.supplier_matches",
        [
            "alias",
            "match_key",
            "cluster_id",
            "canonical",
            "canonical_key",
            "method",
            "score",
            "row_count",
        ],
        [
            (
                m.alias,
                m.key,
                m.cluster,
                matches.clusters[m.cluster].canonical,
                normalize(matches.clusters[m.cluster].canonical),
                m.method,
                m.score,
                tf.supplier_rows[m.alias],
            )
            for m in sorted(matches.matches.values(), key=lambda m: m.alias)
        ],
    )
    copy_rows(
        conn,
        "staging.supplier_review",
        ["alias", "nearest_canonical", "score"],
        [(c.alias, matches.clusters[c.nearest_cluster].canonical, c.score) for c in matches.review],
    )
    return rows_in


def _load_fx(conn: psycopg.Connection, years: range) -> None:
    conn.execute("CREATE TEMP TABLE fx_in (LIKE core.fx_rates) ON COMMIT DROP")
    copy_rows(conn, "fx_in", ["currency", "month", "rate_to_rwf", "source"], fx_rows(years))
    conn.execute("""
        WITH up AS (
            INSERT INTO core.fx_rates AS t (currency, month, rate_to_rwf, source)
            SELECT currency, month, rate_to_rwf, source FROM fx_in
            ON CONFLICT (currency, month) DO UPDATE
                SET rate_to_rwf = EXCLUDED.rate_to_rwf, source = EXCLUDED.source
                WHERE (t.rate_to_rwf, t.source) IS DISTINCT FROM (EXCLUDED.rate_to_rwf,
                                                                  EXCLUDED.source)
            RETURNING old.currency IS NULL AS inserted
        )
        INSERT INTO load_stats
        SELECT 'core.fx_rates', count(*) FILTER (WHERE inserted),
               count(*) FILTER (WHERE NOT inserted)
        FROM up
    """)


def run_etl(conn: psycopg.Connection, books_dir: Path) -> EtlResult:
    sheets = extract_all(books_dir)
    if not sheets:
        raise FileNotFoundError(f"no .xlsx workbooks in {books_dir}; run `make data` first")
    tf = transform(sheets)
    matches = match_suppliers(tf.supplier_rows)
    years = sorted({s.year for s in sheets if s.year})
    fx_years = range(years[0] - 1, years[-1] + 2) if years else range(2000, 2031)

    apply_schema(conn)
    with conn.transaction():
        conn.execute(f"TRUNCATE {', '.join(STAGING_TABLES)}")
        conn.execute(
            "CREATE TEMP TABLE load_stats (table_name text, inserted int, updated int)"
            " ON COMMIT DROP"
        )
        run_id = conn.execute(
            "INSERT INTO etl.load_runs DEFAULT VALUES RETURNING run_id"
        ).fetchone()[0]
        rows_in = _stage(conn, tf, matches)
        _load_fx(conn, fx_years)
        for script in load_scripts():
            conn.execute(script)
        changes = {
            table: {"inserted": ins, "updated": upd}
            for table, ins, upd in conn.execute(
                "SELECT table_name, inserted, updated FROM load_stats ORDER BY table_name"
            )
        }
        workbooks = len({s.book for s in sheets})
        conn.execute(
            "UPDATE etl.load_runs SET finished_at = now(), workbooks = %s, rows_in = %s,"
            " changes = %s WHERE run_id = %s",
            (workbooks, rows_in, json.dumps(changes), run_id),
        )
    return EtlResult(run_id, workbooks, rows_in, changes)
