"""Regression: the seeded project/plaza master is canonical and unambiguous.

The operations master document lists 28 corridors and 46 plazas. A plaza must
resolve to exactly one project in both directions (plaza -> project and
project -> plaza). Corridor names, combined spellings and OCR aliases must not
be seeded as plazas: the fuzzy matcher in ``extract`` already tolerates the
spelling drift.

Run from the repo root:

    $env:PYTHONPATH = "backend"
    .venv\\Scripts\\python.exe -m unittest backend.tests.test_master_seed -v
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from bgauto.extract import extract
from bgauto.masters import Masters
from bgauto.ocr import DocumentOCR, PageOCR

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "db" / "sqlite" / "schema.sql"
SEED = ROOT / "db" / "sqlite" / "seed.sql"

EASTERN = "NHIT Eastern Projects Private Limited"
SOUTHERN = "NHIT Southern Projects Private Limited"
WESTERN = "NHIT Western Projects Private Limited"

# project: (corridor name, company, {plaza: chainage})
EXPECTED = {
    "KRK": ("Kachugaon-Kalijhar", EASTERN, {"Patagaon": "62.6", "Dhalapara": "51.5"}),
    "RKJL": ("Rewa-Katni-Jabalpur-Lakhnadon", EASTERN, {
        "Odhaki": "69.1", "Mohtara": "88.07",
        "Kherwasani": "67.3", "Boharipar": "81.7",
    }),
    "CHK": ("Chichra-Kharagpur", EASTERN, {"Balibhasa": "56.1"}),
    "OB": ("Orai-Bara", EASTERN, {"Usaka": "62.9"}),
    "LK": ("Lakhnadon-Khawasa", EASTERN, {"Madai": "107.3", "Khawasa": "107.3"}),
    "HHC": ("Hubli-Haveri-Chitradurga", EASTERN, {
        "Bankapur": "214.3", "Chalageri": "214.3", "Hebbalu": "214.3",
    }),
    "KP": ("Kalijhar-Patacharkuchi", EASTERN, {"Galia": "27.3"}),
    "NR": ("Narasannapeta-Ranasthalam", SOUTHERN, {"Madapam": "53.79"}),
    "RH": ("Ranasthalam to Hanumanthvaka", SOUTHERN, {"Nathavalasa": "66.5"}),
    "APA": ("Anandapuram-Pendurthi-Anakapalle", SOUTHERN, {
        "Marripalem": "48.6", "Dukkavanipalem": "48.6",
    }),
    "GDK": ("Gundugolanu Devarapalli Kovvur", SOUTHERN, {"Veeravalli": "70"}),
    "CM": ("Chittoor-Mallavaram", SOUTHERN, {"Gadnaki": "61.128"}),
    "APTNK": (
        "AP/TN Border to Nalagampalli, AP/Karnataka Border Section",
        SOUTHERN,
        {"Mahasamudram": "84.79"},
    ),
    "BS": ("Bareilly-Sitapur", SOUTHERN, {"Faridpur": "157.59", "Maigalganj": "157.59"}),
    "MH": ("Muzaffarnagar-Haridwar", SOUTHERN, {"Bahadrabad": "78.56", "Chapar": "78.56"}),
    "RS": ("Raipur-Simga", SOUTHERN, {"Tarpongi": "48.5"}),
    "SB": ("Simga-Bilaspur", SOUTHERN, {"Bhojpuri": "77.94", "Mudipar": "77.94"}),
    "GMP": ("Gandhidham-Mundra Port", SOUTHERN, {"Mokha": "71.4"}),
    "AS": ("Abu Road-Swaroopganj", WESTERN, {"Undvariya": "31"}),
    "PA": ("Palanpur-Abu Road", WESTERN, {"Khemana": "45"}),
    "BK": ("Belgaum-Karnataka Border", WESTERN, {"Hattargi": "78", "Kognoli": "78"}),
    "CK": ("Chittorgarh-Kota", WESTERN, {
        "Aroli": "160.5", "Bassi": "160.5", "Dhaneshwar": "160.5",
    }),
    "AB": ("Agra-Bypass", WESTERN, {"Raibha": "32.8"}),
    "SJ": ("Shivpuri-Jhansi", WESTERN, {"Raksha": "75.3"}),
    "BW": ("Borkhedi-Wadner-Deodhari-Kelapur", WESTERN, {
        "Daroda": "138.4", "Kelapur": "138.4",
    }),
    "KK": ("Kothkota Kurnool", WESTERN, {"Pullur": "74.62"}),
    "ACT": ("Amravati-Chikhali-Tarsod", WESTERN, {
        "Tarod Kasba": "73", "Dasarkhed": "45",
        "Kurankhed": "76", "Nashirabad": "62.7",
    }),
    "GCA": ("Gundugolanu-Chinna Avutapalli", WESTERN, {"Kalaparru": "54.4"}),
}

# Rows that used to be seeded and must never become a matchable plaza.
FORBIDDEN_PLAZAS = {
    "Anandapuram to Hanumanthavaka",
    "Simga - Bilaspur",
    "Simga to Bilaspur",
    "Raipur",
    "Raipur - Simga",
    "Chappar",
    "Chappar & Bahadrabad",
    "Chappar/Bahadrabad",
    "Muzafarnagar - Haridwar",
    "Muzafarnagar to Haridwar",
    "Bareilly Sitapur",
    "Barielly - Sitapur",
    "Faridpur & Maigalganj",
    "Bhojpuri & Mudipar",
    "Mudipar & Bhojpuri",
    "Dukkavaniplem",
    "Dukkavaniplem & Marriplem",
    "Dukkawalipalem & Maripalem",
    "Gadanki",
    "Veeravali",
    "Nathavalsa",
    "Mahasamundaram",
    "Mahasamudaram",
    "Mahasamundram",
}

# OCR spellings that are still accepted by fuzzy matching, without alias rows.
OCR_VARIANTS = {
    "Chappar": ("Chapar", "MH"),
    "Veeravali": ("Veeravalli", "GDK"),
    "Nathavalsa": ("Nathavalasa", "RH"),
    "Gadanki": ("Gadnaki", "CM"),
    "Mahasamundaram": ("Mahasamudram", "APTNK"),
    "Mahasamudram": ("Mahasamudram", "APTNK"),
    "Dukkavaniplem": ("Dukkavanipalem", "APA"),
}


def _doc(*lines: str) -> DocumentOCR:
    return DocumentOCR(
        source=Path("BG 1234AB12345678901.pdf"),
        pages=[PageOCR(number=1, width=0, height=0, text="\n".join(lines))],
    )


class SeedMasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCHEMA.exists() or not SEED.exists():
            raise unittest.SkipTest("db/sqlite schema or seed not present")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._tmp.name) / "seed.sqlite3"
        conn = sqlite3.connect(cls.db)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.executescript(SEED.read_text(encoding="utf-8"))
        conn.close()
        cls.masters = Masters.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_exactly_the_canonical_projects(self):
        self.assertEqual(sorted(self.masters.projects), sorted(EXPECTED))
        self.assertNotIn("RS,SB", self.masters.projects)
        self.assertNotIn("NSPPL", self.masters.projects)

    def test_company_mapping(self):
        options = {option["code"]: option for option in self.masters.project_options}
        for code, (name, company, _) in EXPECTED.items():
            with self.subTest(project=code):
                self.assertEqual(options[code]["name"], name)
                self.assertEqual(options[code]["company"], company)

    def test_only_the_three_spvs_are_companies(self):
        self.assertEqual(
            sorted(self.masters.companies), sorted([EASTERN, SOUTHERN, WESTERN])
        )
        self.assertNotIn("NSPPL", self.masters.companies)

    def test_bank_and_contractor_start_empty(self):
        # Names are created on confirm (after the Firecrawl check), not seeded.
        self.assertEqual(self.masters.banks, [])
        self.assertEqual(self.masters.contractors, [])

    def test_company_follows_the_project(self):
        doc = DocumentOCR(
            source=Path("BG 0045NDLG00004227.pdf"),
            pages=[PageOCR(number=1, width=0, height=0, text=(
                "PERFORMANCE BANK GUARANTEE\n"
                "Beneficiary: NHIT Southern Projects Private Limited (NSPPL)\n"
                "Toll Plaza: Faridpur"
            ))],
        )
        result = extract(doc, self.masters)
        self.assertEqual(result.value("Project Name"), "BS")
        self.assertEqual(result.value("Company Name"), SOUTHERN)

    def test_every_plaza_maps_to_exactly_one_project(self):
        seen = Counter(plaza["name"] for plaza in self.masters.toll_plazas)
        duplicates = {name: count for name, count in seen.items() if count > 1}
        self.assertEqual(duplicates, {})

        by_project = {}
        for plaza in self.masters.toll_plazas:
            by_project.setdefault(plaza["project"], {})[plaza["name"]] = plaza["chainage"]
        self.assertEqual(
            by_project,
            {code: plazas for code, (_, _, plazas) in EXPECTED.items()},
        )

    def test_forbidden_alias_and_corridor_rows_are_absent(self):
        seeded = {plaza["name"] for plaza in self.masters.toll_plazas}
        self.assertEqual(seeded & FORBIDDEN_PLAZAS, set())

    def test_every_plaza_resolves_to_its_project(self):
        for code, (_, _, plazas) in EXPECTED.items():
            for plaza in plazas:
                with self.subTest(plaza=plaza):
                    result = extract(_doc("BANK GUARANTEE", f"Toll Plaza: {plaza}"), self.masters)
                    self.assertEqual(result.value("Toll Plaza Name"), plaza)
                    self.assertEqual(result.value("Project Name"), code)

    def test_project_name_in_text_resolves_the_corridor(self):
        for code, (name, _, _) in EXPECTED.items():
            # "to"/"and"/"-" separators must not stop the corridor-name match.
            text = name.replace("/", " ").replace(",", " ").replace("-", " to ")
            with self.subTest(project=code):
                result = extract(
                    _doc("BANK GUARANTEE", f"Routine Maintenance at {text} section"),
                    self.masters,
                )
                self.assertEqual(result.value("Project Name"), code)

    def test_ocr_spellings_match_without_alias_rows(self):
        for spelling, (canonical, code) in OCR_VARIANTS.items():
            with self.subTest(spelling=spelling):
                result = extract(
                    _doc("BANK GUARANTEE", f"Toll Plaza: {spelling}"), self.masters
                )
                self.assertEqual(result.value("Toll Plaza Name"), canonical)
                self.assertEqual(result.value("Project Name"), code)


if __name__ == "__main__":
    unittest.main()
