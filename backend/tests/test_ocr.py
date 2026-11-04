"""Fast checks for the Datalab OCR adapter.

Nothing here talks to the network: the client and the options factory are
faked, so the tests cover mode selection, failure mapping and the conversion of
Datalab's JSON/Markdown output into page text.
"""
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from bgauto import ocr


class FakeResult:
    def __init__(self, data=None, markdown="", success=True, error=None, quality=4.5):
        self.json = data
        self.markdown = markdown
        self.success = success
        self.error = error
        self.parse_quality_score = quality


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def convert(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self.error:
            raise self.error
        return self.result


class HttpError(Exception):
    status_code = 402
    response_data = {"error": "payment required"}


BLOCK_TREE = {
    "block_type": "Document",
    "children": [
        {
            "block_type": "Page",
            "polygon": [[0, 0], [612, 0], [612, 792], [0, 792]],
            "children": [
                {"block_type": "SectionHeader", "html": "<h1>BANK GUARANTEE</h1>"},
                {"block_type": "Text", "html": "<p>BG No: 1234</p>"},
            ],
        },
        {
            "block_type": "Page",
            "children": [
                {
                    "block_type": "Table",
                    "html": "<table><tr><td>Bank</td><td>HDFC</td></tr></table>",
                }
            ],
        },
    ],
}


class DatalabConversionTests(TestCase):
    def test_fast_mode_is_the_default(self):
        result = FakeResult(data=BLOCK_TREE)
        client = FakeClient(result=result)
        options = Mock()
        with patch.object(ocr.config, "DATALAB_MODE", "fast"), patch.object(
            ocr, "_client", return_value=client
        ), patch.object(ocr, "_options", return_value=options) as factory:
            document = ocr.convert_pdf(Path("sample.pdf"))

        factory.assert_called_once_with("fast")
        self.assertEqual(document.engine, "datalab")
        self.assertEqual(len(document.pages), 2)
        self.assertEqual(document.pages[0].text, "BANK GUARANTEE\nBG No: 1234")
        self.assertEqual(document.pages[0].width, 612)
        self.assertEqual(document.pages[0].height, 792)
        self.assertAlmostEqual(document.mean_conf, 0.9)
        self.assertEqual(document.confidence_source, "datalab_quality")

        path, kwargs = client.calls[0]
        self.assertEqual(path, "sample.pdf")
        self.assertIs(kwargs["options"], options)
        self.assertEqual(kwargs["max_polls"], ocr.config.DATALAB_MAX_POLLS)
        self.assertEqual(kwargs["poll_interval"], ocr.config.DATALAB_POLL_INTERVAL)

    def test_explicit_mode_is_passed_through(self):
        client = FakeClient(result=FakeResult(data=BLOCK_TREE))
        options = Mock()
        with patch.object(ocr, "_client", return_value=client), patch.object(
            ocr, "_options", return_value=options
        ) as factory:
            ocr.convert_pdf(Path("sample.pdf"), mode="accurate")
        factory.assert_called_once_with("accurate")

    def test_unknown_mode_is_refused_before_any_call(self):
        with patch.object(ocr, "_client") as client:
            with self.assertRaises(ValueError):
                ocr.convert_pdf(Path("sample.pdf"), mode="turbo")
        client.assert_not_called()

    def test_progress_is_reported(self):
        seen = []
        client = FakeClient(result=FakeResult(data=BLOCK_TREE))
        with patch.object(ocr, "_client", return_value=client), patch.object(
            ocr, "_options", return_value=Mock()
        ):
            ocr.convert_pdf(Path("sample.pdf"), progress=seen.append)
        self.assertEqual(seen[0]["percent"], 0)
        self.assertEqual(seen[-1]["percent"], 100)
        self.assertEqual(seen[0]["engine"], "datalab")

    def test_exhausted_allowance_is_explained(self):
        client = FakeClient(error=HttpError("402"))
        with patch.object(ocr, "_client", return_value=client), patch.object(
            ocr, "_options", return_value=Mock()
        ):
            with self.assertRaises(RuntimeError) as caught:
                ocr.convert_pdf(Path("sample.pdf"))
        self.assertIn("allowance is exhausted", str(caught.exception))

    def test_unsuccessful_result_is_an_error(self):
        client = FakeClient(
            result=FakeResult(data=BLOCK_TREE, success=False, error="bad upload")
        )
        with patch.object(ocr, "_client", return_value=client), patch.object(
            ocr, "_options", return_value=Mock()
        ):
            with self.assertRaises(RuntimeError) as caught:
                ocr.convert_pdf(Path("sample.pdf"))
        self.assertIn("bad upload", str(caught.exception))

    def test_pages_key_and_direct_html_blocks(self):
        tree = {"block_type": "Document", "pages": [{"html": "<p>Only page</p>"}]}
        pages = ocr._json_pages(tree, 0.8)
        self.assertEqual([page.text for page in pages], ["Only page"])
        self.assertAlmostEqual(pages[0].mean_conf, 0.8)

    def test_missing_quality_score_leaves_confidence_blank(self):
        client = FakeClient(result=FakeResult(data=BLOCK_TREE, quality=None))
        with patch.object(ocr, "_client", return_value=client), patch.object(
            ocr, "_options", return_value=Mock()
        ):
            document = ocr.convert_pdf(Path("sample.pdf"))
        self.assertEqual(document.confidence_source, "none")
        self.assertEqual(document.mean_conf, 0.0)

    def test_markdown_fallback_splits_pages(self):
        markdown = (
            "Page one\n\n1\n" + "-" * 48 + "\n\nPage two line"
        )
        client = FakeClient(result=FakeResult(data=None, markdown=markdown, quality=5))
        with patch.object(ocr, "_client", return_value=client), patch.object(
            ocr, "_options", return_value=Mock()
        ):
            document = ocr.convert_pdf(Path("sample.pdf"))
        self.assertEqual([page.text for page in document.pages], ["Page one", "Page two line"])
        self.assertAlmostEqual(document.mean_conf, 1.0)


class DatalabAvailabilityTests(TestCase):
    def test_requires_sdk_and_a_key_or_saved_login(self):
        with patch.object(ocr, "_sdk_installed", return_value=True), patch.object(
            ocr.config, "DATALAB_API_KEY", "secret"
        ):
            self.assertTrue(ocr.datalab_available())
        with patch.object(ocr, "_sdk_installed", return_value=True), patch.object(
            ocr.config, "DATALAB_API_KEY", ""
        ), patch.object(ocr, "_saved_login_path", return_value=Path("missing.json")):
            self.assertFalse(ocr.datalab_available())
        with patch.object(ocr, "_sdk_installed", return_value=False), patch.object(
            ocr.config, "DATALAB_API_KEY", "secret"
        ):
            self.assertFalse(ocr.datalab_available())
