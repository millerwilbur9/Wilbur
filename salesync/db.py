import os
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def connect(url: str | None = None) -> psycopg.Connection:
    url = url or os.environ["DATABASE_URL"]
    conn = psycopg.connect(url, autocommit=False)
    conn.execute("set search_path to sales, public")
    conn.execute("set time zone 'UTC'")
    return conn


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply migrations/*.sql that have not run yet. Returns the names applied."""
    conn.execute("create schema if not exists sales")
    conn.execute(
        "create table if not exists sales.schema_migration ("
        " name text primary key, applied_at timestamptz not null default now())"
    )
    done = {r[0] for r in conn.execute("select name from sales.schema_migration")}
    applied = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in done:
            continue
        conn.execute(path.read_text())
        conn.execute("insert into sales.schema_migration (name) values (%s)", (path.name,))
        applied.append(path.name)
    conn.commit()
    conn.execute("set search_path to sales, public")
    return applied
