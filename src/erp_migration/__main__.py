"""Command line: python -m erp_migration {generate,etl,report,calibrate}."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from erp_migration.config import (
    DEFAULT_END_YEAR,
    DEFAULT_SEED,
    DEFAULT_START_YEAR,
    Paths,
    database_url,
    default_paths,
)


def _paths(args: argparse.Namespace) -> Paths:
    return Paths(Path(args.data_dir)) if args.data_dir else default_paths()


def cmd_generate(args: argparse.Namespace) -> None:
    from erp_migration.generate import generate

    result = generate(_paths(args), args.seed, args.start_year, args.end_year)
    print(
        f"wrote {result.workbooks} workbooks, {result.rows} rows below headers "
        f"to {_paths(args).books}; ground truth in {_paths(args).truth}"
    )


def cmd_etl(args: argparse.Namespace) -> None:
    from erp_migration.etl.db import connect
    from erp_migration.etl.pipeline import run_etl

    started = time.perf_counter()
    with connect(args.database_url) as conn:
        result = run_etl(conn, _paths(args).books)
    print(
        f"run {result.run_id}: {result.workbooks} workbooks, {result.rows_in} rows in, "
        f"{time.perf_counter() - started:.1f}s"
    )
    for table, c in result.changes.items():
        print(f"  {table:28} inserted {c['inserted']:6}  updated {c['updated']:6}")


def cmd_report(args: argparse.Namespace) -> None:
    from erp_migration.etl.db import connect
    from erp_migration.report.build import build_report

    with connect(args.database_url) as conn:
        written = build_report(
            conn, _paths(args).truth, Path(args.out), Path(args.readme) if args.readme else None
        )
    for path in written:
        print(f"wrote {path}")


def cmd_calibrate(args: argparse.Namespace) -> None:
    from erp_migration.matching.calibrate import calibrate

    c = calibrate()
    print(f"seeds {c.seeds}")
    print("threshold  precision  recall")
    for r in c.rows:
        print(f"{r.threshold:9}  {r.scores.precision:9.4f}  {r.scores.recall:6.4f}")
    print(f"auto-merge threshold: {c.auto_threshold}, review threshold: {c.review_threshold}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="erp_migration")
    parser.add_argument("--data-dir", help="where books and ground truth live (default: data)")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="write synthetic messy workbooks and ground truth")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    g.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    g.set_defaults(func=cmd_generate)

    e = sub.add_parser("etl", help="load the workbooks into PostgreSQL")
    e.add_argument("--database-url", default=database_url())
    e.set_defaults(func=cmd_etl)

    r = sub.add_parser("report", help="write the data-quality report from the last load")
    r.add_argument("--database-url", default=database_url())
    r.add_argument("--out", default="docs/index.html")
    r.add_argument(
        "--readme", default="README.md", help="README whose results block is refreshed ('' to skip)"
    )
    r.set_defaults(func=cmd_report)

    c = sub.add_parser("calibrate", help="print the supplier-matching threshold sweep")
    c.set_defaults(func=cmd_calibrate)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
