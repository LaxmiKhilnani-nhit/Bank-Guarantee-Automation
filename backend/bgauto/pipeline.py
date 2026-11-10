"""Datalab pipeline adapter: convert -> structured extract.

The app no longer derives tracker fields from OCR text. It runs the configured
Datalab pipeline, takes the convert step output for the review text, and maps
the extract step JSON directly into the tracker model (see
``extract.extract_from_pipeline``).

Results are deleted from Datalab after about a day, so callers must archive the
raw step payloads as soon as the run completes.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, ocr

log = logging.getLogger(__name__)

# Keys that belong to the Datalab response envelope, not to the extraction.
_ENVELOPE_KEYS = {
    "success",
    "status",
    "error",
    "error_in",
    "page_count",
    "total_pages",
    "inferenced_pages",
    "total_tokens",
    "request_id",
    "checkpoint_id",
    "cost_breakdown",
    "final_cost_cents",
    "parse_quality_score",
    "json",
    "markdown",
    "html",
    "chunks",
    "images",
    "links",
    "metadata",
    "filepath",
    "runtime",
    "converted",
    "dropped_pages",
    "block_fallback_pages",
    "pages_with_dropped_blocks",
    "post_inference_params",
    "extraction_schema_json",
    "page_schema",
    "lift_result",
    "output_format",
    "extraction",
    "data",
    "result",
    "output",
}


@dataclass
class PipelineResult:
    document: ocr.DocumentOCR
    data: dict[str, Any]
    execution_id: str
    pipeline_id: str
    pipeline_version: int | None = None
    extraction_mode: str | None = None
    page_count: int | None = None
    raw_convert: dict[str, Any] = field(default_factory=dict)
    raw_extract: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.pipeline_id,
            "version": self.pipeline_version,
            "execution_id": self.execution_id,
            "extraction_mode": self.extraction_mode,
            "pages": self.page_count or len(self.document.pages),
            "warnings": list(self.warnings),
        }

    def raw(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "version": self.pipeline_version,
            "execution_id": self.execution_id,
            "extraction_mode": self.extraction_mode,
            "convert": self.raw_convert,
            "extract": self.raw_extract,
        }


def _describe(error: Exception) -> str:
    return ocr._describe(error)


def _payload(value) -> dict[str, Any]:
    """Flatten the common ``{"result": {...}}`` wrappers around step output."""
    if not isinstance(value, dict):
        return {}
    for key in ("result", "data", "output"):
        inner = value.get(key)
        if isinstance(inner, dict):
            return {**value, **inner}
    return value


def _extraction_data(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("error") or "extraction failed"))

    for key in ("extraction_schema_json", "extraction", "data", "result"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict) and value:
            return value

    # Turbo can return the fields at the top level without a wrapper.
    if any(not key.startswith("_") and key not in _ENVELOPE_KEYS for key in payload):
        return payload

    raise RuntimeError("Datalab returned no extraction JSON")


def _clean_extraction(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if key.startswith("_") or key in _ENVELOPE_KEYS:
            continue
        if key.endswith("_citations") or key.endswith("_score"):
            continue
        clean[key] = value
    return clean


def _find_step(execution, step_type: str):
    for step in getattr(execution, "steps", None) or []:
        if getattr(step, "step_type", None) == step_type:
            return step
    return None


def _failed_steps(execution) -> list[str]:
    messages: list[str] = []
    for step in getattr(execution, "steps", None) or []:
        if getattr(step, "status", None) == "failed":
            detail = getattr(step, "error_message", None) or "no error detail"
            messages.append(f"{step.step_type} step failed: {detail}")
    return messages


def _stage(execution) -> str:
    extract = _find_step(execution, "extract")
    if extract is not None and getattr(extract, "status", "") in {
        "dispatched", "running",
    }:
        return "extracting"
    convert = _find_step(execution, "convert")
    if convert is not None and getattr(convert, "status", "") in {
        "pending", "dispatched", "running",
    }:
        return "converting"
    if getattr(execution, "status", "") == "completed":
        return "finishing"
    return "processing"


def _percent(execution) -> float:
    value = getattr(execution, "progress_percent", None)
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number > 0:
        return max(0.0, min(100.0, number))

    steps = getattr(execution, "steps", None) or []
    if not steps:
        return 0.0
    weights = {"completed": 1.0, "skipped": 1.0, "running": 0.5, "dispatched": 0.25}
    return round(
        100.0
        * sum(weights.get(getattr(step, "status", ""), 0.0) for step in steps)
        / len(steps),
        1,
    )


def _wait(client, execution_id: str, progress=None):
    """Poll until the execution is terminal, reporting the active step."""
    interval = max(float(config.DATALAB_POLL_INTERVAL), 0.1)
    started = time.monotonic()
    execution = None
    for _ in range(max(1, int(config.DATALAB_MAX_POLLS))):
        execution = client.get_pipeline_execution(execution_id, max_polls=1)
        if progress:
            progress(
                {
                    "engine": "datalab",
                    "stage": _stage(execution),
                    "percent": _percent(execution),
                    "seconds": round(time.monotonic() - started, 1),
                }
            )
        if getattr(execution, "status", "") in {
            "completed", "completed_with_errors", "failed",
        }:
            return execution
        time.sleep(interval)
    if getattr(execution, "status", "") not in {
        "completed", "completed_with_errors", "failed",
    }:
        raise RuntimeError(
            "Datalab pipeline did not finish within "
            f"{max(1, int(config.DATALAB_MAX_POLLS)) * interval:.0f}s"
        )
    return execution


def _extraction_mode(execution) -> str | None:
    snapshot = getattr(execution, "config_snapshot", None)
    if not isinstance(snapshot, dict):
        return None
    steps = snapshot.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and step.get("type") == "extract":
            settings = step.get("settings") or {}
            if isinstance(settings, dict):
                return settings.get("extraction_mode")
    return None


def _fetch_step_result(client, execution_id: str, step_index: int):
    """Download a step result, retrying while Datalab still publishes it.

    A pipeline can report ``completed`` a moment before a step's result
    endpoint is readable; a single immediate call then 404s with "not yet
    available". Only that transient error is retried.
    """
    attempts = max(1, min(int(config.DATALAB_MAX_POLLS), 60))
    interval = max(float(config.DATALAB_POLL_INTERVAL), 0.5)
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return client.get_step_result(execution_id, step_index)
        except Exception as error:
            last = error
            if "not yet available" not in _describe(error).lower():
                raise
            time.sleep(interval)
    assert last is not None
    raise last


def run(pdf_path: Path, progress=None) -> PipelineResult:
    """Run the configured Datalab pipeline on one PDF.

    Raises ``RuntimeError`` with a reviewer-friendly message on API, pipeline,
    or extraction failure. No OCR-text fallback is attempted.
    """
    client = ocr._client()
    if progress:
        progress(
            {
                "engine": "datalab",
                "stage": "starting",
                "percent": 0,
                "seconds": 0,
            }
        )

    try:
        execution = client.run_pipeline(
            config.DATALAB_PIPELINE_ID,
            file_path=str(pdf_path),
            version=config.DATALAB_PIPELINE_VERSION,
            # A cached convert checkpoint has no step-result endpoint, so the
            # review text and raw archive would be missing. Always materialise
            # both steps; extract (turbo) never reuses a checkpoint anyway.
            skip_cache=True,
            max_polls=1,
            poll_interval=config.DATALAB_POLL_INTERVAL,
        )
        execution = _wait(client, execution.execution_id, progress=progress)
    except Exception as error:
        raise RuntimeError(
            f"Datalab pipeline failed to read {pdf_path.name}: {_describe(error)}"
        ) from error

    status = getattr(execution, "status", "failed")
    failures = _failed_steps(execution)
    if status == "failed":
        detail = "; ".join(failures) or "pipeline execution failed"
        raise RuntimeError(f"Datalab pipeline could not read {pdf_path.name}: {detail}")

    extract_step = _find_step(execution, "extract")
    convert_step = _find_step(execution, "convert")
    if extract_step is None:
        raise RuntimeError("The Datalab pipeline has no extract step")
    if convert_step is None:
        raise RuntimeError("The Datalab pipeline has no convert step")
    if getattr(extract_step, "status", None) == "failed":
        detail = "; ".join(failures) or "extraction step failed"
        raise RuntimeError(f"Datalab could not extract fields from {pdf_path.name}: {detail}")
    if getattr(convert_step, "status", None) == "failed":
        detail = "; ".join(failures) or "conversion step failed"
        raise RuntimeError(f"Datalab could not read {pdf_path.name}: {detail}")

    try:
        raw_convert = _fetch_step_result(
            client, execution.execution_id, convert_step.step_index
        )
        raw_extract = _fetch_step_result(
            client, execution.execution_id, extract_step.step_index
        )
    except Exception as error:
        raise RuntimeError(
            f"Could not download Datalab results for {pdf_path.name}: {_describe(error)}"
        ) from error

    convert_payload = _payload(raw_convert)
    extract_payload = _payload(raw_extract)
    try:
        data = _clean_extraction(_extraction_data(extract_payload))
    except RuntimeError as error:
        raise RuntimeError(
            f"Datalab returned no usable extraction for {pdf_path.name}: {error}"
        ) from error

    try:
        document = ocr.document_from_payload(
            pdf_path,
            json_payload=convert_payload.get("json"),
            markdown=str(convert_payload.get("markdown") or ""),
            quality=convert_payload.get("parse_quality_score"),
        )
    except RuntimeError as error:
        raise RuntimeError(
            f"Datalab returned no readable pages for {pdf_path.name}: {error}"
        ) from error

    page_count = (
        extract_payload.get("page_count")
        or extract_payload.get("inferenced_pages")
        or convert_payload.get("page_count")
        or convert_payload.get("total_pages")
    )
    try:
        page_count = int(page_count) if page_count is not None else None
    except (TypeError, ValueError):
        page_count = None

    warnings = failures if status == "completed_with_errors" else []
    if progress:
        progress(
            {
                "engine": "datalab",
                "stage": "finishing",
                "percent": 100,
                "seconds": 0,
            }
        )
    return PipelineResult(
        document=document,
        data=data,
        execution_id=execution.execution_id,
        pipeline_id=config.DATALAB_PIPELINE_ID,
        pipeline_version=getattr(execution, "pipeline_version", None),
        extraction_mode=_extraction_mode(execution)
        or config.DATALAB_EXTRACTION_MODE,
        page_count=page_count,
        raw_convert=raw_convert if isinstance(raw_convert, dict) else {},
        raw_extract=raw_extract if isinstance(raw_extract, dict) else {},
        warnings=warnings,
    )
