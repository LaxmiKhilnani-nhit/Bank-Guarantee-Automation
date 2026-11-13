"""Tests for the per-field / per-page engine comparison."""
from __future__ import annotations

import unittest
from pathlib import Path

from bgauto import compare
from bgauto.extract import Extraction
from bgauto.normalize import Field
from bgauto.ocr import DocumentOCR, PageOCR


def _page(number: int, text: str, conf: float = 0.9, source: str = "ocr") -> PageOCR:
    return PageOCR(
        number=number,
        width=100,
        height=100,
        text=text,
        confidence=conf,
        confidence_source=source,
    )


def _result(engine: str, pages: list[PageOCR], **fields) -> Extraction:
    document = DocumentOCR(source=Path("BG 1234AB12345678901.pdf"), pages=pages, engine=engine)
    extraction = Extraction(source=document.source, ocr=document)
    for name, value in fields.items():
        extraction.fields[name] = Field(value=value, raw=str(value), page=1)
    return extraction


class BaseChoiceTests(unittest.TestCase):
    def test_full_page_coverage_beats_a_partial_engine_with_more_fields(self):
        partial = _result(
            "tesseract",
            [_page(1, "BG No 1234")],
            **{
                "BG No": "1234",
                "Company Name": "ACME",
                "Bank Name": "ICICI",
                "From": None,
            },
        )
        full = _result(
            "indra",
            [_page(1, "BG No 1234"), _page(2, "page two"), _page(3, "page three")],
            **{"BG No": "1234"},
        )
        self.assertEqual(compare.choose_base({"tesseract": partial, "indra": full}, "both"), "indra")

    def test_page_coverage_is_reported(self):
        stats = compare._engine_stats(
            {
                "indra": _result("indra", [_page(1, "one"), _page(2, "two")]),
            },
            2,
        )
        self.assertAlmostEqual(stats["indra"]["page_coverage"], 1.0)


class FieldMergeTests(unittest.TestCase):
    def test_agreement_keeps_the_value_without_a_flag(self):
        left = _result("tesseract", [_page(1, "x")], **{"BG No": "1234"})
        right = _result("indra", [_page(1, "x")], **{"BG No": "1234"})
        _, merged, report = compare.merge({"tesseract": left, "indra": right}, "both")
        self.assertEqual(merged.value("BG No"), "1234")
        self.assertTrue(report["columns"]["BG No"]["agreement"])
        self.assertEqual(report["merged_fields"], [])
        self.assertEqual(merged.fields["BG No"].flags, [])

    def test_disagreement_keeps_the_base_value_and_flags_it(self):
        left = _result("tesseract", [_page(1, "x")], **{"BG No": "1234"})
        right = _result("indra", [_page(1, "x")], **{"BG No": "5678"})
        _, merged, report = compare.merge({"tesseract": left, "indra": right}, "indra")
        self.assertEqual(merged.value("BG No"), "5678")
        self.assertIn("engine_disagrees", merged.fields["BG No"].flags)
        self.assertIn("BG No", report["disagreements"])
        self.assertFalse(report["columns"]["BG No"]["agreement"])

    def test_blank_field_is_filled_from_the_other_engine(self):
        left = _result(
            "tesseract",
            [_page(1, "x")],
            **{
                "BG No": None,
                "Company Name": "ACME",
                "Project Name": "NSPPL",
                "Bank Name": "ICICI",
                "From": "2026-01-01",
                "To": "2027-01-01",
            },
        )
        right = _result("indra", [_page(1, "x")], **{"BG No": "5678"})
        _, merged, report = compare.merge({"tesseract": left, "indra": right}, "both")
        self.assertEqual(merged.value("BG No"), "5678")
        self.assertIn("BG No", report["merged_fields"])

class PageComparisonTests(unittest.TestCase):
    def test_overlap_is_computed_per_page(self):
        left = _result("tesseract", [_page(1, "BANK GUARANTEE 1234")])
        right = _result(
            "indra", [_page(1, "BANK GUARANTEE 1234"), _page(2, "only indra")]
        )
        rows = compare._page_rows({"tesseract": left, "indra": right}, 2)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["overlap"]["tesseract/indra"], 1.0)
        self.assertIsNone(rows[1]["engines"]["tesseract"])
        self.assertIn("indra", rows[1]["engines"])


if __name__ == "__main__":
    unittest.main()
