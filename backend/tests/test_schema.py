"""A new BG_DB must start from the approved schema, not an empty file."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bgauto import schema


class FreshDatabase(unittest.TestCase):
    def test_empty_database_gets_the_baseline_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fresh.sqlite3"
            applied = schema.ensure_schema(db)
            self.assertIn("schema:baseline", applied)

            conn = sqlite3.connect(db)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {"users", "roles", "bg_records", "sessions", "reminders",
                     "audit_logs"} <= tables
                )
                user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
                self.assertIn("failed_attempts", user_columns)
                self.assertIn("locked_until", user_columns)
                bg_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(bg_records)")
                }
                self.assertIn("updated_at", bg_columns)
            finally:
                conn.close()

            again = schema.ensure_schema(db)
            self.assertFalse([item for item in again if item.startswith("schema:baseline")])

    def test_existing_schema_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "existing.sqlite3"
            schema.ensure_schema(db)
            first = schema.ensure_schema(db)
            self.assertFalse([item for item in first if item.startswith("schema:baseline")])


if __name__ == "__main__":
    unittest.main()
