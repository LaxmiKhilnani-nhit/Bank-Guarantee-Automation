"""One-shot data migration: SQLite -> PostgreSQL (the SQLite file is never written).

Usage:
    python db/import_sqlite.py --source PATH [--apply]

``--source`` is required: pass the path to the legacy SQLite file. DRY RUN is
the default: the script opens it read-only
(``file:...?mode=ro``), diffs it against PostgreSQL and prints the plan. Nothing
is written until ``--apply`` is passed, and ``--apply`` performs the whole
import in ONE transaction (commit at the end, rollback on any exception, and
the verification runs inside that same transaction so a failed check leaves the
database untouched).

The target must already carry ``V1__schema.sql`` + ``V2__seed.sql`` +
``V3__schema.sql`` (``python db/apply.py``): only then do the resolved foreign
keys point at the canonical master IDs.

What moves (only the referenced business rows):

* ``users``         3 rows, copied with their original UUID strings (32-hex is
                    valid UUID input) so audit/loa/bg foreign keys keep
                    resolving; integer ``is_active`` becomes a Python bool.
* ``user_roles``    3 rows, mapped by role NAME against the target ``roles``.
* ``loa_records``   1 row, copied with its original id.
* ``bg_records``    1 row (``0024NDLG00226526``); every foreign key is resolved
                    by name/code in the target - company alias ``NSPPL`` ->
                    ``NHIT Southern Projects Private Limited``, the combined
                    plaza spelling ``Bhojpuri & Mudipar`` -> ``Bhojpuri`` (a
                    combined plaza is deliberately NOT created), and a missing
                    bank/contractor is inserted with ``source = 'migrated'``.
* ``audit_logs``    all rows, preserving ``ts``; ``old_data``/``new_data`` are
                    decoded from SQLite JSON text and re-written as JSONB, and
                    ``ip`` is stored as TEXT. The SQLite integer id is NOT
                    copied: the Postgres column is GENERATED ALWAYS AS
                    IDENTITY. Rows are inserted in source id order.

What is deliberately not copied:

* ``sessions`` (force a fresh sign-in), ``reminders``, ``lookup_cache`` and
  ``ocr_results`` (empty or cache-only);
* the legacy masters - 1 company, 12 projects, 32 toll plazas (combined
  spellings included), 7 banks, 32 contractors, 25 natures, plus the ``roles``
  and lookup tables. The canonical ``V2__seed.sql`` data wins.

Idempotent: every insert is ``ON CONFLICT DO NOTHING``, and an audit row is
skipped when the same (ts, action, user_id, bg_no, ip) already exists, so a
second run reports zero changes.

Env (see db/apply.py): ``DATABASE_URL``, else ``PGHOST`` / ``PGPORT`` /
``PGDATABASE`` / ``PGUSER`` / ``PGPASSWORD``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from apply import conn_params  # noqa: E402  (needs BASE on sys.path)

# Tables that move, in insert order (foreign keys first).
COPIED_TABLES = ("users", "user_roles", "loa_records", "bg_records", "audit_logs")

# Tables read for name resolution but never copied, with the reason.
SKIPPED_TABLES: dict[str, str] = {
    "sessions": "old sessions must not survive the move (force a fresh sign-in)",
    "reminders": "empty; the scheduler recreates its rows",
    "lookup_cache": "cache only",
    "ocr_results": "empty and unused by the application",
    "company": "legacy master (1 alias row); the canonical V2 seed wins",
    "project": "legacy masters (12 rows, one bogus); the V2 seed wins",
    "toll_plaza": "legacy masters (32 rows incl. combined spellings); V2 wins",
    "bank": "legacy masters (7 rows); only the referenced bank is added",
    "contractor": "legacy masters (32 rows); only the referenced contractor",
    "nature_of_service": "legacy masters (25 rows); the V2 seed (27) wins",
    "bg_type": "lookup, already seeded by V2/V3",
    "bg_status": "lookup, already seeded by V2/V3",
    "roles": "lookup, already seeded by V2",
}

# Row counts the live file was expected to have when the plan was written. These
# are informational only: validate() requires one target row per source row, so
# a smaller (test) database is not a failure. A mismatch is printed as a note.
PLANNED_COUNTS = {
    "users": 3, "user_roles": 3, "loa_records": 1, "bg_records": 1,
    "audit_logs": 45,
}

# Canonical V2 seed minimums; below these the foreign keys cannot resolve.
MASTER_MINIMUMS = {
    "company": 3, "project": 28, "toll_plaza": 46, "nature_of_service": 27,
    "roles": 3, "bg_type": 2, "bg_status": 6,
}

# Live company aliases -> canonical company names (V2 seed).
COMPANY_ALIAS = {
    "NSPPL": "NHIT Southern Projects Private Limited",
    "NEPPL": "NHIT Eastern Projects Private Limited",
    "NWPPL": "NHIT Western Projects Private Limited",
}

# Live combined/misspelled plaza spellings -> the canonical single plaza.
# Combined spellings are deliberately NOT seeded: the register stores one plaza
# per BG, so the live "Bhojpuri & Mudipar" resolves to "Bhojpuri".
PLAZA_SPELLING = {
    "Bhojpuri & Mudipar": "Bhojpuri",
}

# Provenance for bank/contractor rows this import has to create.
MIGRATED_SOURCE = "migrated"

BG_COLUMNS = (
    "id", "bg_no", "company_id", "project_id", "toll_plaza_id", "bg_type",
    "nature_id", "contractor_id", "bank_id", "loa_id", "po_number", "bg_amount",
    "from_date", "to_date", "claim_period", "extended_date", "status", "remarks",
    "source_pdf", "created_by", "created_at", "updated_at", "reminder_sent_at",
    "supersedes_bg_id",
)
BG_SQL = (
    f"INSERT INTO bg_records ({', '.join(BG_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(BG_COLUMNS))}) "
    "ON CONFLICT (bg_no) DO NOTHING"
)

USERS_COLUMNS = (
    "id", "username", "email", "full_name", "password_hash", "is_active",
    "last_login", "created_at", "updated_at",
)
# V3 lockout columns: copied when the target has them, left to their defaults
# otherwise.
USERS_EXTRA_COLUMNS = ("failed_attempts", "locked_until")
USERS_SQL = (
    "INSERT INTO users (id, username, email, full_name, password_hash, is_active,"
    " last_login, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (id) DO NOTHING"
)
USER_ROLES_SQL = (
    "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING"
)
LOA_SQL = (
    "INSERT INTO loa_records (id, loa_po_number, description, created_by,"
    " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (id) DO NOTHING"
)
AUDIT_SQL = (
    "INSERT INTO audit_logs (ts, user_id, action, bg_id, bg_no, old_data,"
    " new_data, ip) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)

# Per-BG foreign keys, for the "no NULL / no dangling reference" verification.
BG_FK_COLUMNS = (
    "company_id", "project_id", "toll_plaza_id", "nature_id", "contractor_id",
    "bank_id", "loa_id",
)
BG_FK_SQL = """
SELECT
  (b.company_id    IS NOT NULL AND c.id   IS NULL)::int
+ (b.project_id    IS NOT NULL AND p.id   IS NULL)::int
+ (b.toll_plaza_id IS NOT NULL AND t.id   IS NULL)::int
+ (b.nature_id     IS NOT NULL AND n.id   IS NULL)::int
+ (b.contractor_id IS NOT NULL AND ct.id  IS NULL)::int
+ (b.bank_id       IS NOT NULL AND bk.id  IS NULL)::int
+ (b.loa_id        IS NOT NULL AND l.id   IS NULL)::int
+ (b.bg_type       IS NOT NULL AND bt.code IS NULL)::int
+ (b.status        IS NOT NULL AND bs.code IS NULL)::int AS violations,
  COALESCE(c.name,  '<NULL>') AS company,
  COALESCE(p.code,  '<NULL>') AS project,
  COALESCE(t.name,  '<NULL>') AS toll_plaza,
  COALESCE(n.name,  '<NULL>') AS nature_of_service,
  COALESCE(ct.name, '<NULL>') AS contractor,
  COALESCE(bk.name, '<NULL>') AS bank,
  COALESCE(l.loa_po_number, '<NULL>') AS loa,
  COALESCE(b.bg_type, '<NULL>') AS bg_type,
  COALESCE(b.status, '<NULL>') AS status,
  u.username AS created_by
FROM bg_records b
LEFT JOIN company c             ON c.id = b.company_id
LEFT JOIN project p             ON p.id = b.project_id
LEFT JOIN toll_plaza t          ON t.id = b.toll_plaza_id
LEFT JOIN nature_of_service n   ON n.id = b.nature_id
LEFT JOIN contractor ct         ON ct.id = b.contractor_id
LEFT JOIN bank bk               ON bk.id = b.bank_id
LEFT JOIN loa_records l         ON l.id = b.loa_id
LEFT JOIN bg_type bt            ON bt.code = b.bg_type
LEFT JOIN bg_status bs          ON bs.code = b.status
LEFT JOIN users u               ON u.id = b.created_by
WHERE b.bg_no = %s
"""
FK_VIOLATION_SQL = """
SELECT 'user_roles.user_id' AS chk, count(*) AS n
  FROM user_roles ur LEFT JOIN users u ON u.id = ur.user_id WHERE u.id IS NULL
UNION ALL SELECT 'user_roles.role_id', count(*)
  FROM user_roles ur LEFT JOIN roles r ON r.id = ur.role_id WHERE r.id IS NULL
UNION ALL SELECT 'loa_records.created_by', count(*)
  FROM loa_records l LEFT JOIN users u ON u.id = l.created_by
  WHERE l.created_by IS NOT NULL AND u.id IS NULL
UNION ALL SELECT 'bg_records.company_id', count(*)
  FROM bg_records b LEFT JOIN company c ON c.id = b.company_id
  WHERE b.company_id IS NOT NULL AND c.id IS NULL
UNION ALL SELECT 'bg_records.project_id', count(*)
  FROM bg_records b LEFT JOIN project p ON p.id = b.project_id
  WHERE b.project_id IS NOT NULL AND p.id IS NULL
UNION ALL SELECT 'bg_records.toll_plaza_id', count(*)
  FROM bg_records b LEFT JOIN toll_plaza t ON t.id = b.toll_plaza_id
  WHERE b.toll_plaza_id IS NOT NULL AND t.id IS NULL
UNION ALL SELECT 'bg_records.nature_id', count(*)
  FROM bg_records b LEFT JOIN nature_of_service n ON n.id = b.nature_id
  WHERE b.nature_id IS NOT NULL AND n.id IS NULL
UNION ALL SELECT 'bg_records.contractor_id', count(*)
  FROM bg_records b LEFT JOIN contractor ct ON ct.id = b.contractor_id
  WHERE b.contractor_id IS NOT NULL AND ct.id IS NULL
UNION ALL SELECT 'bg_records.bank_id', count(*)
  FROM bg_records b LEFT JOIN bank bk ON bk.id = b.bank_id
  WHERE b.bank_id IS NOT NULL AND bk.id IS NULL
UNION ALL SELECT 'bg_records.loa_id', count(*)
  FROM bg_records b LEFT JOIN loa_records l ON l.id = b.loa_id
  WHERE b.loa_id IS NOT NULL AND l.id IS NULL
UNION ALL SELECT 'bg_records.created_by', count(*)
  FROM bg_records b LEFT JOIN users u ON u.id = b.created_by
  WHERE b.created_by IS NOT NULL AND u.id IS NULL
UNION ALL SELECT 'audit_logs.user_id', count(*)
  FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
  WHERE a.user_id IS NOT NULL AND u.id IS NULL
"""


class MigrationError(RuntimeError):
    """A migration problem the operator has to fix (bad FK, missing table...)."""


@dataclass
class Resolved:
    """One resolved foreign key: the value to store plus a report line."""

    value: Any
    detail: str
    pending: bool = False  # True: the row would be created by this import


@dataclass
class TableReport:
    """Per-table diff between the SQLite source and the PostgreSQL target."""

    table: str
    source: int = 0
    target_before: int = 0
    new: int = 0
    existing: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Rows in the target once this import has run (planned or actual)."""
        return self.target_before + self.new


# --- small helpers ---------------------------------------------------------


def open_source(path: Path) -> sqlite3.Connection:
    """Open the SQLite file read-only; it is never written or created."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def source_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def norm_ts(value: Any) -> str:
    """Naive-UTC text for a timestamp so source and target compare equal.

    SQLite stores ``datetime('now')`` (UTC, no offset); psycopg hands
    ``timestamptz`` back as an aware datetime. Both normalise to the same text.
    """
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        stamp = value
    else:
        text = str(value).strip()
        if not text:
            return ""
        try:
            stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text  # keep verbatim; PostgreSQL rejects it with a clear error
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(dt.timezone.utc).replace(tzinfo=None)
    fmt = "%Y-%m-%d %H:%M:%S.%f" if stamp.microsecond else "%Y-%m-%d %H:%M:%S"
    return stamp.strftime(fmt)


def ts_param(value: Any) -> str | None:
    """Value for a nullable timestamp column: text, or NULL for blank input."""
    text = norm_ts(value)
    return text or None


def require_ts(value: Any, label: str) -> str:
    """Value for a NOT NULL timestamp column."""
    text = norm_ts(value)
    if not text:
        raise MigrationError(f"{label}: {value!r} is not a timestamp")
    return text


def date_text(value: Any) -> str | None:
    """'YYYY-MM-DD' or None; the register may hold legacy Excel text."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return None
    return text


def canon_id(value: Any) -> str:
    """A UUID in one canonical spelling, so hex and hyphenated forms compare.

    SQLite stores ``302cc6b32e9f...``; psycopg returns a ``uuid.UUID`` for the
    target column, whose ``str()`` is hyphenated and lowercase.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return text


def audit_key(ts: str, action: Any, user_id: Any, bg_no: Any, ip: Any) -> tuple:
    """Identity of an audit row, used for the idempotency check."""
    return (ts, str(action or ""), canon_id(user_id), str(bg_no or ""), str(ip or ""))


def target_label() -> str:
    """Human-readable target with the password masked."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)
    return (f"{os.getenv('PGHOST', 'localhost')}:{os.getenv('PGPORT', '5432')}"
            f"/{os.getenv('PGDATABASE', 'bg_automation')}"
            f" as {os.getenv('PGUSER', 'bg_app')}")


def insert_sql(table: str, columns: tuple[str, ...] | list[str],
               conflict: str | None) -> str:
    """INSERT with one conflict target, or none when there is no unique key."""
    names = ", ".join(columns)
    marks = ", ".join(["%s"] * len(columns))
    tail = f" ON CONFLICT ({conflict}) DO NOTHING" if conflict else ""
    return f"INSERT INTO {table} ({names}) VALUES ({marks}){tail}"


# --- the import ------------------------------------------------------------


class Importer:
    """Diffs the SQLite source against PostgreSQL and optionally copies it."""

    def __init__(self, src: sqlite3.Connection, tgt: Any, *, apply: bool) -> None:
        self.src = src
        self.tgt = tgt
        self.apply = apply
        self.tables = source_tables(src)
        self.reports: dict[str, TableReport] = {}
        self.notes: list[str] = []
        self.bg_rows: list[tuple[dict[str, Any], dict[str, Resolved]]] = []
        self.planned_users: list[str] = []
        self.role_users: set[str] = set()
        self.created_masters: list[str] = []
        self._audit_keys: Counter | None = None
        self._columns: dict[str, set[str]] = {}

    # -- reporting ----------------------------------------------------------

    def report(self, table: str) -> TableReport:
        if table not in self.reports:
            self.reports[table] = TableReport(table)
        return self.reports[table]

    def note(self, message: str) -> None:
        self.notes.append(message)

    # -- source helpers -----------------------------------------------------

    def rows(self, table: str, order: str | None = "id") -> list[sqlite3.Row]:
        """All rows of a source table, ordered; [] when the table is missing."""
        if table not in self.tables:
            return []
        columns = {r[1] for r in self.src.execute(
            f'PRAGMA table_info("{table}")').fetchall()}
        clause = f' ORDER BY "{order}"' if order and order in columns else ""
        return self.src.execute(f'SELECT * FROM "{table}"{clause}').fetchall()

    def source_value(self, table: str, pk: Any, column: str) -> Any:
        """One column of a legacy master row (company/project/plaza/...)."""
        if table not in self.tables or pk is None:
            return None
        row = self.src.execute(
            f'SELECT * FROM "{table}" WHERE id = ?', (pk,)).fetchone()
        if row is None or column not in row.keys():
            return None
        return row[column]

    def source_name(self, table: str, pk: Any) -> str | None:
        value = self.source_value(table, pk, "name")
        return None if value is None else str(value).strip() or None

    # -- target helpers -----------------------------------------------------

    def columns(self, table: str) -> set[str]:
        if table not in self._columns:
            self._columns[table] = {
                r["column_name"] for r in self.tgt.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (table,)).fetchall()}
        return self._columns[table]

    def target_count(self, table: str) -> int:
        try:
            row = self.tgt.execute(
                sql.SQL("SELECT count(*) AS n FROM {}").format(
                    sql.Identifier(table))).fetchone()
        except psycopg.Error:  # pragma: no cover - preflight catches this first
            return 0
        return int(row["n"])

    def target_value(self, query: str, params: tuple) -> Any:
        row = self.tgt.execute(query, params).fetchone()
        return None if row is None else next(iter(row.values()))

    def user_ids(self) -> set[str]:
        return {canon_id(r["id"]) for r in self.tgt.execute(
            "SELECT id FROM users").fetchall()}

    def audit_keys(self) -> Counter:
        """How many rows in the target already share each audit identity."""
        if self._audit_keys is None:
            keys: Counter = Counter()
            for r in self.tgt.execute(
                    "SELECT ts, action, user_id, bg_no, ip FROM audit_logs").fetchall():
                keys[audit_key(norm_ts(r["ts"]), r["action"], r["user_id"],
                               r["bg_no"], r["ip"])] += 1
            self._audit_keys = keys
        return self._audit_keys

    def insert(self, rep: TableReport, statement: str, params: tuple) -> None:
        """Execute one insert (apply) or just count it (dry run)."""
        if self.apply:
            self.tgt.execute(statement, params)
        rep.new += 1

    # -- preflight ----------------------------------------------------------

    def preflight(self) -> None:
        """Fail early with a readable message when the target is not ready."""
        # SQLite stores datetime('now') in UTC. Pin the session so the naive
        # timestamps this import writes into timestamptz columns keep that
        # reading, whoever opened the connection.
        self.tgt.execute("SET TIME ZONE 'UTC'")
        have = {r["tablename"] for r in self.tgt.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()}
        needed = set(COPIED_TABLES) | {"company", "project", "toll_plaza",
                                       "nature_of_service", "bank", "contractor",
                                       "roles", "bg_type", "bg_status"}
        missing = sorted(needed - have)
        if missing:
            raise MigrationError(
                "the target is missing table(s): " + ", ".join(missing)
                + ". Run python db/apply.py first (V1 + V2 + V3).")

        required_columns = {
            "users": set(USERS_COLUMNS) | set(USERS_EXTRA_COLUMNS[:1]),
            "user_roles": {"user_id", "role_id"},
            "loa_records": {"id", "loa_po_number", "created_at", "updated_at"},
            "bg_records": set(BG_COLUMNS),
            "audit_logs": {"ts", "user_id", "action", "bg_no", "old_data",
                           "new_data", "ip"},
            "company": {"id", "name"},
            "project": {"id", "code"},
            "toll_plaza": {"id", "project_id", "name"},
            "nature_of_service": {"id", "name"},
            "bank": {"id", "name"}, "contractor": {"id", "name"},
            "roles": {"id", "name"}, "bg_type": {"code"}, "bg_status": {"code"},
        }
        for table, columns in required_columns.items():
            gaps = sorted(columns - self.columns(table))
            if gaps:
                raise MigrationError(
                    f"the target table {table} is missing column(s): "
                    + ", ".join(gaps)
                    + ". Run python db/apply.py first (V1 + V2 + V3).")

    # -- phases -------------------------------------------------------------

    def plan_all(self) -> None:
        self.plan_users()
        self.plan_user_roles()
        self.plan_loa()
        self.plan_bg()
        self.plan_audit()

    def plan_users(self) -> None:
        rep = self.report("users")
        rep.target_before = self.target_count("users")
        rows = self.rows("users")
        rep.source = len(rows)
        if not rows:
            rep.notes.append("table missing or empty in the source")
            return
        ids = self.user_ids()
        by_username = {str(r["username"]).lower(): canon_id(r["id"]) for r in self.tgt.execute(
            "SELECT id, username FROM users").fetchall()}
        extras = [c for c in USERS_EXTRA_COLUMNS if c in self.columns("users")]
        columns = list(USERS_COLUMNS) + extras
        statement = insert_sql("users", columns, "id")
        for row in rows:
            user_id = canon_id(row["id"])
            if user_id in ids:
                rep.existing += 1
                continue
            taken = by_username.get(str(row["username"] or "").lower())
            if taken is not None:
                rep.skipped += 1
                rep.notes.append(f"username {row['username']!r} already exists as "
                                 f"{taken}; source row skipped")
                continue
            is_active = row["is_active"]
            params: list[Any] = [
                user_id, row["username"], row["email"], row["full_name"],
                row["password_hash"],
                bool(is_active) if is_active is not None else True,
                ts_param(row["last_login"]),
                require_ts(row["created_at"], f"users {user_id}.created_at"),
                require_ts(row["updated_at"], f"users {user_id}.updated_at"),
            ]
            for c in extras:
                if c == "locked_until":
                    params.append(ts_param(row[c]) if c in row.keys() else None)
                elif c in row.keys():
                    params.append(row[c] if row[c] is not None else 0)
                else:
                    params.append(None)
            self.insert(rep, statement, tuple(params))
            ids.add(user_id)
            self.planned_users.append(user_id)

    def plan_user_roles(self) -> None:
        rep = self.report("user_roles")
        rep.target_before = self.target_count("user_roles")
        rows = self.rows("user_roles")
        rep.source = len(rows)
        if not rows:
            rep.notes.append("table missing or empty in the source")
            return
        role_names = {r["id"]: str(r["name"]) for r in self.rows("roles")
                      if r["name"] is not None}
        roles = {str(r["name"]): r["id"] for r in self.tgt.execute(
            "SELECT id, name FROM roles").fetchall()}
        have = {(canon_id(r["user_id"]), r["role_id"]) for r in self.tgt.execute(
            "SELECT user_id, role_id FROM user_roles").fetchall()}
        users = self.user_ids() | set(self.planned_users)
        for row in rows:
            name = role_names.get(row["role_id"])
            if name is None or name not in roles:
                rep.skipped += 1
                rep.notes.append(f"role {name or row['role_id']!r} is not in the "
                                 "target roles table; source row skipped")
                continue
            user_id = canon_id(row["user_id"])
            if user_id not in users:
                rep.skipped += 1
                rep.notes.append(f"user {user_id} is not in the target users "
                                 "table; source row skipped")
                continue
            pair = (user_id, roles[name])
            if pair in have:
                rep.existing += 1
                self.role_users.add(user_id)
                continue
            self.insert(rep, USER_ROLES_SQL, pair)
            have.add(pair)
            self.role_users.add(user_id)

    def plan_loa(self) -> None:
        rep = self.report("loa_records")
        rep.target_before = self.target_count("loa_records")
        rows = self.rows("loa_records")
        rep.source = len(rows)
        if not rows:
            rep.notes.append("table missing or empty in the source")
            return
        ids = {canon_id(r["id"]) for r in self.tgt.execute(
            "SELECT id FROM loa_records").fetchall()}
        numbers = {str(r["loa_po_number"]) for r in self.tgt.execute(
            "SELECT loa_po_number FROM loa_records").fetchall()}
        users = self.user_ids() | set(self.planned_users)
        for row in rows:
            loa_id = canon_id(row["id"])
            number = str(row["loa_po_number"])
            if loa_id in ids or number in numbers:
                rep.existing += 1
                continue
            created_by = canon_id(row["created_by"]) if row["created_by"] else None
            if created_by is not None and created_by not in users:
                self.note(f"loa_records {number}: created_by {created_by} is not an "
                          "imported user; stored NULL")
                created_by = None
            self.insert(rep, LOA_SQL, (loa_id, row["loa_po_number"],
                                       row["description"], created_by,
                                       require_ts(row["created_at"],
                                                  f"loa_records {loa_id}.created_at"),
                                       require_ts(row["updated_at"],
                                                  f"loa_records {loa_id}.updated_at")))
            ids.add(loa_id)
            numbers.add(number)

    def plan_bg(self) -> None:
        rep = self.report("bg_records")
        rep.target_before = self.target_count("bg_records")
        rows = self.rows("bg_records")
        rep.source = len(rows)
        if not rows:
            rep.notes.append("table missing or empty in the source")
            return
        ids = {canon_id(r["id"]) for r in self.tgt.execute(
            "SELECT id FROM bg_records").fetchall()}
        numbers = {str(r["bg_no"]) for r in self.tgt.execute(
            "SELECT bg_no FROM bg_records").fetchall()}
        for row in rows:
            bg_id, bg_no = canon_id(row["id"]), str(row["bg_no"])
            if bg_id in ids or bg_no in numbers:
                rep.existing += 1
                continue
            values, fks = self.resolve_bg(row)
            self.insert(rep, BG_SQL, tuple(values[c] for c in BG_COLUMNS))
            ids.add(bg_id)
            numbers.add(bg_no)
            self.bg_rows.append((values, fks))

    def resolve_bg(self, row: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Resolved]]:
        """Map one bg_records row onto the canonical master IDs."""
        bg_no = str(row["bg_no"])
        project = self.resolve_project(row, bg_no)
        fks: dict[str, Resolved] = {
            "company_id": self.resolve_company(row, bg_no),
            "project_id": project,
            "toll_plaza_id": self.resolve_plaza(row, bg_no, project),
            "nature_id": self.resolve_nature(row, bg_no),
            "contractor_id": self.resolve_registry("contractor", row, bg_no),
            "bank_id": self.resolve_registry("bank", row, bg_no),
            "loa_id": self.resolve_loa(row, bg_no),
            "bg_type": self.resolve_code("bg_type", row["bg_type"], bg_no),
            "status": self.resolve_code("bg_status", row["status"], bg_no),
        }
        # Legacy Excel "Yes" is not a date: the flag lives in the status.
        extended, status = self.extended_date(row, str(fks["status"].value), bg_no)
        fks["status"] = self.resolve_code("bg_status", status, bg_no)

        created_by = canon_id(row["created_by"]) if row["created_by"] else None
        known_users = self.user_ids() | set(self.planned_users)
        if created_by is not None and created_by not in known_users:
            self.note(f"bg_records {bg_no}: created_by {created_by} is not an "
                      "imported user; stored NULL")
            created_by = None

        amount = row["bg_amount"]
        if amount is None or float(amount) <= 0:
            raise MigrationError(
                f"bg_records {bg_no}: bg_amount {amount!r} must be > 0")

        values: dict[str, Any] = {
            "id": canon_id(row["id"]),
            "bg_no": bg_no,
            "company_id": fks["company_id"].value,
            "project_id": fks["project_id"].value,
            "toll_plaza_id": fks["toll_plaza_id"].value,
            "bg_type": fks["bg_type"].value,
            "nature_id": fks["nature_id"].value,
            "contractor_id": fks["contractor_id"].value,
            "bank_id": fks["bank_id"].value,
            "loa_id": fks["loa_id"].value,
            "po_number": row["po_number"],
            "bg_amount": amount,
            "from_date": self.require_date(row["from_date"], f"{bg_no}.from_date"),
            "to_date": self.require_date(row["to_date"], f"{bg_no}.to_date"),
            "claim_period": date_text(row["claim_period"]),
            "extended_date": extended,
            "status": status,
            "remarks": row["remarks"],
            "source_pdf": row["source_pdf"],
            "created_by": created_by,
            "created_at": require_ts(row["created_at"], f"{bg_no}.created_at"),
            "updated_at": require_ts(row["updated_at"], f"{bg_no}.updated_at"),
            "reminder_sent_at": ts_param(row["reminder_sent_at"]),
            "supersedes_bg_id": self.resolve_supersedes(row, bg_no),
        }
        return values, fks

    def resolve_company(self, row: sqlite3.Row, bg_no: str) -> Resolved:
        """Legacy alias -> canonical company name; the alias is never a row."""
        name = self.source_name("company", row["company_id"])
        if not name:
            raise MigrationError(
                f"bg_records {bg_no}: company_id {row['company_id']!r} is not a "
                "company in the source database")
        canonical = COMPANY_ALIAS.get(name, name)
        hit = self.target_value("SELECT id FROM company WHERE name = %s", (canonical,))
        if hit is None and canonical != name:
            hit = self.target_value("SELECT id FROM company WHERE name = %s", (name,))
        if hit is None:
            raise MigrationError(
                f"bg_records {bg_no}: company {name!r} (alias {canonical!r}) is not "
                "in the target company table. Apply V1-V3 and the V2 seed first.")
        detail = f"{name} -> {canonical}" if canonical != name else name
        return Resolved(hit, f"{detail} (id {hit})")

    def resolve_project(self, row: sqlite3.Row, bg_no: str) -> Resolved:
        """Projects are keyed on their code; 'SB' exists in the canonical set."""
        code = self.source_value("project", row["project_id"], "code")
        if not code:
            raise MigrationError(
                f"bg_records {bg_no}: project_id {row['project_id']!r} is not a "
                "project in the source database")
        code = str(code).strip()
        hit = self.target_value("SELECT id FROM project WHERE code = %s", (code,))
        if hit is None:
            raise MigrationError(
                f"bg_records {bg_no}: project code {code!r} is not in the target "
                "project table. Apply V1-V3 and the V2 seed first.")
        return Resolved(hit, f"{code} (id {hit})")

    def resolve_plaza(self, row: sqlite3.Row, bg_no: str,
                      project: Resolved) -> Resolved:
        """One plaza per BG: 'Bhojpuri & Mudipar' becomes 'Bhojpuri'."""
        name = self.source_name("toll_plaza", row["toll_plaza_id"])
        if not name:
            raise MigrationError(
                f"bg_records {bg_no}: toll_plaza_id {row['toll_plaza_id']!r} is not "
                "a toll plaza in the source database")
        canonical = PLAZA_SPELLING.get(name, name)
        hit = self.target_value(
            "SELECT id FROM toll_plaza WHERE project_id = %s AND name = %s",
            (project.value, canonical))
        if hit is None:
            raise MigrationError(
                f"bg_records {bg_no}: toll plaza {name!r} (mapped to {canonical!r}) "
                f"is not seeded under project {project.detail}. Combined plaza "
                "spellings are deliberately not created.")
        detail = f"{name} -> {canonical}" if canonical != name else name
        return Resolved(hit, f"{detail} (project {project.value}, id {hit})")

    def resolve_nature(self, row: sqlite3.Row, bg_no: str) -> Resolved:
        name = self.source_name("nature_of_service", row["nature_id"])
        if not name:
            raise MigrationError(
                f"bg_records {bg_no}: nature_id {row['nature_id']!r} is not a "
                "nature of service in the source database")
        hit = self.target_value(
            "SELECT id FROM nature_of_service WHERE name = %s", (name,))
        if hit is None:
            raise MigrationError(
                f"bg_records {bg_no}: nature of service {name!r} is not in the "
                "target nature_of_service table. Apply V1-V3 and the V2 seed first.")
        return Resolved(hit, f"{name} (id {hit})")

    def resolve_registry(self, table: str, row: sqlite3.Row, bg_no: str) -> Resolved:
        """bank / contractor: resolve by name, create with source='migrated'."""
        source_id = row["contractor_id"] if table == "contractor" else row["bank_id"]
        name = self.source_name(table, source_id)
        if not name:
            raise MigrationError(
                f"bg_records {bg_no}: {table}_id {source_id!r} is not a {table} in "
                "the source database")
        hit = self.target_value(f"SELECT id FROM {table} WHERE name = %s", (name,))
        if hit is not None:
            return Resolved(hit, f"{name} (id {hit})")
        if self.apply:
            columns = ["name"] + (["source"] if "source" in self.columns(table) else [])
            params = [name] + ([MIGRATED_SOURCE] if len(columns) > 1 else [])
            created = self.tgt.execute(insert_sql(table, columns, "name") + " RETURNING id",
                                       tuple(params)).fetchone()
            hit = created["id"] if created else self.target_value(
                f"SELECT id FROM {table} WHERE name = %s", (name,))
            self.created_masters.append(f"{table} {name} (id {hit})")
            return Resolved(hit, f"{name} (created with source='{MIGRATED_SOURCE}', "
                                 f"id {hit})")
        return Resolved(None, f"{name} -> created with source='{MIGRATED_SOURCE}'",
                        pending=True)

    def resolve_loa(self, row: sqlite3.Row, bg_no: str) -> Resolved:
        """LOA is keyed on its id, which loa_records copies verbatim."""
        loa_id = row["loa_id"]
        if not loa_id:
            raise MigrationError(f"bg_records {bg_no}: loa_id is NULL")
        raw_id = str(row["loa_id"])
        loa_id = canon_id(raw_id)
        hit = self.target_value("SELECT id FROM loa_records WHERE id = %s", (loa_id,))
        if hit is None:
            number = self.source_value("loa_records", raw_id, "loa_po_number")
            if not self.apply and number:
                # Dry run: plan_loa has not inserted it yet.
                return Resolved(loa_id, f"{number} (would be copied, id {loa_id})",
                                pending=True)
            raise MigrationError(
                f"bg_records {bg_no}: LOA {loa_id} is not in the target "
                "loa_records table (copy loa_records first).")
        number = self.target_value(
            "SELECT loa_po_number FROM loa_records WHERE id = %s", (hit,))
        return Resolved(canon_id(hit), f"{number} (id {hit})")

    def resolve_code(self, table: str, code: Any, bg_no: str) -> Resolved:
        """bg_type / bg_status lookups, keyed on code."""
        if code is None or str(code).strip() == "":
            raise MigrationError(f"bg_records {bg_no}: {table} is empty")
        text = str(code).strip()
        hit = self.target_value(f"SELECT code FROM {table} WHERE code = %s", (text,))
        if hit is None:
            raise MigrationError(
                f"bg_records {bg_no}: {table} {text!r} is not in the target {table} "
                "table. Apply V1-V3 and the V2 seed first.")
        return Resolved(text, text)

    def resolve_supersedes(self, row: sqlite3.Row, bg_no: str) -> str | None:
        """Extension chain (ST007): the superseded BG must be present."""
        ref = row["supersedes_bg_id"]
        if not ref:
            return None
        ref = canon_id(ref)
        if ref == canon_id(row["id"]):
            self.note(f"bg_records {bg_no}: supersedes_bg_id points at itself; "
                      "stored NULL (CHECK id <> supersedes_bg_id)")
            return None
        hit = self.target_value(
            "SELECT id FROM bg_records WHERE id::text = %s OR bg_no = %s", (ref, ref))
        if hit is None:
            self.note(f"bg_records {bg_no}: supersedes_bg_id {ref} is not present in "
                      "the target; stored NULL")
            return None
        return canon_id(hit)

    def extended_date(self, row: sqlite3.Row, status: str,
                      bg_no: str) -> tuple[str | None, str]:
        """Dates pass through; legacy Excel text becomes a status flag only."""
        raw = row["extended_date"]
        text = date_text(raw)
        if text is not None:
            return text, status
        if raw is None or str(raw).strip() == "":
            return None, status
        if status != "Extended":
            status = "Extended"
            self.note(f"bg_records {bg_no}: extended_date {raw!r} is not a date; "
                      "kept as status='Extended' with a NULL date")
        return None, status

    @staticmethod
    def require_date(value: Any, label: str) -> str:
        text = date_text(value)
        if text is None:
            raise MigrationError(f"{label}: {value!r} is not a YYYY-MM-DD date")
        return text

    def plan_audit(self) -> None:
        rep = self.report("audit_logs")
        rep.target_before = self.target_count("audit_logs")
        rows = self.rows("audit_logs", order="id")
        rep.source = len(rows)
        if not rows:
            rep.notes.append("table missing or empty in the source")
            return
        users = self.user_ids() | set(self.planned_users)
        seen = self.audit_keys()
        for row in rows:
            label = row["id"] if "id" in row.keys() else "?"
            user_id = canon_id(row["user_id"]) if row["user_id"] else None
            if user_id is not None and user_id not in users:
                self.note(f"audit_logs #{label}: user_id {user_id} is not an imported "
                          "user; stored NULL")
                user_id = None
            ts = require_ts(row["ts"], f"audit_logs #{label}.ts")
            key = audit_key(ts, row["action"], user_id, row["bg_no"], row["ip"])
            if seen[key] > 0:
                seen[key] -= 1
                rep.existing += 1
                continue
            bg_id = None
            if row["bg_id"]:
                source_bg = canon_id(row["bg_id"])
                bg_id = self.target_value(
                    "SELECT id FROM bg_records WHERE id::text = %s", (source_bg,))
                if bg_id is None and not self.apply and any(
                        canon_id(v["id"]) == source_bg for v, _ in self.bg_rows):
                    bg_id = source_bg  # would be copied by plan_bg
                elif bg_id is None:
                    self.note(f"audit_logs #{label}: bg_id {row['bg_id']} is not in "
                              "the target; stored NULL")
                bg_id = canon_id(bg_id) if bg_id else None
            self.insert(rep, AUDIT_SQL, (
                ts, user_id, row["action"], bg_id, row["bg_no"],
                self.jsonb(row, "old_data", label),
                self.jsonb(row, "new_data", label),
                row["ip"]))

    def jsonb(self, row: sqlite3.Row, column: str, label: Any) -> Jsonb | None:
        """SQLite JSON text -> psycopg Jsonb for a JSONB column."""
        if column not in row.keys():
            return None
        value = row[column]
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return Jsonb(value)
        text = str(value).strip()
        if not text:
            return None
        try:
            return Jsonb(json.loads(text))
        except ValueError as exc:
            raise MigrationError(
                f"audit_logs #{label}: {column} is not valid JSON text: {exc}") from exc

    # -- verification -------------------------------------------------------

    def validate(self, applied: bool) -> list[str]:
        """Row counts, NULL foreign keys and dangling references."""
        failures: list[str] = []
        for table in COPIED_TABLES:
            rep = self.reports.get(table)
            if rep is None:
                continue
            # Every source row must land, minus the ones deliberately skipped
            # (a username collision, a role missing from the target, ...).
            required = rep.source - rep.skipped
            total = self.target_count(table) if applied else rep.total
            ok = total >= required
            print(f"  [{'OK' if ok else 'FAIL'}] {table}: {total} rows "
                  f"(need >= {required})")
            if not ok:
                failures.append(f"{table}: {total} rows < {required}")
            planned = PLANNED_COUNTS.get(table)
            if planned is not None and rep.source and rep.source != planned:
                print(f"         note: the live file was expected to have "
                      f"{planned} rows, this source has {rep.source}")

        for values, fks in self.bg_rows:
            self.validate_bg(str(values["bg_no"]), values, fks, applied, failures)

        for r in self.tgt.execute(FK_VIOLATION_SQL).fetchall():
            ok = r["n"] == 0
            print(f"  [{'OK' if ok else 'FAIL'}] fk {r['chk']}: {r['n']} violations")
            if not ok:
                failures.append(f"fk violations {r['chk']}: {r['n']}")

        role_users = {str(r["user_id"]) for r in self.tgt.execute(
            "SELECT DISTINCT user_id FROM user_roles").fetchall()} | self.role_users
        orphans = sorted(u for u in self.planned_users if u not in role_users)
        ok = not orphans
        print(f"  [{'OK' if ok else 'FAIL'}] users without a role: {len(orphans)}")
        if not ok:
            failures.append(f"users without a role: {orphans}")

        return failures

    def validate_bg(self, bg_no: str, values: dict[str, Any],
                    fks: dict[str, Resolved], applied: bool,
                    failures: list[str]) -> None:
        """The live BG must resolve every foreign key it depends on."""
        missing = [f"{col} ({f.detail})" for col, f in fks.items()
                   if f.value is None and not f.pending]
        pending = [col for col, f in fks.items() if f.pending]
        if missing:
            failures.append("bg_records %s: unresolved foreign keys %s"
                            % (bg_no, ", ".join(missing)))
        print(f"  [{'OK' if not missing else 'FAIL'}] bg_records {bg_no}: "
              f"{len(fks) - len(missing)}/{len(fks)} foreign keys resolved"
              + (f", unresolved: {', '.join(missing)}" if missing else "")
              + (f", to be created: {', '.join(pending)}" if pending else ""))

        row = self.tgt.execute(BG_FK_SQL, (bg_no,)).fetchone()
        if row is None:
            if applied:
                failures.append(f"bg_records {bg_no} is not in the target")
                print(f"  [FAIL] bg_records {bg_no}: not present after the import")
            else:
                print(f"  [SKIP] bg_records {bg_no}: not in the target yet (dry run)")
            return
        ok = row["violations"] == 0
        print(f"  [{'OK' if ok else 'FAIL'}] bg_records {bg_no}: "
              f"{row['violations']} FK violations | "
              f"company={row['company']} project={row['project']} "
              f"plaza={row['toll_plaza']} nature={row['nature_of_service']} "
              f"contractor={row['contractor']} bank={row['bank']} "
              f"loa={row['loa']} type={row['bg_type']} status={row['status']} "
              f"created_by={row['created_by'] or 'NULL'}")
        if not ok:
            failures.append(f"bg_records {bg_no}: {row['violations']} FK violations")

    # -- printing -----------------------------------------------------------

    def print_plan(self) -> None:
        print("--- copy plan ---------------------------------------------------")
        for table in COPIED_TABLES:
            rep = self.reports.get(table)
            if rep is None:
                print(f"  {table}: not planned")
                continue
            print(f"  {table}")
            print(f"      {rep.source} source rows, {rep.existing} already present, "
                  f"{rep.new} to insert, {rep.skipped} skipped"
                  f" -> {rep.total} rows after the import")
            for note in rep.notes:
                print(f"      . {note}")

        print("")
        print("--- not copied (by design) --------------------------------------")
        for table, reason in SKIPPED_TABLES.items():
            count = f"{len(self.rows(table))} rows" if table in self.tables else "(absent)"
            print(f"  {table:<19} {count:<12} {reason}")

        if self.bg_rows:
            print("")
            for values, fks in self.bg_rows:
                print(f"--- bg_records {values['bg_no']} foreign keys "
                      "---------------------------")
                for column, resolved in fks.items():
                    flag = " (would be created)" if resolved.pending else ""
                    print(f"  {column:<16} {resolved.detail}{flag}")
                print(f"  {'created_by':<16} "
                      f"{values['created_by'] or 'NULL (no matching user)'}")
                print(f"  {'amount':<16} {values['bg_amount']} {values['bg_type']} "
                      f"{values['from_date']} -> {values['to_date']} "
                      f"status {values['status']}")

    def print_summary(self, applied: bool) -> None:
        if self.notes:
            print("")
            print("--- warnings ---------------------------------------------------")
            for note in self.notes:
                print(f"  [WARN] {note}")
        print("")
        if self.created_masters:
            print(f"masters created: {'; '.join(self.created_masters)}")
        print("APPLIED - committed in one transaction." if applied
              else "DRY RUN - nothing was written. Re-run with --apply to copy.")


# --- entry point -----------------------------------------------------------


def run(source: Path, apply: bool) -> list[str]:
    """Plan (and optionally apply) the migration; returns validation failures."""
    src = open_source(source)
    stamp = (source.stat().st_size, source.stat().st_mtime_ns)
    print(f"source: {source} (read-only)")
    print(f"target: {target_label()}")
    print(f"mode:   {'APPLY (one transaction)' if apply else 'DRY RUN (no changes)'}")

    try:
        with psycopg.connect(**conn_params(), autocommit=True,
                             row_factory=dict_row) as conn:
            importer = Importer(src, conn, apply=apply)
            importer.preflight()

            counts = {table: importer.target_count(table)
                      for table in MASTER_MINIMUMS}
            print("canonical masters in the target: " + ", ".join(
                f"{table} {counts[table]} (min {minimum})"
                for table, minimum in MASTER_MINIMUMS.items()))
            for table, minimum in MASTER_MINIMUMS.items():
                if counts[table] < minimum:
                    importer.note(f"target {table} has {counts[table]} rows "
                                  f"(min {minimum}); run python db/apply.py")

            if apply:
                try:
                    with conn.transaction():  # one transaction: commit at the end
                        importer.plan_all()
                        failures = importer.validate(applied=True)
                        if failures:
                            raise MigrationError("validation failed: "
                                                 + "; ".join(failures))
                except MigrationError:
                    print("ROLLED BACK - no changes were committed.")
                    raise
            else:
                importer.plan_all()
                failures = importer.validate(applied=False)

            importer.print_plan()

            unchanged = stamp == (source.stat().st_size, source.stat().st_mtime_ns)
            print("")
            print(f"  [{'OK' if unchanged else 'FAIL'}] source file unchanged: "
                  f"{unchanged}")
            if not unchanged:
                failures.append("the SQLite source file was modified")

        importer.print_summary(applied=apply)
        return failures
    finally:
        src.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-shot SQLite -> PostgreSQL data import "
                    "(dry run by default; --apply writes).")
    parser.add_argument("--source", required=True,
                        help="path to the SQLite database (opened read-only)")
    parser.add_argument("--apply", action="store_true",
                        help="perform the import in one transaction "
                             "(default: dry run)")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        print(f"ERROR: source database not found: {source}", file=sys.stderr)
        return 2
    try:
        failures = run(source, apply=args.apply)
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        print(f"ERROR: cannot reach Postgres: {exc}", file=sys.stderr)
        print("Hint: docker compose -f db/docker-compose.yml up -d", file=sys.stderr)
        return 1
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
