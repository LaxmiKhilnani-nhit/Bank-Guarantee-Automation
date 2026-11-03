"""Idempotent schema top-up for the cockpit tables.

The database ships as a seeded SQLite file with no migration history, so the
only safe way to grow it is a repeatable top-up: add a column if it is missing,
create a table/index if it is missing. Nothing is dropped except the audit
table's CHECK constraint, which is rebuilt in place (see ``_widen_audit``).

Called by the master-data importer, the CLI and once at web-app startup.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

# (table, column definition). Order matters only for readability.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("company", "source TEXT"),
    ("company", "is_active INTEGER NOT NULL DEFAULT 1"),
    ("project", "state TEXT"),
    ("project", '"round" INTEGER'),
    ("project", "project_manager TEXT"),
    ("project", "source TEXT"),
    ("project", "is_active INTEGER NOT NULL DEFAULT 1"),
    ("toll_plaza", "chainage TEXT"),
    ("toll_plaza", "toll_manager TEXT"),
    ("toll_plaza", "maintenance_manager TEXT"),
    ("toll_plaza", "corridor_manager TEXT"),
    ("toll_plaza", "source TEXT"),
    ("toll_plaza", "is_active INTEGER NOT NULL DEFAULT 1"),
    ("bank", "source TEXT"),
    ("contractor", "source TEXT"),
    ("users", "failed_attempts INTEGER NOT NULL DEFAULT 0"),
    ("users", "locked_until TEXT"),
    ("bg_records", "updated_at TEXT"),
    ("bg_records", "po_number TEXT"),
)

_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY,
      user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      expires_at TEXT NOT NULL,
      last_seen  TEXT,
      ip         TEXT,
      user_agent TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lookup_cache (
      field      TEXT NOT NULL,
      query      TEXT NOT NULL,
      payload    TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (field, query)
    )
    """,
)

# The seeded audit_logs table constrains `action` to a short list. The pilot logs
# more than that (login_failed, locked, submit, ...), and SQLite cannot ALTER a
# CHECK, so the table is rebuilt once with the wider list and rows copied across.
_AUDIT_ACTIONS = (
    "upload", "extract", "confirm", "export", "remind", "extend", "close",
    "login", "login_failed", "logout", "locked", "submit", "review_save",
    "user_create", "user_update", "password_change", "role_change",
    "reminder_sent", "reminder_resend", "workbook_reset",
)
_AUDIT_DDL = """
CREATE TABLE audit_logs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL DEFAULT (datetime('now')),
  user_id   TEXT REFERENCES users(id) ON DELETE SET NULL,
  action    TEXT NOT NULL
            CHECK (action IN ({actions})),
  bg_id     TEXT REFERENCES bg_records(id) ON DELETE SET NULL,
  bg_no     TEXT,
  old_data  TEXT CHECK (old_data IS NULL OR json_valid(old_data)),
  new_data  TEXT CHECK (new_data IS NULL OR json_valid(new_data)),
  ip        TEXT
)
""".format(actions=", ".join(f"'{a}'" for a in _AUDIT_ACTIONS))

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_toll_plaza_project ON toll_plaza(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_code ON project(code)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_bg_records_to ON bg_records(to_date)",
    "CREATE INDEX IF NOT EXISTS idx_bg_records_loa ON bg_records(loa_id)",
    "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, scheduled_for)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts)",
)

# The shipped database already contains this schema. A new BG_DB (or a Docker
# volume that starts empty) has no tables at all, and the top-up below assumes
# the base tables exist, so the full idempotent script is applied once first.
_SCHEMA_FILE = config.REPO_ROOT / "db" / "sqlite" / "schema.sql"


def _widen_audit(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'audit_logs'"
    ).fetchone()
    if row is None or "login_failed" in (row[0] or ""):
        return False
    conn.executescript(
        f"""
        PRAGMA foreign_keys = OFF;
        ALTER TABLE audit_logs RENAME TO audit_logs_old;
        {_AUDIT_DDL};
        INSERT INTO audit_logs (id, ts, user_id, action, bg_id, bg_no, old_data, new_data, ip)
          SELECT id, ts, user_id, action, bg_id, bg_no, old_data, new_data, ip
          FROM audit_logs_old;
        DROP TABLE audit_logs_old;
        PRAGMA foreign_keys = ON;
        """
    )
    return True


def ensure_schema(db_path: Path | None = None) -> list[str]:
    """Add any missing columns/tables. Returns what it changed (empty when current)."""
    path = Path(db_path or config.DB_PATH)
    applied: list[str] = []
    conn = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "users" not in tables and _SCHEMA_FILE.is_file():
            # Fresh database: create the approved baseline before topping up.
            conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
            applied.append("schema:baseline")
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        for table, definition in _COLUMNS:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns:
                continue  # table absent (older or trimmed database): nothing to add
            name = definition.split()[0].strip('"')
            if name in columns:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
            applied.append(f"{table}.{name}")
        for statement in _TABLES:
            name = statement.split("EXISTS", 1)[1].split("(", 1)[0].strip()
            conn.execute(statement)
            if name not in tables:
                applied.append(f"table:{name}")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "audit_logs" in tables and _widen_audit(conn):
            applied.append("table:audit_logs(rebuilt)")
        if {"project", "toll_plaza", "sessions", "bg_records", "reminders"} <= tables:
            for statement in _INDEXES:
                conn.execute(statement)
        conn.commit()
    finally:
        conn.close()
    return applied
