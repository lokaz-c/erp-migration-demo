"""Command line: python -m erp_migration generate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from erp_migration.config import (
    DEFAULT_END_YEAR,
    DEFAULT_SEED,
    DEFAULT_START_YEAR,
    Paths,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="erp_migration")
    parser.add_argument("--data-dir", help="where books and ground truth live (default: data)")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="write synthetic messy workbooks and ground truth")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    g.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    g.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
