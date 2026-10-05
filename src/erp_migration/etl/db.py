"""PostgreSQL helpers (psycopg 3)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from importlib.resources import files

import psycopg

from erp_migration.etl.reasons import REASONS

SQL = files("erp_migration") / "sql"


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True)


def sql_text(name: str) -> str:
    return (SQL / name).read_text()


def load_scripts() -> list[str]:
    """The SQL load steps, in file-name order."""
    return [
        p.read_text()
        for p in sorted((SQL / "load").iterdir(), key=lambda p: p.name)
        if p.name.endswith(".sql")
    ]


def apply_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(sql_text("schema.sql"))
        conn.execute(sql_text("views.sql"))
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO etl.reject_reasons (code, category, stage, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE
                    SET category = EXCLUDED.category, stage = EXCLUDED.stage,
                        description = EXCLUDED.description
                """,
                [(code, *meta) for code, meta in REASONS.items()],
            )


def copy_rows(
    conn: psycopg.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence]
) -> int:
    n = 0
    sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
    with conn.cursor() as cur, cur.copy(sql) as copy:
        for row in rows:
            copy.write_row(row)
            n += 1
    return n
