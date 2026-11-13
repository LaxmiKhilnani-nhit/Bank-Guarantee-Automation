"""The migrated schema must contain everything the backend reads."""
from __future__ import annotations

import unittest

from bgauto import store
from support import TempDatabase


class SchemaParity(TempDatabase):
    def test_required_tables_and_columns_are_present(self):
        with store.connect() as conn:
            self.assertEqual(store.schema_problems(conn), [])

    def test_v3_columns_have_the_expected_types(self):
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND (table_name, column_name) IN ("
                "  ('audit_logs', 'ip'), ('lookup_cache', 'payload'),"
                "  ('bg_status', 'sort_order'), ('users', 'failed_attempts'),"
                "  ('reminders', 'recipients')"
                ")"
            ).fetchall()
        types = {(row["table_name"], row["column_name"]): row["data_type"] for row in rows}
        self.assertEqual(types[("audit_logs", "ip")], "text")
        self.assertEqual(types[("lookup_cache", "payload")], "jsonb")
        self.assertEqual(types[("bg_status", "sort_order")], "integer")
        self.assertEqual(types[("users", "failed_attempts")], "integer")
        self.assertEqual(types[("reminders", "recipients")], "ARRAY")

    def test_canonical_masters_are_seeded(self):
        with store.connect() as conn:
            counts = {
                table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                for table in ("company", "project", "toll_plaza", "nature_of_service")
            }
        self.assertEqual(counts["company"], 3)
        self.assertEqual(counts["project"], 28)
        self.assertEqual(counts["toll_plaza"], 46)
        self.assertGreaterEqual(counts["nature_of_service"], 27)


if __name__ == "__main__":
    unittest.main()
