"""Regression: db/import_sqlite.py copies the legacy SQLite data into PostgreSQL.

The importer is a one-shot migration. It reads a legacy SQLite file, resolves
every foreign key against the canonical masters (the company alias ``NSPPL`` ->
``NHIT Southern Projects Private Limited``, the combined plaza spelling
``Bhojpuri & Mudipar`` -> ``Bhojpuri`` without seeding the combined spelling, a
missing bank/contractor created with ``source = 'migrated'``) and inserts the
referenced rows. Two behaviours are easy to break and cost real data:

* the three identical legacy audit rows must all be copied - they are three
  logins, not one row that happens to be listed three times;
* a second import must insert nothing: the idempotency check compares the
  source ``user_id`` text with the target ``uuid`` column, so both sides have to
  be normalised to the same canonical spelling before they are compared.

Run from the repo root:

    $env:PYTHONPATH = "backend;backend\tests"
    $env:BG_TEST_DATABASE_URL = "postgresql://bg_app:bg_app_local_pw@127.0.0.1:5433/bg_automation"
    .venv\\Scripts\\python.exe -m unittest test_import_sqlite -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from bgauto import config
from support import TempDatabase

# db/import_sqlite.py is a script, not a package module: it is imported the way
# db/import_sqlite.py itself imports db/apply.py, with <repo>/db on sys.path.
DB_DIR = Path(__file__).resolve().parents[2] / "db"
if str(DB_DIR) not in sys.path:
    sys.path.insert(0, str(DB_DIR))

import import_sqlite  # noqa: E402  (needs DB_DIR on sys.path)

# Only the tables the importer reads, with the columns it reads.
FIXTURE_DDL = """
CREATE TABLE users (
  id              TEXT PRIMARY KEY,
  username        TEXT NOT NULL,
  email           TEXT,
  full_name       TEXT,
  password_hash   TEXT NOT NULL,
  is_active       INTEGER NOT NULL,
  last_login      TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  failed_attempts INTEGER,
  locked_until    TEXT
);
CREATE TABLE roles (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE user_roles (
  user_id TEXT NOT NULL,
  role_id INTEGER NOT NULL,
  PRIMARY KEY (user_id, role_id)
);
CREATE TABLE company (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE project (
  id   INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  name TEXT
);
CREATE TABLE toll_plaza (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER,
  name       TEXT NOT NULL
);
CREATE TABLE nature_of_service (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE bank (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE contractor (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE bg_type (
  code TEXT PRIMARY KEY
);
CREATE TABLE bg_status (
  code TEXT PRIMARY KEY
);
CREATE TABLE loa_records (
  id            TEXT PRIMARY KEY,
  loa_po_number TEXT NOT NULL,
  description   TEXT,
  created_by    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE TABLE bg_records (
  id               TEXT PRIMARY KEY,
  bg_no            TEXT NOT NULL,
  company_id       INTEGER,
  project_id       INTEGER,
  toll_plaza_id    INTEGER,
  bg_type          TEXT,
  nature_id        INTEGER,
  contractor_id    INTEGER,
  bank_id          INTEGER,
  loa_id           TEXT,
  po_number        TEXT,
  bg_amount        REAL,
  from_date        TEXT,
  to_date          TEXT,
  claim_period     TEXT,
  extended_date    TEXT,
  status           TEXT,
  remarks          TEXT,
  source_pdf       TEXT,
  created_by       TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  reminder_sent_at TEXT,
  supersedes_bg_id TEXT
);
CREATE TABLE audit_logs (
  id       INTEGER PRIMARY KEY,
  ts       TEXT NOT NULL,
  user_id  TEXT,
  action   TEXT NOT NULL,
  bg_id    TEXT,
  bg_no    TEXT,
  old_data TEXT,
  new_data TEXT,
  ip       TEXT
);
"""

# 32-hex ids, the spelling the live SQLite file stores (no hyphens).
ADMIN_ID = "302cc6b32e9f4a1c8d5e6f70a1b2c3d4"
VIEWER_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
LOA_ID = "11223344556677889900aabbccddeeff"
BG_ID = "ffeeddccbbaa99887766554433221100"

BG_NO = "0024NDLG00226526"
LOA_NUMBER = "NSPPL/TMS/2025/01"

COMPANY_ALIAS = "NSPPL"
CANONICAL_COMPANY = "NHIT Southern Projects Private Limited"
PROJECT_CODE = "SB"
PLAZA_LEGACY = "Bhojpuri & Mudipar"
PLAZA_CANONICAL = "Bhojpuri"
NATURE = "Manpower"
BANK_NAME = "ICICI Bank Limited"
CONTRACTOR_NAME = "ARYA OMNITALK WIRELESS SOLUTIONS PVT LTD"

# The three login rows that share one identity: the import must copy all three.
DUPLICATE_TS = "2025-02-01 10:00:00"

UPLOAD_NEW_DATA = '{"bg_no": "0024NDLG00226526", "status": "Draft"}'
CONFIRM_OLD_DATA = '{"status": "Draft"}'
CONFIRM_NEW_DATA = '{"status": "Active"}'

COPIED_TABLES = ("users", "user_roles", "loa_records", "bg_records", "audit_logs")


def build_fixture(path: Path) -> None:
    """Write the legacy SQLite database the importer is expected to migrate."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(FIXTURE_DDL)
        conn.executemany(
            "INSERT INTO users (id, username, email, full_name, password_hash,"
            " is_active, last_login, created_at, updated_at, failed_attempts,"
            " locked_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (ADMIN_ID, "admin", "admin@example.test", "A Team",
                 "scrypt$32768$8$1$c2FsdHNhbHQ=$ZHVtbXloYXNo", 1,
                 "2025-02-01 10:00:00", "2025-01-10 08:15:00",
                 "2025-02-01 10:00:00", 0, None),
                (VIEWER_ID, "viewer", "viewer@example.test", "V Iewer",
                 "scrypt$32768$8$1$c2FsdHNhbHQ=$ZHVtbXloYXNo", 0,
                 None, "2025-01-11 09:20:00", "2025-01-11 09:20:00", 3,
                 "2025-01-11 09:25:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO roles (id, name) VALUES (?, ?)",
            [(1, "admin"), (2, "uploader"), (3, "viewer")],
        )
        conn.executemany(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            [(ADMIN_ID, 1), (VIEWER_ID, 3)],
        )
        # Legacy masters: the alias row, one project, one combined plaza, ...
        conn.executemany(
            "INSERT INTO company (id, name) VALUES (?, ?)",
            [(1, COMPANY_ALIAS)],
        )
        conn.executemany(
            "INSERT INTO project (id, code, name) VALUES (?, ?, ?)",
            [(1, PROJECT_CODE, "Simga-Bilaspur")],
        )
        conn.executemany(
            "INSERT INTO toll_plaza (id, project_id, name) VALUES (?, ?, ?)",
            [(1, 1, PLAZA_LEGACY)],
        )
        conn.executemany(
            "INSERT INTO nature_of_service (id, name) VALUES (?, ?)",
            [(1, NATURE)],
        )
        conn.executemany(
            "INSERT INTO bank (id, name) VALUES (?, ?)", [(1, BANK_NAME)],
        )
        conn.executemany(
            "INSERT INTO contractor (id, name) VALUES (?, ?)",
            [(1, CONTRACTOR_NAME)],
        )
        conn.executemany(
            "INSERT INTO bg_type (code) VALUES (?)", [("PBG",), ("ABG",)],
        )
        conn.executemany(
            "INSERT INTO bg_status (code) VALUES (?)",
            [("Draft",), ("Active",), ("Expiring",), ("Expired",),
             ("Extended",), ("Closed",)],
        )
        conn.execute(
            "INSERT INTO loa_records (id, loa_po_number, description,"
            " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (LOA_ID, LOA_NUMBER, "TMS manpower LOA", ADMIN_ID,
             "2025-01-12 11:00:00", "2025-01-12 11:00:00"),
        )
        conn.execute(
            "INSERT INTO bg_records (id, bg_no, company_id, project_id,"
            " toll_plaza_id, bg_type, nature_id, contractor_id, bank_id, loa_id,"
            " po_number, bg_amount, from_date, to_date, claim_period,"
            " extended_date, status, remarks, source_pdf, created_by,"
            " created_at, updated_at, reminder_sent_at, supersedes_bg_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            "         ?, ?, ?, ?, ?)",
            (BG_ID, BG_NO, 1, 1, 1, "PBG", 1, 1, 1, LOA_ID,
             "PO/TMS/2025/01", 1234567.89, "2025-01-15", "2025-12-31",
             "2026-03-31", None, "Active", "Migrated from the legacy register",
             "BG 0024NDLG00226526.pdf", ADMIN_ID, "2025-01-16 09:30:00",
             "2025-01-16 09:30:00", None, None),
        )
        conn.executemany(
            "INSERT INTO audit_logs (id, ts, user_id, action, bg_id, bg_no,"
            " old_data, new_data, ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "2025-01-16 09:35:00", ADMIN_ID, "upload", BG_ID, BG_NO,
                 None, UPLOAD_NEW_DATA, "10.0.0.5"),
                (2, "2025-01-16 09:40:00", VIEWER_ID, "confirm", BG_ID, BG_NO,
                 CONFIRM_OLD_DATA, CONFIRM_NEW_DATA, "10.0.0.5"),
                # One identity, three rows: all three must survive the import.
                (3, DUPLICATE_TS, ADMIN_ID, "login", BG_ID, BG_NO, None, None,
                 "10.1.1.10"),
                (4, DUPLICATE_TS, ADMIN_ID, "login", BG_ID, BG_NO, None, None,
                 "10.1.1.10"),
                (5, DUPLICATE_TS, ADMIN_ID, "login", BG_ID, BG_NO, None, None,
                 "10.1.1.10"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class ImportSqliteTests(TempDatabase):
    """The one-shot SQLite -> PostgreSQL importer against a fresh schema."""

    def setUp(self):
        super().setUp()
        # TempDatabase drops the schema; the SQLite file is ours to clean up.
        self._fixture_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._fixture_dir.cleanup)
        self.sqlite_path = Path(self._fixture_dir.name) / "legacy.sqlite3"
        build_fixture(self.sqlite_path)

    # -- helpers ------------------------------------------------------------

    def _connect(self):
        """A plain connection to the test schema (not the app's pool)."""
        return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)

    def _target_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            return {table: self._count(conn, table) for table in COPIED_TABLES}

    @staticmethod
    def _count(conn, table: str) -> int:
        return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]

    def _import(self, apply: bool) -> tuple[import_sqlite.Importer, list[str]]:
        """Plan (and optionally apply) the import; run() is never called."""
        source = import_sqlite.open_source(self.sqlite_path)
        self.addCleanup(source.close)
        failures: list[str] = []
        with self._connect() as conn:
            with conn.transaction():  # one transaction: commit at the end
                # preflight() pins the session to UTC, the reading the legacy
                # SQLite timestamps were written in.
                importer = import_sqlite.Importer(source, conn, apply=apply)
                importer.preflight()
                importer.plan_all()
                if not apply:
                    failures = importer.validate(applied=False)
        return importer, failures

    # -- the dry run --------------------------------------------------------

    def test_dry_run_plans_everything_and_writes_nothing(self):
        importer, failures = self._import(apply=False)

        # A smaller database is not a failure: every source row is planned and
        # every structural check (foreign keys, resolved masters) is clean.
        self.assertEqual(failures, [])

        reports = importer.reports
        self.assertEqual(
            {table: reports[table].new for table in COPIED_TABLES},
            {"users": 2, "user_roles": 2, "loa_records": 1, "bg_records": 1,
             "audit_logs": 5},
        )
        self.assertEqual({table: reports[table].source for table in COPIED_TABLES},
                         {"users": 2, "user_roles": 2, "loa_records": 1,
                          "bg_records": 1, "audit_logs": 5})
        # A dry run writes nothing, not even the migrated bank/contractor.
        self.assertEqual(self._target_counts(),
                         dict.fromkeys(COPIED_TABLES, 0))
        with self._connect() as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) AS n FROM bank").fetchone()["n"], 0)
            self.assertEqual(
                conn.execute("SELECT count(*) AS n FROM contractor").fetchone()["n"], 0)

    # -- the real import ----------------------------------------------------

    def test_apply_copies_every_row_and_resolves_the_masters(self):
        self._import(apply=True)

        counts = self._target_counts()
        self.assertEqual(counts, {"users": 2, "user_roles": 2, "loa_records": 1,
                                  "bg_records": 1, "audit_logs": 5})

        with self._connect() as conn:
            users = {row["username"]: row for row in conn.execute(
                "SELECT id::text AS id, username, is_active, failed_attempts,"
                " locked_until, password_hash FROM users").fetchall()}
            self.assertEqual(sorted(users), ["admin", "viewer"])
            # 32-hex text became UUID columns; both spellings must be the same.
            self.assertEqual(users["admin"]["id"], str(uuid.UUID(ADMIN_ID)))
            self.assertEqual(users["viewer"]["id"], str(uuid.UUID(VIEWER_ID)))
            self.assertTrue(users["admin"]["is_active"])
            self.assertFalse(users["viewer"]["is_active"])
            self.assertEqual(users["admin"]["failed_attempts"], 0)
            self.assertEqual(users["viewer"]["failed_attempts"], 3)
            self.assertIsNone(users["admin"]["locked_until"])
            self.assertIsNotNone(users["viewer"]["locked_until"])
            self.assertTrue(users["admin"]["password_hash"].startswith("scrypt$"))

            roles = sorted(row["name"] for row in conn.execute(
                "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
            ).fetchall())
            self.assertEqual(roles, ["admin", "viewer"])

            bg = conn.execute(
                "SELECT c.name AS company, p.code AS project, t.name AS plaza,"
                " n.name AS nature, bk.name AS bank, ct.name AS contractor,"
                " l.loa_po_number AS loa, b.bg_type, b.status, b.bg_amount,"
                " b.from_date, b.to_date, b.po_number, u.username AS created_by"
                " FROM bg_records b"
                " LEFT JOIN company c            ON c.id  = b.company_id"
                " LEFT JOIN project p            ON p.id  = b.project_id"
                " LEFT JOIN toll_plaza t         ON t.id  = b.toll_plaza_id"
                " LEFT JOIN nature_of_service n  ON n.id  = b.nature_id"
                " LEFT JOIN bank bk              ON bk.id = b.bank_id"
                " LEFT JOIN contractor ct        ON ct.id = b.contractor_id"
                " LEFT JOIN loa_records l        ON l.id  = b.loa_id"
                " LEFT JOIN users u              ON u.id  = b.created_by"
                " WHERE b.bg_no = %s", (BG_NO,)).fetchone()
            self.assertEqual(bg["company"], CANONICAL_COMPANY)
            self.assertEqual(bg["project"], PROJECT_CODE)
            self.assertEqual(bg["plaza"], PLAZA_CANONICAL)
            self.assertEqual(bg["nature"], NATURE)
            self.assertEqual(bg["bg_type"], "PBG")
            self.assertEqual(bg["status"], "Active")
            self.assertEqual(bg["loa"], LOA_NUMBER)
            self.assertEqual(bg["created_by"], "admin")
            self.assertEqual(bg["from_date"].isoformat(), "2025-01-15")
            self.assertEqual(bg["to_date"].isoformat(), "2025-12-31")

            # The combined spelling is resolved, never seeded (UC003/MD-006).
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM toll_plaza WHERE name = %s",
                (PLAZA_LEGACY,)).fetchone()["n"], 0)
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM toll_plaza").fetchone()["n"], 46)
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM company WHERE name = %s",
                (COMPANY_ALIAS,)).fetchone()["n"], 0)

            for table, name in (("bank", BANK_NAME), ("contractor", CONTRACTOR_NAME)):
                self.assertEqual(
                    conn.execute(f"SELECT name, source FROM {table}"
                                 " WHERE name = %s", (name,)).fetchone()["source"],
                    "migrated",
                    f"{table} {name!r} must carry source='migrated'")

            audits = conn.execute(
                "SELECT id, ts, user_id::text AS user_id, action, bg_no,"
                " jsonb_typeof(old_data) AS old_kind,"
                " jsonb_typeof(new_data) AS new_kind, ip"
                " FROM audit_logs ORDER BY id").fetchall()
            self.assertEqual(len(audits), 5)
            # The UUID the audit rows point at must not have been lost (V3
            # canonicalisation regression: user_id was compared as text).
            self.assertTrue(all(row["user_id"] for row in audits))
            self.assertEqual({row["user_id"] for row in audits},
                             {str(uuid.UUID(ADMIN_ID)),
                              str(uuid.UUID(VIEWER_ID))})
            # All three identical login rows are present, none was deduped.
            logins = [row for row in audits if row["action"] == "login"]
            self.assertEqual(len(logins), 3)
            self.assertTrue(all(row["ts"] == logins[0]["ts"] for row in logins))
            self.assertEqual(logins[0]["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                             DUPLICATE_TS)
            for row in logins:
                self.assertEqual(row["bg_no"], BG_NO)
                self.assertIsNotNone(row["ts"])
                self.assertEqual(row["ip"], "10.1.1.10")

            # SQLite JSON text became JSONB objects, NULLs stayed NULL.
            self.assertEqual([row["new_kind"] for row in audits[:1]], ["object"])
            self.assertEqual([row["old_kind"] for row in audits[1:2]], ["object"])
            self.assertEqual([row["new_kind"] for row in audits[1:2]], ["object"])
            self.assertEqual([row["old_kind"] for row in audits[2:]], [None] * 3)
            self.assertEqual([row["new_kind"] for row in audits[2:]], [None] * 3)
            self.assertEqual(
                conn.execute("SELECT old_data->>'status' AS s FROM audit_logs"
                             " WHERE action = 'confirm'").fetchone()["s"], "Draft")
            self.assertEqual(
                conn.execute("SELECT new_data->>'bg_no' AS n FROM audit_logs"
                             " WHERE action = 'upload'").fetchone()["n"], BG_NO)

    def test_second_apply_inserts_nothing_new(self):
        self._import(apply=True)
        before = self._target_counts()

        # The second import must be a no-op: the source user_id text and the
        # target uuid column compare equal, and the three identical audit rows
        # are not mistaken for three new ones.
        second, _ = self._import(apply=True)
        self.assertEqual(
            {table: second.reports[table].new for table in COPIED_TABLES},
            dict.fromkeys(COPIED_TABLES, 0),
        )
        self.assertEqual(
            {table: second.reports[table].existing for table in COPIED_TABLES},
            {"users": 2, "user_roles": 2, "loa_records": 1, "bg_records": 1,
             "audit_logs": 5},
        )
        self.assertEqual(self._target_counts(), before)

        with self._connect() as conn:
            # Nothing was duplicated: one bank, one contractor, one plaza.
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM bank WHERE name = %s",
                (BANK_NAME,)).fetchone()["n"], 1)
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM contractor WHERE name = %s",
                (CONTRACTOR_NAME,)).fetchone()["n"], 1)
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM audit_logs WHERE action = 'login'"
            ).fetchone()["n"], 3)


if __name__ == "__main__":
    unittest.main()
