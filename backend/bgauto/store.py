"""Shared SQLite access: one place that opens, commits and rolls back.

Every mutating path (confirm, reminders, user admin) goes through :func:`connect`
so a failed write never leaves a half-committed register behind. Audit rows are
written by :func:`audit` with the same connection as the change they describe.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config


def utcnow() -> dt.datetime:
    """Naive UTC, matching SQLite's ``datetime('now')``."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def iso(value: dt.datetime | dt.date | None = None) -> str:
    return (value or utcnow()).replace(microsecond=0).isoformat(sep=" ")


@contextmanager
def connect(path: Path | None = None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(
    conn: sqlite3.Connection,
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
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id,
            action,
            bg_no,
            json.dumps(old, default=str) if old is not None else None,
            json.dumps(new, default=str) if new is not None else None,
            ip,
        ),
    )
