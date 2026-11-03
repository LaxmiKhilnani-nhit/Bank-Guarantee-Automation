"""Fast checks for local Indra and Tesseract selection/fallback behavior."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from bgauto import ocr
from bgauto.ocr import DocumentOCR


def _document(engine):
    return DocumentOCR(Path("BG 1234AB12345678901.pdf"), [], engine=engine)


class OCRSelectionTests(TestCase):
    def test_indra_is_the_default_engine(self):
        with patch.object(ocr.config, "OCR_ENGINE", "indra"), patch.object(
            ocr, "_indra_pdf", return_value=_document("indra")
        ) as indra:
            documents, errors = ocr.ocr_variants(Path("sample.pdf"))
        indra.assert_called_once()
        self.assertEqual(set(documents), {"indra"})
        self.assertFalse(errors)

    def test_both_runs_both_local_engines(self):
        with patch.object(ocr, "_tesseract_pdf", return_value=_document("tesseract")), patch.object(
            ocr, "_indra_pdf", return_value=_document("indra")
        ):
            documents, errors = ocr.ocr_variants(Path("sample.pdf"), engine="both")
        self.assertEqual(set(documents), {"indra", "tesseract"})
        self.assertFalse(errors)

    def test_indra_failure_falls_back_to_tesseract(self):
        with patch.object(ocr, "_indra_pdf", side_effect=RuntimeError("model unavailable")), patch.object(
            ocr, "_tesseract_pdf", return_value=_document("tesseract")
        ):
            documents, errors = ocr.ocr_variants(Path("sample.pdf"), engine="indra")
        self.assertEqual(set(documents), {"tesseract"})
        self.assertIn("model unavailable", errors["indra"])
