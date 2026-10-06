"""Build the data-quality report from the database and the ground truth.

Every number on the page is queried from the last load or computed from the
truth files here; nothing is typed by hand. The output is deterministic (no
timestamps, every query fully ordered), so CI can regenerate it and fail if
the committed copy differs.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

import psycopg
from jinja2 import Environment, StrictUndefined

from erp_migration.config import FX_BASE_RATE, FX_MONTHLY_STEP
from erp_migration.matching.calibrate import calibrate
from erp_migration.matching.suppliers import AUTO_MERGE_THRESHOLD, REVIEW_THRESHOLD
from erp_migration.report import charts, scoring

SQL = files("erp_migration") / "sql" / "report"
README_START = "<!-- results:start -->"
README_END = "<!-- results:end -->"
CORE_TABLES = [
    "core.products",
    "core.suppliers",
    "core.supplier_aliases",
    "core.fx_rates",
    "core.purchase_orders",
    "core.purchase_order_lines",
    "core.sales",
    "core.inventory_movements",
    "core.employees",
    "core.payroll_runs",
    "core.payroll_lines",
]
DOMAIN_TARGET = {
    "products": "core.products",
    "purchases": "core.purchase_order_lines",
    "sales": "core.sales",
    "inventory": "core.inventory_movements",
    "payroll": "core.payroll_lines",
}


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def _report_query(conn: psycopg.Connection, name: str) -> list[dict[str, Any]]:
    return _rows(conn, (SQL / f"{name}.sql").read_text())


def _cells_text(cells: dict[str, Any], limit: int = 140) -> str:
    text = "; ".join(f"{k}: {v}" for k, v in cells.items() if v not in (None, ""))
    return text if len(text) <= limit else text[: limit - 3] + "..."


def collect(conn: psycopg.Connection, truth_dir: Path) -> dict[str, Any]:
    truth = scoring.read_truth_rows(truth_dir)
    manifest = json.loads((truth_dir / "manifest.json").read_text())

    sheets = _rows(
        conn,
        """
        SELECT book, sheet_index, sheet, domain, skipped_reason
        FROM staging.sheets ORDER BY book COLLATE "C", sheet_index""",
    )
    outcomes = {
        r["outcome"]: r["n"]
        for r in _rows(conn, "SELECT outcome, count(*) AS n FROM etl.row_outcomes GROUP BY outcome")
    }
    per_book = _rows(
        conn,
        """
        SELECT o.book, min(o.domain) AS domain, count(DISTINCT o.sheet_index) AS sheets,
               count(*) AS rows_in,
               count(*) FILTER (WHERE o.outcome = 'loaded')   AS loaded,
               count(*) FILTER (WHERE o.outcome = 'merged')   AS merged,
               count(*) FILTER (WHERE o.outcome = 'rejected') AS rejected,
               count(*) FILTER (WHERE o.outcome = 'skipped')  AS skipped,
               count(*) FILTER (WHERE o.outcome = 'loaded' AND l.source_ref IS NULL)
                   AS not_found_in_core
        FROM etl.row_outcomes o
        LEFT JOIN etl.loaded_rows l
               ON l.source_ref = concat_ws('|', o.book, o.sheet_index, o.row_num)
        GROUP BY o.book
        ORDER BY o.book COLLATE "C" """,
    )
    totals = {
        k: sum(b[k] for b in per_book)
        for k in ("rows_in", "loaded", "merged", "rejected", "skipped", "not_found_in_core")
    }

    reasons = _rows(
        conn,
        """
        SELECT rr.code, rr.category, rr.stage, rr.description, count(j.reason_code) AS n
        FROM etl.reject_reasons rr
        LEFT JOIN etl.rejects j ON j.reason_code = rr.code
        GROUP BY rr.code, rr.category, rr.stage, rr.description
        HAVING count(j.reason_code) > 0
        ORDER BY rr.category, count(j.reason_code) DESC, rr.code""",
    )
    examples = _rows(
        conn,
        """
        WITH ranked AS (
            SELECT j.reason_code, j.book, j.sheet_index, r.sheet, j.row_num, j.detail, r.cells,
                   row_number() OVER (PARTITION BY j.reason_code
                                      ORDER BY j.book COLLATE "C", j.sheet_index, j.row_num) AS n,
                   count(*) OVER (PARTITION BY j.reason_code) AS total
            FROM etl.rejects j
            JOIN staging.rows r USING (book, sheet_index, row_num)
        )
        SELECT * FROM ranked
        WHERE n <= 3 OR (total > 3 AND n = total)
        ORDER BY reason_code, n""",
    )
    by_reason: dict[str, list[dict]] = {}
    for e in examples:
        if len(by_reason.setdefault(e["reason_code"], [])) < 3:
            e["cells_text"] = _cells_text(e["cells"])
            by_reason[e["reason_code"]].append(e)
    for r in reasons:
        r["examples"] = by_reason.get(r["code"], [])

    merged_where = _rows(
        conn,
        """
        SELECT r.domain,
               CASE WHEN m.book = m.kept_book AND m.sheet_index = m.kept_sheet_index
                        THEN 'same sheet'
                    WHEN m.book = m.kept_book THEN 'same workbook, another sheet'
                    ELSE 'another workbook' END AS location,
               count(*) AS n
        FROM etl.merged_rows m
        JOIN staging.rows r USING (book, sheet_index, row_num)
        GROUP BY 1, 2
        ORDER BY 1, 2""",
    )
    merged_examples = _rows(
        conn,
        """
        SELECT DISTINCT ON (location) location, natural_key, book, sheet, row_num, kept_book,
               kept_sheet, kept_row_num
        FROM (
            SELECT CASE WHEN m.book = m.kept_book AND m.sheet_index = m.kept_sheet_index
                            THEN 'same sheet'
                        WHEN m.book = m.kept_book THEN 'same workbook, another sheet'
                        ELSE 'another workbook' END AS location,
                   m.natural_key, m.book, r.sheet, m.row_num, m.kept_book,
                   k.sheet AS kept_sheet, m.kept_row_num
            FROM etl.merged_rows m
            JOIN staging.rows r USING (book, sheet_index, row_num)
            JOIN staging.rows k ON (k.book, k.sheet_index, k.row_num)
                                 = (m.kept_book, m.kept_sheet_index, m.kept_row_num)
        ) x
        ORDER BY location, book COLLATE "C", row_num""",
    )

    matches_by_method = _rows(
        conn,
        """
        SELECT method, count(*) AS spellings, sum(row_count) AS rows
        FROM staging.supplier_matches GROUP BY method ORDER BY method""",
    )
    top_suppliers = _rows(
        conn,
        """
        SELECT canonical, count(*) AS spellings, sum(row_count) AS rows,
               string_agg(alias, ' | ' ORDER BY row_count DESC, alias) AS aliases
        FROM staging.supplier_matches
        GROUP BY canonical
        ORDER BY count(*) DESC, sum(row_count) DESC, canonical
        LIMIT 8""",
    )
    review = _rows(
        conn,
        """
        SELECT alias, nearest_canonical, score FROM staging.supplier_review
        ORDER BY score DESC, alias""",
    )
    truth_aliases = scoring.read_truth_aliases(truth_dir)
    for r in review:
        a, b = truth_aliases.get(r["alias"]), truth_aliases.get(r["nearest_canonical"])
        r["same_in_truth"] = a is not None and a == b

    date_methods = _rows(
        conn,
        """
        SELECT date_method, count(*) AS n FROM (
            SELECT date_method FROM staging.purchase_rows
            UNION ALL SELECT date_method FROM staging.sale_rows
            UNION ALL SELECT date_method FROM staging.movement_rows) d
        GROUP BY date_method ORDER BY n DESC, date_method""",
    )
    currency_sources = _rows(
        conn,
        """
        SELECT currency, currency_source, count(*) AS n FROM (
            SELECT currency, currency_source FROM staging.purchase_rows
            UNION ALL SELECT currency, currency_source FROM staging.sale_rows) c
        GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2""",
    )

    core_counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in CORE_TABLES}
    domain_rows = {
        r["domain"]: r
        for r in _rows(
            conn,
            """
        SELECT domain, count(*) AS rows_in,
               count(*) FILTER (WHERE kind = 'data') AS data_rows
        FROM staging.rows GROUP BY domain ORDER BY domain""",
        )
    }
    expected = scoring.expected_records(truth)
    before_after = [
        {
            "domain": d,
            "rows_in": domain_rows[d]["rows_in"],
            "data_rows": domain_rows[d]["data_rows"],
            "target": DOMAIN_TARGET[d],
            "loaded": core_counts[DOMAIN_TARGET[d]],
            "expected": expected.get(d, 0),
        }
        for d in ("purchases", "sales", "inventory", "payroll", "products")
        if d in domain_rows
    ]

    runs = _rows(
        conn,
        "SELECT run_id, rows_in, changes FROM etl.load_runs "
        "WHERE finished_at IS NOT NULL ORDER BY run_id",
    )
    for run in runs:
        run["inserted"] = sum(c["inserted"] for c in run["changes"].values())
        run["updated"] = sum(c["updated"] for c in run["changes"].values())

    outcome_score = scoring.score_outcomes(conn, truth)
    supplier_score = scoring.score_suppliers(
        conn, truth_aliases, scoring.read_truth_suppliers(truth_dir)
    )
    date_score = scoring.score_dates(conn, truth)
    amount_score = scoring.score_amounts(conn, truth)
    cal = calibrate()
    sweep = [(r.threshold, r.scores.precision, r.scores.recall) for r in cal.rows]

    data_reasons = [(r["code"], r["n"]) for r in reasons if r["category"] != "noise"]
    noise_reasons = [r for r in reasons if r["category"] == "noise"]

    recon = {
        "receipts": _report_query(conn, "receipts_vs_orders"),
        "negative_stock": _report_query(conn, "negative_stock"),
        "sales_vs_card": _report_query(conn, "sales_vs_stock_card"),
        "yearly": _report_query(conn, "yearly_totals"),
    }

    s = supplier_score.scores
    metrics = {
        "seed": manifest["seed"],
        "years": f"{manifest['start_year']}-{manifest['end_year']}",
        "workbooks": len({x["book"] for x in sheets}),
        "sheets_read": sum(1 for x in sheets if not x["skipped_reason"]),
        "sheets_skipped": sum(1 for x in sheets if x["skipped_reason"]),
        "rows_in": totals["rows_in"],
        "rows_loaded": totals["loaded"],
        "rows_merged": totals["merged"],
        "rows_rejected": totals["rejected"],
        "rows_skipped": totals["skipped"],
        "loaded_rows_not_found_in_core": totals["not_found_in_core"],
        "supplier_spellings": supplier_score.spellings,
        "suppliers_found": supplier_score.clusters,
        "suppliers_true": supplier_score.true_suppliers,
        "supplier_pair_precision": round(s.precision, 4),
        "supplier_pair_recall": round(s.recall, 4),
        "supplier_false_merges": len(supplier_score.false_merges),
        "supplier_review_queue": supplier_score.review_total,
        "supplier_review_correct": supplier_score.review_correct,
        "auto_merge_threshold": cal.auto_threshold,
        "review_threshold": cal.review_threshold,
        "outcomes_matching_truth": outcome_score.agree,
        "dates_checked": date_score.checked,
        "dates_correct": date_score.correct,
        "amounts_checked": amount_score.checked,
        "amounts_correct": amount_score.correct,
        "core_rows": core_counts,
        "last_run_inserted": runs[-1]["inserted"] if runs else None,
        "last_run_updated": runs[-1]["updated"] if runs else None,
        "load_runs": len(runs),
    }

    return {
        "metrics": metrics,
        "manifest": manifest,
        "sheets": sheets,
        "skipped_sheets": [x for x in sheets if x["skipped_reason"]],
        "outcomes": outcomes,
        "per_book": per_book,
        "totals": totals,
        "reasons": [r for r in reasons if r["category"] != "noise"],
        "noise_reasons": noise_reasons,
        "reject_chart": charts.bar_chart(data_reasons, "Rejected rows by reason"),
        "merged_where": merged_where,
        "merged_examples": merged_examples,
        "matches_by_method": matches_by_method,
        "top_suppliers": top_suppliers,
        "review": review,
        "supplier_score": supplier_score,
        "calibration": cal,
        "sweep_chart": charts.sweep_chart(
            sweep, cal.auto_threshold, "Precision and recall by auto-merge threshold"
        ),
        "sweep_rows": [
            r
            for r in cal.rows
            if r.threshold % 2 == 0
            or r.threshold in (cal.auto_threshold, cal.auto_threshold - 1, cal.auto_threshold + 1)
        ],
        "configured": (AUTO_MERGE_THRESHOLD, REVIEW_THRESHOLD),
        "date_methods": date_methods,
        "date_score": date_score,
        "currency_sources": currency_sources,
        "amount_score": amount_score,
        "before_after": before_after,
        "core_counts": core_counts,
        "runs": runs,
        "outcome_score": outcome_score,
        "recon": recon,
        "fx": {"base": FX_BASE_RATE, "step": FX_MONTHLY_STEP},
    }


def _fmt_int(v: Any) -> str:
    return f"{int(v):,}" if v is not None else ""


def _fmt_money(v: Any) -> str:
    return f"{float(v):,.0f}" if v is not None else ""


def _pct(n: float, d: float, places: int = 2) -> str:
    return f"{100 * n / d:.{places}f}%" if d else "n/a"


def render(data: dict[str, Any]) -> str:
    env = Environment(
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(n=_fmt_int, money=_fmt_money)
    env.globals.update(pct=_pct)
    template = (files("erp_migration") / "report" / "templates" / "report.html.j2").read_text()
    return env.from_string(template).render(**data)


def results_markdown(m: dict[str, Any]) -> str:
    rows = [
        (
            "Workbooks read (sheets read / skipped)",
            f"{m['workbooks']} ({m['sheets_read']} / {m['sheets_skipped']})",
        ),
        ("Rows below a header", f"{m['rows_in']:,}"),
        ("Loaded into core tables", f"{m['rows_loaded']:,}"),
        ("Exact duplicates merged", f"{m['rows_merged']:,}"),
        ("Rejected with a reason code", f"{m['rows_rejected']:,}"),
        ("Skipped layout rows (blank, subtotal, repeated header)", f"{m['rows_skipped']:,}"),
        (
            "Row outcomes that match the ground truth",
            f"{m['outcomes_matching_truth']:,} of {m['rows_in']:,} "
            f"({_pct(m['outcomes_matching_truth'], m['rows_in'])})",
        ),
        (
            "Supplier spellings collapsed into suppliers",
            f"{m['supplier_spellings']} into {m['suppliers_found']} "
            f"(ground truth: {m['suppliers_true']})",
        ),
        (
            "Supplier matching, pairwise precision / recall",
            f"{m['supplier_pair_precision']:.3f} / {m['supplier_pair_recall']:.3f}",
        ),
        (
            "Spellings left for manual review",
            f"{m['supplier_review_queue']} ({m['supplier_review_correct']} are true matches)",
        ),
        ("Dates read correctly (parsed rows)", f"{m['dates_correct']:,} of {m['dates_checked']:,}"),
        (
            "Amount and currency read correctly (parsed rows)",
            f"{m['amounts_correct']:,} of {m['amounts_checked']:,}",
        ),
        (
            "Reload of the same books: rows inserted / updated",
            f"{m['last_run_inserted']} / {m['last_run_updated']}",
        ),
    ]
    lines = [
        README_START,
        f"<!-- Generated by `make demo` (seed {m['seed']}). Do not edit by hand. -->",
        "",
        "| Measure | Result |",
        "| --- | --- |",
        *[f"| {a} | {b} |" for a, b in rows],
        "",
        README_END,
    ]
    return "\n".join(lines)


def update_readme(readme: Path, metrics: dict[str, Any]) -> bool:
    text = readme.read_text()
    pattern = re.compile(re.escape(README_START) + r".*?" + re.escape(README_END), re.S)
    if not pattern.search(text):
        return False
    readme.write_text(pattern.sub(lambda _: results_markdown(metrics), text))
    return True


def build_report(
    conn: psycopg.Connection, truth_dir: Path, out: Path, readme: Path | None
) -> list[Path]:
    data = collect(conn, truth_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data))
    metrics_path = out.parent / "metrics.json"
    metrics_path.write_text(
        json.dumps(data["metrics"], indent=2, sort_keys=True, default=str) + "\n"
    )
    written = [out, metrics_path]
    if readme and readme.exists() and update_readme(readme, data["metrics"]):
        written.append(readme)
    return written
