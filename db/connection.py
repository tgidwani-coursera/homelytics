"""PostgreSQL connection management and generic upsert helpers.

Credentials are read from the environment (see .env.example). A module-level
ThreadedConnectionPool is created lazily so the scrapers can share connections
without re-authenticating on every query.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("homelytics.db")

_POOL: ThreadedConnectionPool | None = None
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _dsn() -> dict[str, Any]:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "homelytics"),
        "user": os.getenv("DB_USER", "homelytics"),
        "password": os.getenv("DB_PASSWORD", ""),
    }


def init_pool(minconn: int = 1, maxconn: int = 5) -> ThreadedConnectionPool:
    """Create the shared connection pool (idempotent)."""
    global _POOL
    if _POOL is None:
        logger.info("Initialising PostgreSQL connection pool")
        _POOL = ThreadedConnectionPool(minconn, maxconn, **_dsn())
    return _POOL


def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.closeall()
        _POOL = None


@contextmanager
def get_cursor(commit: bool = True, dict_rows: bool = False) -> Iterator:
    """Borrow a connection from the pool and yield a cursor.

    Rolls back and re-raises on any exception; otherwise commits when asked.
    """
    pool = init_pool()
    conn = pool.getconn()
    cursor_factory = RealDictCursor if dict_rows else None
    try:
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def apply_schema() -> None:
    """Run db/schema.sql to create tables if they do not already exist."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_cursor() as cur:
        cur.execute(sql)
    logger.info("Schema applied (tables ensured)")


def upsert(
    table: str,
    row: dict[str, Any],
    conflict_cols: Sequence[str],
    update_cols: Iterable[str] | None = None,
    returning: str | None = None,
) -> Any:
    """Insert a single row, updating on conflict (idempotent writes).

    `row` values that are dict/list are wrapped as JSONB automatically.
    Returns the value of `returning` (e.g. a generated id) when requested.
    """
    prepared = {
        key: (Json(value) if isinstance(value, (dict, list)) else value)
        for key, value in row.items()
    }
    cols = list(prepared.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_list = ", ".join(cols)
    conflict = ", ".join(conflict_cols)

    if update_cols is None:
        update_cols = [c for c in cols if c not in conflict_cols]
    update_cols = list(update_cols)

    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict_action = f"DO UPDATE SET {set_clause}"
    else:
        conflict_action = "DO NOTHING"

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) {conflict_action}"
    )
    if returning:
        sql += f" RETURNING {returning}"

    with get_cursor() as cur:
        cur.execute(sql, [prepared[c] for c in cols])
        if returning:
            fetched = cur.fetchone()
            if fetched is not None:
                return fetched[0]
            # ON CONFLICT DO NOTHING returns no row — fetch the existing id.
            where = " AND ".join(f"{c} = %s" for c in conflict_cols)
            cur.execute(
                f"SELECT {returning} FROM {table} WHERE {where}",
                [prepared[c] for c in conflict_cols],
            )
            existing = cur.fetchone()
            return existing[0] if existing else None
    return None


def upsert_many(
    table: str,
    rows: Sequence[dict[str, Any]],
    conflict_cols: Sequence[str],
    update_cols: Iterable[str] | None = None,
) -> int:
    """Bulk upsert. Returns the number of rows submitted."""
    if not rows:
        return 0

    cols = list(rows[0].keys())
    conflict = ", ".join(conflict_cols)

    if update_cols is None:
        update_cols = [c for c in cols if c not in conflict_cols]
    update_cols = list(update_cols)

    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict_action = f"DO UPDATE SET {set_clause}"
    else:
        conflict_action = "DO NOTHING"

    def _prep(row: dict[str, Any]) -> list[Any]:
        return [
            Json(row.get(c)) if isinstance(row.get(c), (dict, list)) else row.get(c)
            for c in cols
        ]

    values = [_prep(r) for r in rows]
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT ({conflict}) {conflict_action}"
    )
    with get_cursor() as cur:
        execute_values(cur, sql, values)
    return len(rows)
