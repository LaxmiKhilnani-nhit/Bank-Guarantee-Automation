"""Master-data parsing and import: the project/plaza lists behind the dropdowns.

The markdown below mirrors the shape of the operations master document, including
its escaped numbering and the "Toll Mananger" typo. No real staff names are used.
"""
from __future__ import annotations

import unittest

from bgauto import store
from bgauto.masterdata import import_master_data, parse_master_markdown
from bgauto.masters import Masters
from support import TempDatabase

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


class ImportTests(TempDatabase):
    # A clean schema: V2 already seeds the canonical AB corridor, and this test
    # imports its own synthetic document.
    canonical_seed = False

    def setUp(self) -> None:
        super().setUp()
        self.doc = self.root / "master-data.md"
        self.doc.write_text(DOC, encoding="utf-8")

    def test_schema_is_already_current(self):
        with store.connect() as conn:
            self.assertEqual(store.schema_problems(conn), [])

    def test_import_creates_and_is_idempotent(self):
        first = import_master_data(self.doc)
        self.assertEqual(len(first["companies"]["created"]), 2)
        self.assertEqual(sorted(first["projects"]["created"]), ["AB", "EZ", "GD"])
        self.assertEqual(len(first["plazas"]["created"]), 3)

        second = import_master_data(self.doc)
        for group in ("companies", "projects", "plazas"):
            for key, value in second[group].items():
                self.assertEqual(value, [], f"{group}.{key} changed on re-import")

        masters = Masters.load()
        self.assertIn("AB", masters.projects)
        self.assertIn(
            {"project": "AB", "name": "First Plaza", "chainage": "62.6"},
            masters.toll_plazas,
        )
        option = next(o for o in masters.project_options if o["code"] == "AB")
        self.assertEqual(option["name"], "Alpha-Beta")
        self.assertEqual(option["company"], "Example Eastern Projects Private Limited")

    def test_missing_projects_are_deactivated_not_deleted(self):
        import_master_data(self.doc)
        smaller = self.root / "smaller.md"
        smaller.write_text(DOC_WITHOUT_AB, encoding="utf-8")
        report = import_master_data(smaller)
        self.assertIn("AB", report["projects"]["deactivated"])
        self.assertIn("EZ", report["projects"]["deactivated"])

        masters = Masters.load()
        self.assertNotIn("AB", masters.projects)
        self.assertIn("GD", masters.projects)
        self.assertNotIn("First Plaza", [p["name"] for p in masters.toll_plazas])

    def test_dry_run_changes_nothing(self):
        import_master_data(self.doc, dry_run=True)
        masters = Masters.load()
        self.assertNotIn("AB", masters.projects)


if __name__ == "__main__":
    unittest.main()
