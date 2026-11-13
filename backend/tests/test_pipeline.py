"""Datalab pipeline adapter: step unwrapping, polling, and failure handling."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bgauto import pipeline


def _step(index: int, step_type: str, status: str = "completed"):
    return SimpleNamespace(
        step_index=index,
        step_type=step_type,
        status=status,
        error_message=None,
    )


def _execution(status: str = "completed", steps=None):
    return SimpleNamespace(
        execution_id="pex_TEST",
        status=status,
        pipeline_version=3,
        progress_percent=100,
        steps=steps
        or [_step(0, "convert"), _step(1, "extract")],
        config_snapshot={
            "steps": [
                {"type": "convert", "settings": {"parse_method": "fast"}},
                {"type": "extract", "settings": {"extraction_mode": "turbo"}},
            ]
        },
    )


CONVERT = {
    "success": True,
    "page_count": 1,
    "json": {
        "children": [
            {
                "block_type": "Page",
                "children": [
                    {"block_type": "Text", "html": "<p>BANK GUARANTEE</p>"}
                ],
            }
        ]
    },
    "markdown": "# BANK GUARANTEE",
}

EXTRACT = {
    "success": True,
    "inferenced_pages": 1,
    "extraction_schema_json": {
        "company_name": "NHIT Southern Projects Private Limited",
        "bg_no": "1234AB12345678901",
        "bg_amount": 1000.0,
        "toll_plaza_names": ["Faridpur"],
        "authorized_signatories": [{"name": "A. Signatory", "sol_id": "1"}],
    },
    "page_schema": "{}",
}


class FakeClient:
    def __init__(self, execution):
        self.execution = execution
        self.calls = []

    def run_pipeline(self, pipeline_id, **kwargs):
        self.calls.append(("run", pipeline_id, kwargs))
        return self.execution

    def get_pipeline_execution(self, execution_id, max_polls=1):
        self.calls.append(("poll", execution_id, max_polls))
        return self.execution

    def get_step_result(self, execution_id, step_index):
        self.calls.append(("step", execution_id, step_index))
        return CONVERT if step_index == 0 else EXTRACT


class PipelineRunTests(unittest.TestCase):
    def test_run_maps_both_steps_and_keeps_provenance(self):
        client = FakeClient(_execution())
        with patch.object(pipeline.ocr, "_client", return_value=client):
            result = pipeline.run(Path("BG 1234AB12345678901.pdf"))

        self.assertEqual(result.data["bg_no"], "1234AB12345678901")
        self.assertNotIn("page_schema", result.data)
        self.assertNotIn("extraction_schema_json", result.data)
        self.assertIn("BANK GUARANTEE", result.document.text)
        self.assertEqual(result.execution_id, "pex_TEST")
        self.assertEqual(result.pipeline_version, 3)
        self.assertEqual(result.extraction_mode, "turbo")
        self.assertEqual(result.page_count, 1)
        self.assertIn("convert", result.raw())
        self.assertEqual(result.meta()["version"], 3)

    def test_run_uses_the_pinned_pipeline_version(self):
        client = FakeClient(_execution())
        with patch.object(pipeline.ocr, "_client", return_value=client):
            with patch.object(pipeline.config, "DATALAB_PIPELINE_VERSION", 3):
                pipeline.run(Path("sample.pdf"))
        _, pipeline_id, kwargs = client.calls[0]
        self.assertEqual(pipeline_id, pipeline.config.DATALAB_PIPELINE_ID)
        self.assertEqual(kwargs["version"], 3)
        self.assertTrue(kwargs["skip_cache"])

    def test_extraction_mode_falls_back_to_the_configured_label(self):
        execution = _execution()
        execution.config_snapshot = None
        client = FakeClient(execution)
        with patch.object(pipeline.ocr, "_client", return_value=client):
            result = pipeline.run(Path("sample.pdf"))
        self.assertEqual(
            result.extraction_mode, pipeline.config.DATALAB_EXTRACTION_MODE
        )

    def test_failed_extract_step_raises_without_a_text_fallback(self):
        execution = _execution(
            steps=[_step(0, "convert"), _step(1, "extract", "failed")]
        )
        client = FakeClient(execution)
        with patch.object(pipeline.ocr, "_client", return_value=client):
            with self.assertRaises(RuntimeError) as caught:
                pipeline.run(Path("sample.pdf"))
        self.assertIn("extract", str(caught.exception))
        self.assertFalse(any(call[0] == "step" for call in client.calls))

    def test_failed_execution_raises(self):
        execution = SimpleNamespace(
            execution_id="pex_TEST",
            status="failed",
            pipeline_version=3,
            progress_percent=0,
            steps=[
                SimpleNamespace(
                    step_index=0,
                    step_type="convert",
                    status="failed",
                    error_message="bad pdf",
                )
            ],
            config_snapshot=None,
        )
        with patch.object(pipeline.ocr, "_client", return_value=FakeClient(execution)):
            with self.assertRaises(RuntimeError) as caught:
                pipeline.run(Path("sample.pdf"))
        self.assertIn("bad pdf", str(caught.exception))

    def test_step_result_is_retried_while_it_is_publishing(self):
        calls = {"count": 0}
        original = FakeClient(_execution())
        original_method = original.get_step_result

        def flaky(execution_id, step_index):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("Step 0 result not yet available (status: completed)")
            return original_method(execution_id, step_index)

        original.get_step_result = flaky
        with patch.object(pipeline.ocr, "_client", return_value=original), patch.object(
            pipeline.time, "sleep", return_value=None
        ):
            result = pipeline.run(Path("sample.pdf"))
        self.assertEqual(result.data["bg_no"], "1234AB12345678901")
        self.assertGreaterEqual(calls["count"], 2)

    def test_missing_extraction_json_raises(self):
        client = FakeClient(_execution())
        client.get_step_result = lambda execution_id, step_index: (
            CONVERT if step_index == 0 else {"success": True, "markdown": "text"}
        )
        with patch.object(pipeline.ocr, "_client", return_value=client):
            with self.assertRaises(RuntimeError) as caught:
                pipeline.run(Path("sample.pdf"))
        self.assertIn("no usable extraction", str(caught.exception))


class StepShapeTests(unittest.TestCase):
    def test_extraction_data_accepts_common_wrappers(self):
        self.assertEqual(
            pipeline._extraction_data({"extraction_schema_json": {"bg_no": "X"}}),
            {"bg_no": "X"},
        )
        self.assertEqual(
            pipeline._extraction_data({"data": {"bg_no": "Y"}}), {"bg_no": "Y"}
        )
        self.assertEqual(
            pipeline._extraction_data({"bg_no": "Z"}), {"bg_no": "Z"}
        )

    def test_extraction_data_refuses_a_failed_payload(self):
        with self.assertRaises(RuntimeError):
            pipeline._extraction_data({"success": False, "error": "quota"})

    def test_clean_extraction_drops_scores_and_envelope(self):
        clean = pipeline._clean_extraction(
            {
                "bg_no": "X",
                "bg_no_score": 0.9,
                "bg_no_citations": [1],
                "page_schema": "{}",
                "_meta": {},
                "success": True,
            }
        )
        self.assertEqual(clean, {"bg_no": "X"})

    def test_stage_follows_the_active_step(self):
        converting = _execution(
            status="processing",
            steps=[
                _step(0, "convert", "running"),
                _step(1, "extract", "pending"),
            ],
        )
        extracting = _execution(
            status="processing",
            steps=[
                _step(0, "convert", "completed"),
                _step(1, "extract", "running"),
            ],
        )
        self.assertEqual(pipeline._stage(converting), "converting")
        self.assertEqual(pipeline._stage(extracting), "extracting")

    def test_percent_falls_back_to_step_weights(self):
        execution = _execution(
            status="processing",
            steps=[_step(0, "convert", "completed"), _step(1, "extract", "running")],
        )
        execution.progress_percent = None
        self.assertEqual(pipeline._percent(execution), 75.0)


if __name__ == "__main__":
    unittest.main()
