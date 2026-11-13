"""Shared test fixture: an isolated PostgreSQL schema for one test.

Every test gets its own schema in a disposable database, created from the real
migrations (V1-V3) and dropped afterwards. The application connects through a
``DATABASE_URL`` whose ``search_path`` is pinned to that schema, so the code
under test never sees another test's rows.

Point the suite at a disposable database with ``BG_TEST_DATABASE_URL`` (or
``DATABASE_URL``), e.g.::

    $env:BG_TEST_DATABASE_URL = "postgresql://bg_app:bg_app_local_pw@127.0.0.1:5433/bg_automation"
    $env:PYTHONPATH = "backend"
    .venv\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from bgauto import config

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted((REPO_ROOT / "db" / "migrations").glob("V*.sql"))


def _base_url() -> str:
    url = (
        os.environ.get("BG_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or config.DATABASE_URL
        or ""
    ).strip()
    if not url:
        raise unittest.SkipTest(
            "No PostgreSQL test database configured. Set BG_TEST_DATABASE_URL "
            "(or DATABASE_URL) to a disposable database."
        )
    return url


def _schema_url(base: str, schema: str) -> str:
    params = conninfo_to_dict(base)
    params["options"] = f"-c search_path={schema},public"
    return make_conninfo(**params)


def _apply_migrations(url: str, canonical_seed: bool) -> None:
    with psycopg.connect(url) as conn:
        for path in MIGRATIONS:
            if not canonical_seed and path.name.startswith("V2__"):
                continue
            conn.execute(path.read_text(encoding="utf-8"))
        conn.commit()


class TempDatabase(unittest.TestCase):
    """Isolate one test in a fresh schema and a temp output directory."""

    canonical_seed = True

    def setUp(self) -> None:
        from bgauto import store

        self.base_url = _base_url()
        self._saved = {
            name: getattr(config, name)
            for name in (
                "DATABASE_URL", "OUT_DIR", "RUNS_DIR", "MAIL_DIR", "WORKBOOK_PATH",
                "TRACKER_PATH", "DATA_DIR", "SCHEDULER_ENABLED", "MAIL_DRY_RUN",
                "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REMINDER_DAYS",
                "BOOTSTRAP_ADMIN_USER", "BOOTSTRAP_ADMIN_PASS", "BOOTSTRAP_ADMIN_EMAIL",
                "SESSION_MINUTES", "FINANCE_DL", "USER_TEAM_DL", "APP_URL",
            )
        }
        self.schema = f"test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.base_url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{self.schema}"')
        store.reset_pool()
        config.DATABASE_URL = _schema_url(self.base_url, self.schema)
        _apply_migrations(config.DATABASE_URL, self.canonical_seed)
        if self.canonical_seed:
            # V3 adds the master metadata columns; the canonical values come from
            # docs/master-data.md through the same importer production uses.
            from bgauto.masterdata import import_master_data

            import_master_data(config.MASTER_DATA_PATH)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        config.OUT_DIR = self.root / "out"
        config.RUNS_DIR = config.OUT_DIR / "web"
        config.MAIL_DIR = config.OUT_DIR / "mail"
        config.WORKBOOK_PATH = config.OUT_DIR / "BG Tracker_ingested.xlsx"
        config.DATA_DIR = self.root / "data"
        config.TRACKER_PATH = REPO_ROOT / "BG Tracker.xlsx"
        config.SCHEDULER_ENABLED = False
        config.MAIL_DRY_RUN = True
        config.SMTP_HOST = ""
        config.SMTP_PORT = 587
        config.SMTP_USER = ""
        config.SMTP_PASS = ""
        config.REMINDER_DAYS = 7
        config.BOOTSTRAP_ADMIN_USER = "admin"
        config.BOOTSTRAP_ADMIN_PASS = "Admin#12345"
        config.BOOTSTRAP_ADMIN_EMAIL = ""
        config.SESSION_MINUTES = 30
        config.FINANCE_DL = ["finance@example.test"]
        config.USER_TEAM_DL = ["users@example.test"]
        config.APP_URL = "http://127.0.0.1:8000"

        from bgauto.web import runs

        runs._masters_cache = None

    def tearDown(self) -> None:
        from bgauto import store

        store.reset_pool()
        for name, value in self._saved.items():
            setattr(config, name, value)
        try:
            with psycopg.connect(self.base_url, autocommit=True) as conn:
                conn.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        except psycopg.Error:
            pass

    def bootstrap_admin(self):
        from bgauto import auth, store

        with store.connect() as conn:
            return auth.ensure_bootstrap_admin(conn)
