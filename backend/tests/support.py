"""Shared test fixture: a throwaway database, output folder and configuration."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bgauto import config


class TempDatabase(unittest.TestCase):
    """Point ``config`` at a temp database and output directory for one test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "app.sqlite3"
        schema_sql = Path(__file__).resolve().parents[2] / "db" / "sqlite" / "schema.sql"
        conn = sqlite3.connect(self.db)
        conn.executescript(schema_sql.read_text(encoding="utf-8"))
        conn.close()

        self._saved = {
            name: getattr(config, name)
            for name in (
                "DB_PATH", "OUT_DIR", "RUNS_DIR", "MAIL_DIR", "WORKBOOK_PATH",
                "TRACKER_PATH", "DATA_DIR", "SCHEDULER_ENABLED", "MAIL_DRY_RUN",
                "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REMINDER_DAYS",
                "BOOTSTRAP_ADMIN_USER", "BOOTSTRAP_ADMIN_PASS", "BOOTSTRAP_ADMIN_EMAIL",
                "SESSION_MINUTES", "FINANCE_DL", "USER_TEAM_DL", "APP_URL",
            )
        }
        config.DB_PATH = self.db
        config.OUT_DIR = self.root / "out"
        config.RUNS_DIR = config.OUT_DIR / "web"
        config.MAIL_DIR = config.OUT_DIR / "mail"
        config.WORKBOOK_PATH = config.OUT_DIR / "BG Tracker_ingested.xlsx"
        config.DATA_DIR = self.root / "data"
        config.TRACKER_PATH = Path(__file__).resolve().parents[2] / "BG Tracker.xlsx"
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
        for name, value in self._saved.items():
            setattr(config, name, value)

    def bootstrap_admin(self):
        from bgauto import auth, store

        with store.connect() as conn:
            return auth.ensure_bootstrap_admin(conn)
