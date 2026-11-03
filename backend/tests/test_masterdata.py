"""Master-data parsing and import: the project/plaza lists behind the dropdowns.

The markdown below mirrors the shape of the operations master document, including
its escaped numbering and the "Toll Mananger" typo. No real staff names are used.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bgauto.masterdata import import_master_data, parse_master_markdown
from bgauto.masters import Masters
from bgauto.schema import ensure_schema

DOC = """\
### Example Eastern Projects Private Limited

##### 1\\. Alpha-Beta \\[AB]

State: Assam

Round: 3

Project Manager: A Person

No. of Plaza: 2

Plaza Names:

a. First Plaza \\[62.6]

Toll Mananger: One

Maintenance Manager: Two

Corridor Manager: Three

b. Second Plaza \\[51.5]

Toll Mananger: Four

##### 2\\. Epsilon-Zeta \\[EZ]

State: West Bengal

Round: 3

No. of Plaza: 0

Plaza Names:

### Example Western Projects Private Limited

##### 1\\. Gamma-Delta \\[GD]

State: Gujarat

Round: 1

No. of Plaza: 1

Plaza Names:

a. Third Plaza \\[31]

## Head Office Departments

| 1 | Admin | ADMIN |
"""

DOC_WITHOUT_AB = """\
### Example Western Projects Private Limited

##### 1\\. Gamma-Delta \\[GD]

State: Gujarat

Round: 1

No. of Plaza: 1

Plaza Names:

a. Third Plaza \\[31]
"""


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_master_markdown(DOC)

    def test_companies_and_projects(self):
        self.assertEqual(
            [c.name for c in self.parsed.companies],
            ["Example Eastern Projects Private Limited", "Example Western Projects Private Limited"],
        )
        eastern = self.parsed.companies[0]
        self.assertEqual([p.code for p in eastern.projects], ["AB", "EZ"])
        self.assertEqual(eastern.projects[0].name, "Alpha-Beta")
        self.assertEqual(eastern.projects[0].state, "Assam")
        self.assertEqual(eastern.projects[0].round, 3)
        self.assertEqual(eastern.projects[0].project_manager, "A Person")

    def test_plazas_and_chainage(self):
        project = self.parsed.companies[0].projects[0]
        self.assertEqual([p.name for p in project.plazas], ["First Plaza", "Second Plaza"])
        self.assertEqual([p.chainage for p in project.plazas], ["62.6", "51.5"])

    def test_manager_typo_is_tolerated(self):
        first, second = self.parsed.companies[0].projects[0].plazas
        self.assertEqual(first.toll_manager, "One")
        self.assertEqual(first.maintenance_manager, "Two")
        self.assertEqual(first.corridor_manager, "Three")
        self.assertEqual(second.toll_manager, "Four")

    def test_head_office_section_is_ignored(self):
        self.assertEqual(self.parsed.plaza_count(), 3)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "masters.sqlite3"
        schema_sql = Path(__file__).resolve().parents[2] / "db" / "sqlite" / "schema.sql"
        if not schema_sql.exists():
            self.skipTest("db/sqlite/schema.sql not present")
        conn = sqlite3.connect(self.db)
        conn.executescript(schema_sql.read_text(encoding="utf-8"))
        conn.close()
        self.doc = Path(self.tmp.name) / "master-data.md"
        self.doc.write_text(DOC, encoding="utf-8")

    def test_schema_is_already_current(self):
        self.assertEqual(ensure_schema(self.db), [])

    def test_import_creates_and_is_idempotent(self):
        first = import_master_data(self.doc, self.db)
        self.assertEqual(len(first["companies"]["created"]), 2)
        self.assertEqual(sorted(first["projects"]["created"]), ["AB", "EZ", "GD"])
        self.assertEqual(len(first["plazas"]["created"]), 3)

        second = import_master_data(self.doc, self.db)
        for group in ("companies", "projects", "plazas"):
            for key, value in second[group].items():
                self.assertEqual(value, [], f"{group}.{key} changed on re-import")

        masters = Masters.load(self.db)
        self.assertIn("AB", masters.projects)
        self.assertIn(
            {"project": "AB", "name": "First Plaza", "chainage": "62.6"},
            masters.toll_plazas,
        )
        option = next(o for o in masters.project_options if o["code"] == "AB")
        self.assertEqual(option["name"], "Alpha-Beta")
        self.assertEqual(option["company"], "Example Eastern Projects Private Limited")

    def test_missing_projects_are_deactivated_not_deleted(self):
        import_master_data(self.doc, self.db)
        smaller = Path(self.tmp.name) / "smaller.md"
        smaller.write_text(DOC_WITHOUT_AB, encoding="utf-8")
        report = import_master_data(smaller, self.db)
        self.assertIn("AB", report["projects"]["deactivated"])
        self.assertIn("EZ", report["projects"]["deactivated"])

        masters = Masters.load(self.db)
        self.assertNotIn("AB", masters.projects)
        self.assertIn("GD", masters.projects)
        self.assertNotIn("First Plaza", [p["name"] for p in masters.toll_plazas])

    def test_dry_run_changes_nothing(self):
        import_master_data(self.doc, self.db, dry_run=True)
        masters = Masters.load(self.db)
        self.assertNotIn("AB", masters.projects)


class SchemaMigrationTests(unittest.TestCase):
    def test_old_tables_get_the_new_columns_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.sqlite3"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE company (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE TABLE project (
                  id INTEGER PRIMARY KEY, company_id INTEGER,
                  code TEXT NOT NULL UNIQUE, name TEXT
                );
                CREATE TABLE toll_plaza (
                  id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT NOT NULL
                );
                """
            )
            conn.close()
            added = ensure_schema(db)
            self.assertIn("project.is_active", added)
            self.assertIn("toll_plaza.chainage", added)
            self.assertEqual(ensure_schema(db), [])


if __name__ == "__main__":
    unittest.main()
