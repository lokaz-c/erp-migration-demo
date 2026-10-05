"""Score a load against the generator's ground truth.

The pipeline never reads the truth files; only this module does, after the
load, to measure how close the pipeline got.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import psycopg

from erp_migration.matching.suppliers import PairScores, pair_scores

Key = tuple[str, int, int]


def read_truth_rows(truth_dir: Path) -> dict[Key, dict[str, str]]:
    with (truth_dir / "rows.csv").open(newline="") as fh:
        return {
            (r["book"], int(r["sheet_index"]), int(r["row_num"])): r for r in csv.DictReader(fh)
        }


def read_truth_aliases(truth_dir: Path) -> dict[str, str]:
    with (truth_dir / "supplier_aliases.csv").open(newline="") as fh:
        return {r["alias"]: r["supplier_code"] for r in csv.DictReader(fh)}


def read_truth_suppliers(truth_dir: Path) -> dict[str, str]:
    with (truth_dir / "suppliers.csv").open(newline="") as fh:
        return {r["supplier_code"]: r["canonical_name"] for r in csv.DictReader(fh)}


@dataclass
class OutcomeScore:
    rows: int
    agree: int
    disagreements: list[dict] = field(default_factory=list)  # expected, actual, count, example


@dataclass
class SupplierScore:
    scores: PairScores
    spellings: int
    clusters: int
    true_suppliers: int
    false_merges: list[dict]  # one cluster holding several true suppliers
    split_suppliers: list[dict]  # one true supplier spread over several clusters
    review_total: int
    review_correct: int


@dataclass
class FieldScore:
    checked: int
    correct: int
    by_method: list[tuple[str, int, int]]  # (method, checked, correct)
    example_errors: list[str]


def score_outcomes(conn: psycopg.Connection, truth: dict[Key, dict[str, str]]) -> OutcomeScore:
    actual = {
        (b, si, rn): (o, rc or "")
        for b, si, rn, o, rc in conn.execute(
            "SELECT book, sheet_index, row_num, outcome, reason_code FROM etl.row_outcomes"
        )
    }
    pairs: Counter[tuple[tuple[str, str], tuple[str, str]]] = Counter()
    example: dict = {}
    agree = 0
    for key in sorted(truth):
        t = truth[key]
        expected = (t["expected_outcome"], t["expected_reason"])
        got = actual.get(key, ("missing", ""))
        if expected == got:
            agree += 1
            continue
        pairs[(expected, got)] += 1
        example.setdefault((expected, got), key)
    rows = [
        {
            "expected": " ".join(x for x in e if x),
            "actual": " ".join(x for x in g if x),
            "count": n,
            "example": example[(e, g)],
        }
        for (e, g), n in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return OutcomeScore(len(truth), agree, rows)


def score_suppliers(
    conn: psycopg.Connection, truth_aliases: dict[str, str], truth_names: dict[str, str]
) -> SupplierScore:
    matches = conn.execute(
        "SELECT alias, cluster_id, canonical, row_count"
        " FROM staging.supplier_matches ORDER BY alias"
    ).fetchall()
    predicted = {alias: cluster for alias, cluster, _, _ in matches}
    canonical = {cluster: name for _, cluster, name, _ in matches}
    rows = {alias: n for alias, _, _, n in matches}
    scores = pair_scores(predicted, truth_aliases)

    by_cluster: dict[int, Counter[str]] = defaultdict(Counter)
    by_true: dict[str, Counter[int]] = defaultdict(Counter)
    for alias, cluster in predicted.items():
        code = truth_aliases.get(alias)
        if code is None:
            continue
        by_cluster[cluster][code] += rows[alias]
        by_true[code][cluster] += rows[alias]

    false_merges = [
        {
            "canonical": canonical[c],
            "true_suppliers": [
                f"{truth_names[code]} ({n} rows)"
                for code, n in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        }
        for c, codes in sorted(by_cluster.items(), key=lambda kv: canonical[kv[0]])
        if len(codes) > 1
    ]
    split = [
        {
            "true_name": truth_names[code],
            "clusters": [
                f"{canonical[c]} ({n} rows)"
                for c, n in sorted(clusters.items(), key=lambda kv: (-kv[1], canonical[kv[0]]))
            ],
        }
        for code, clusters in sorted(by_true.items(), key=lambda kv: truth_names[kv[0]])
        if len(clusters) > 1
    ]

    review = conn.execute(
        "SELECT alias, nearest_canonical FROM staging.supplier_review ORDER BY alias"
    ).fetchall()
    review_correct = sum(
        truth_aliases.get(alias) is not None
        and truth_aliases.get(alias) == truth_aliases.get(nearest)
        for alias, nearest in review
    )
    return SupplierScore(
        scores,
        len(predicted),
        len(set(predicted.values())),
        len(set(truth_aliases[a] for a in predicted if a in truth_aliases)),
        false_merges,
        split,
        len(review),
        review_correct,
    )


def score_dates(conn: psycopg.Connection, truth: dict[Key, dict[str, str]]) -> FieldScore:
    rows = conn.execute("""
        SELECT book, sheet_index, row_num, order_date, date_method FROM staging.purchase_rows
        UNION ALL
        SELECT book, sheet_index, row_num, sale_date, date_method FROM staging.sale_rows
        UNION ALL
        SELECT book, sheet_index, row_num, movement_date, date_method FROM staging.movement_rows
    """).fetchall()
    checked: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    errors = []
    for book, si, rn, value, method in sorted(rows):
        true = truth[(book, si, rn)]["true_date"]
        checked[method] += 1
        if value.isoformat() == true:
            correct[method] += 1
        elif len(errors) < 5:
            errors.append(f"{book} sheet {si} row {rn}: read {value}, true {true} ({method})")
    by_method = sorted(((m, checked[m], correct[m]) for m in checked), key=lambda x: (-x[1], x[0]))
    return FieldScore(sum(checked.values()), sum(correct.values()), by_method, errors)


def score_amounts(conn: psycopg.Connection, truth: dict[Key, dict[str, str]]) -> FieldScore:
    rows = conn.execute("""
        SELECT book, sheet_index, row_num, amount, currency, currency_source
        FROM staging.purchase_rows
        UNION ALL
        SELECT book, sheet_index, row_num, amount, currency, currency_source FROM staging.sale_rows
    """).fetchall()
    checked: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    errors = []
    for book, si, rn, amount, currency, source in sorted(rows):
        t = truth[(book, si, rn)]
        checked[source] += 1
        ok = currency == t["true_currency"] and Decimal(amount) == Decimal(t["true_amount"])
        if ok:
            correct[source] += 1
        elif len(errors) < 5:
            errors.append(
                f"{book} sheet {si} row {rn}: read {amount} {currency}, "
                f"true {t['true_amount']} {t['true_currency']}"
            )
    by_source = sorted(((s, checked[s], correct[s]) for s in checked), key=lambda x: (-x[1], x[0]))
    return FieldScore(sum(checked.values()), sum(correct.values()), by_source, errors)


def expected_records(truth: dict[Key, dict[str, str]]) -> dict[str, int]:
    """Distinct true records that should end up in a core table, per domain."""
    records: dict[str, set[str]] = defaultdict(set)
    for t in truth.values():
        if t["expected_outcome"] == "loaded":
            records[t["domain"]].add(t["record_id"])
    return {domain: len(ids) for domain, ids in sorted(records.items())}
