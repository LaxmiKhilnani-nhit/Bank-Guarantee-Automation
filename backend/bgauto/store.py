"""Shared PostgreSQL access: one place that opens, commits and rolls back.

Every mutating path (confirm, reminders, user admin) goes through :func:`connect`
so a failed write never leaves a half-committed register behind. Audit rows are
written by :func:`audit` with the same connection as the change they describe.

The connection pool is process-wide and lazily created from
``config.DATABASE_URL``. Rows come back as dictionaries (``dict_row``), so
``row["column"]`` is the only access form - unlike ``sqlite3.Row``, a psycopg
tuple has no name-based indexing for free.

Timezone policy: the session timezone is pinned to UTC so ``now()`` matches the
old ``datetime('now')`` behaviour. Calendar decisions ("today", overdue, T-7)
are made explicitly in IST with ``AT TIME ZONE 'Asia/Kolkata'``.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

# "Today" in the operations timezone. IST never observes DST.
TODAY_SQL = "(now() AT TIME ZONE 'Asia/Kolkata')::date"

_pool: ConnectionPool | None = None
_pool_conninfo: str | None = None
_pool_lock = threading.Lock()


def _json_dumps(value: Any) -> str:
    """JSON for JSONB columns; dates and Decimals are common in audit payloads."""
    return json.dumps(value, default=str, ensure_ascii=False)


def utcnow() -> dt.datetime:
    """Naive UTC, matching SQLite's ``datetime('now')``."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def naive_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Normalise a database timestamp to naive UTC for comparisons."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def iso(value: dt.datetime | dt.date | None = None) -> str:
    return (value or utcnow()).replace(microsecond=0).isoformat(sep=" ")


def _conninfo() -> str:
    url = (config.DATABASE_URL or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at the PostgreSQL database, e.g. "
            "postgresql://bg_app:password@db:5432/bg_automation"
        )
    return url


def _configure(conn) -> None:
    """Pin each pooled session to UTC so ``now()`` matches the old behaviour."""
    conn.execute("SET TIME ZONE 'UTC'")
    conn.commit()


def get_pool() -> ConnectionPool:
    """Return the process-wide pool, creating it on first use."""
    global _pool, _pool_conninfo
    url = _conninfo()
    with _pool_lock:
        if _pool is not None and _pool_conninfo == url:
            return _pool
        if _pool is not None:  # configuration changed (tests, CLI): start clean
            _pool.close()
        _pool = ConnectionPool(
            url,
            min_size=config.DB_POOL_MIN,
            max_size=config.DB_POOL_MAX,
            timeout=config.DB_POOL_TIMEOUT,
            kwargs={"row_factory": dict_row},
            configure=_configure,
            open=True,
            name="bgauto",
        )
        _pool_conninfo = url
        return _pool


def reset_pool() -> None:
    """Close and forget the pool (tests and the CLI after a config change)."""
    global _pool, _pool_conninfo
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _pool_conninfo = None


@contextmanager
def connect(path=None):
    """One transaction. Commits on success, rolls back on any exception.

    ``path`` is accepted for call-site compatibility with the old SQLite
    signature; PostgreSQL always uses ``DATABASE_URL``. Passing a path is an
    error because silently writing to a different database would be worse.
    """
    if path is not None:
        raise ValueError("Pass DATABASE_URL, not a database path.")
    with get_pool().connection() as conn:
        yield conn


def audit(
    conn,
    action: str,
    *,
    user_id: str | None = None,
    bg_no: str | None = None,
    old: dict | None = None,
    new: dict | None = None,
    ip: str | None = None,
) -> None:
    """Record one auditable action. Never raises on a constrained action."""
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, bg_no, old_data, new_data, ip) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            user_id,
            action,
            bg_no,
            Jsonb(old, dumps=_json_dumps) if old is not None else None,
            Jsonb(new, dumps=_json_dumps) if new is not None else None,
            ip,
        ),
    )


# --- readiness -----------------------------------------------------------

_REQUIRED_TABLES = (
    "company", "project", "toll_plaza", "bank", "contractor", "bg_type",
    "nature_of_service", "bg_status", "roles", "users", "user_roles",
    "sessions", "lookup_cache", "loa_records", "bg_records", "reminders",
    "audit_logs",
)
_REQUIRED_COLUMNS = {
    "users": ("failed_attempts", "locked_until"),
    "project": ("state", "round", "project_manager", "source", "is_active"),
    "toll_plaza": ("chainage", "toll_manager", "maintenance_manager",
                   "corridor_manager", "source", "is_active"),
    "company": ("source", "is_active"),
    "audit_logs": ("ip", "old_data", "new_data"),
    "bg_status": ("sort_order",),
}


def schema_problems(conn) -> list[str]:
    """What is missing from the database this process is connected to."""
    problems: list[str] = []
    tables = {
        r["relname"]
        for r in conn.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname = ANY (current_schemas(false))"
        ).fetchall()
    }
    for table in _REQUIRED_TABLES:
        if table not in tables:
            problems.append(f"missing table {table}")
    for table, columns in _REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        present = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = ANY (current_schemas(false)) AND table_name = %s",
                (table,),
            ).fetchall()
        }
        for column in columns:
            if column not in present:
                problems.append(f"missing column {table}.{column}")
    return problems


def require_schema(conn=None) -> None:
    """Fail fast with a readable message when migrations have not run."""
    if conn is not None:
        problems = schema_problems(conn)
    else:
        with connect() as own:
            problems = schema_problems(own)
    if problems:
        raise RuntimeError(
            "The database schema is out of date: " + "; ".join(problems)
            + ". Run `python db/bootstrap.py` (or `docker compose run --rm migrate`)."
        )


def server_version() -> str | None:
    """PostgreSQL version string, or None when the database is unreachable.

    Uses a short direct connection rather than the pool so ``/health`` fails
    fast when the database is down instead of waiting for a pooled slot.
    """
    url = (config.DATABASE_URL or "").strip()
    if not url:
        return None
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            return conn.execute("SHOW server_version").fetchone()[0]
    except psycopg.Error:
        return None
