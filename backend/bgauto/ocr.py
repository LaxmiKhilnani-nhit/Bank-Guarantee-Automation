"""PDF OCR through the Datalab API.

Datalab converts the PDF to a page-aware JSON/Markdown tree. This adapter turns
that output into the small ``DocumentOCR`` model the extractor already uses, so
field rules and the review UI stay unchanged.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


@dataclass
class Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int


@dataclass
class PageOCR:
    number: int
    width: int
    height: int
    text: str
    words: list[Word] = field(default_factory=list)
    # Confidence scores are retained only when supplied by the OCR engine.
    confidence: float | None = None
    confidence_source: str = "ocr"
    key_values: list[tuple[str, str, float | None]] = field(default_factory=list)

    @property
    def mean_conf(self) -> float:
        if self.confidence is not None:
            return round(self.confidence, 4)
        weighted = [(w.conf, len(w.text)) for w in self.words if w.conf >= 0 and w.text.strip()]
        total = sum(n for _, n in weighted)
        if not total:
            return 0.0
        return round(sum(c * n for c, n in weighted) / total / 100.0, 4)

    @property
    def lines(self) -> list[str]:
        return [ln.strip() for ln in self.text.splitlines() if ln.strip()]


@dataclass
class DocumentOCR:
    source: Path
    pages: list[PageOCR]
    engine: str = "datalab"

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    @property
    def mean_conf(self) -> float:
        if not self.pages:
            return 0.0
        return round(sum(p.mean_conf for p in self.pages) / len(self.pages), 4)

    @property
    def confidence_source(self) -> str:
        """Source of page confidence values, when available."""
        return self.pages[0].confidence_source if self.pages else "none"

    def page_texts(self) -> list[str]:
        return [p.text for p in self.pages]


# --- Datalab client ------------------------------------------------------


def _saved_login_path() -> Path:
    return Path.home() / ".config" / "datalab" / "config.json"


def _sdk_installed() -> bool:
    try:
        import datalab_sdk  # noqa: F401
    except ImportError:
        return False
    return True


def datalab_available() -> bool:
    """True when the SDK is installed and a key or saved login exists.

    This is a local configuration check; it deliberately does not spend an API
    call, so the health endpoint and Docker healthcheck stay free.
    """
    return _sdk_installed() and (
        bool(config.DATALAB_API_KEY) or _saved_login_path().is_file()
    )


@lru_cache(maxsize=1)
def _client():
    try:
        from datalab_sdk import DatalabClient
    except ImportError as error:
        raise RuntimeError(
            "The Datalab SDK is not installed; install backend/requirements.txt"
        ) from error
    options = {
        "base_url": config.DATALAB_BASE_URL,
        "timeout": config.DATALAB_TIMEOUT,
    }
    if config.DATALAB_API_KEY:
        options["api_key"] = config.DATALAB_API_KEY
    return DatalabClient(**options)


def _describe(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    if status == 402:
        return (
            "Datalab allowance is exhausted. Add a card at "
            "https://www.datalab.to/app/subscription before retrying."
        )
    data = getattr(error, "response_data", None)
    detail = ""
    if isinstance(data, dict):
        detail = str(data.get("error") or data.get("detail") or "")
    text = detail or str(error)
    return " ".join(text.split())[:400] or error.__class__.__name__


def _percent(value) -> float | None:
    """Normalise a progress callback value to 0-100."""
    if isinstance(value, dict):
        value = (
            value.get("progress_percent")
            or value.get("percent")
            or value.get("progress")
        )
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, number))


# --- Datalab JSON -> page text ------------------------------------------


_PAGE_DELIMITERS = re.compile(r"\n\n(\d+)\n-{48}\n\n")


class _HtmlLines(HTMLParser):
    """Turn Datalab's block HTML into one line per paragraph or table row."""

    _BLOCK_TAGS = {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "ul", "ol", "pre", "blockquote", "section", "article",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._flush()
        elif tag == "br":
            if self._in_cell:
                self._buffer.append(" ")
            else:
                self._flush()
        elif tag in {"td", "th"}:
            self._in_cell = True
        elif tag in self._BLOCK_TAGS:
            if self._in_cell:
                self._buffer.append(" ")
            else:
                self._flush()

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self._buffer.append("  ")
            self._in_cell = False
        elif tag == "tr":
            self._flush()
        elif tag in self._BLOCK_TAGS:
            if self._in_cell:
                self._buffer.append(" ")
            else:
                self._flush()

    def handle_data(self, data: str) -> None:
        self._buffer.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buffer).split())
        if text:
            self.lines.append(text)
        self._buffer = []

    def finish(self) -> list[str]:
        self._flush()
        return self.lines


def _html_lines(value: str) -> list[str]:
    if not value:
        return []
    parser = _HtmlLines()
    try:
        parser.feed(value)
        return parser.finish()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
        return [" ".join(line.split()) for line in html_lib.unescape(text).splitlines() if line.strip()]


def _block_lines(blocks) -> list[str]:
    lines: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        children = block.get("children") or []
        if children:
            lines.extend(_block_lines(children))
            continue
        for key in ("html", "text", "content", "markdown"):
            value = block.get(key)
            if not value:
                continue
            if key == "html":
                lines.extend(_html_lines(str(value)))
            else:
                lines.extend(str(value).splitlines())
            break
    return lines


def _page_size(block: dict) -> tuple[int, int]:
    polygon = block.get("polygon") or []
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (TypeError, ValueError, IndexError):
        return 0, 0
    if not xs or not ys:
        return 0, 0
    return int(max(xs)), int(max(ys))


def _clean_markdown_line(line: str) -> str:
    text = line.strip()
    if not text:
        return ""
    # Markdown table separator: |---|---|
    if "|" in text and re.fullmatch(r"\|?[\s:\-|]+\|?", text):
        return ""
    if text.startswith("|") and text.endswith("|"):
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        text = "  ".join(cell for cell in cells if cell)
    return text


def _markdown_pages(text: str, confidence: float | None) -> list[PageOCR]:
    parts = _PAGE_DELIMITERS.split(text or "")
    if len(parts) == 1:
        chunks = [parts[0]]
    else:
        chunks = []
        if parts[0].strip():
            chunks.append(parts[0])
        for index in range(1, len(parts), 2):
            chunks.append(parts[index + 1] if index + 1 < len(parts) else "")
    pages: list[PageOCR] = []
    for number, chunk in enumerate(chunks, start=1):
        lines = [
            cleaned
            for cleaned in (_clean_markdown_line(line) for line in chunk.splitlines())
            if cleaned
        ]
        pages.append(
            PageOCR(
                number=number,
                width=0,
                height=0,
                text="\n".join(lines),
                confidence=confidence,
                confidence_source="datalab_quality" if confidence is not None else "none",
            )
        )
    return pages


def _json_pages(raw, confidence: float | None) -> list[PageOCR]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if isinstance(raw, dict):
        # Datalab returns a Document block whose children are Page blocks, but
        # accept the obvious alternatives so a schema tweak does not blank a run.
        for key in ("children", "pages", "blocks"):
            if isinstance(raw.get(key), list):
                candidates = raw[key]
                break
        else:
            candidates = [raw]
    elif isinstance(raw, list):
        candidates = raw
    else:
        return []

    pages: list[PageOCR] = []
    for block in candidates:
        if not isinstance(block, dict):
            continue
        block_type = block.get("block_type")
        if block_type not in (None, "Page"):
            children = [block]
        else:
            children = block.get("children") or []
            if not children and block.get("html"):
                children = [block]
        width, height = _page_size(block)
        text = "\n".join(_block_lines(children))
        pages.append(
            PageOCR(
                number=len(pages) + 1,
                width=width,
                height=height,
                text=text,
                confidence=confidence,
                confidence_source="datalab_quality" if confidence is not None else "none",
            )
        )
    return pages


def _quality(result) -> float | None:
    score = getattr(result, "parse_quality_score", None)
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return max(0.0, min(1.0, value / 5.0))


def _pages_from_result(result) -> list[PageOCR]:
    confidence = _quality(result)
    pages = _json_pages(getattr(result, "json", None), confidence)
    if pages:
        return pages
    return _markdown_pages(getattr(result, "markdown", "") or "", confidence)


# --- public entry point --------------------------------------------------


def _options(mode: str):
    try:
        from datalab_sdk import ConvertOptions
    except ImportError as error:
        raise RuntimeError(
            "The Datalab SDK is not installed; install backend/requirements.txt"
        ) from error
    return ConvertOptions(
        output_format="json",
        mode=mode,
        disable_image_extraction=True,
        disable_image_captions=True,
    )


def convert_pdf(
    pdf_path: Path,
    mode: str | None = None,
    progress=None,
) -> DocumentOCR:
    """Convert one PDF with Datalab and return page-level text."""
    selected = (mode or config.DATALAB_MODE or "fast").strip().lower()
    if selected not in config.DATALAB_MODES:
        raise ValueError(
            f"unknown Datalab mode {selected!r}; use one of "
            f"{', '.join(config.DATALAB_MODES)}"
        )

    options = _options(selected)

    def on_progress(value) -> None:
        if not progress:
            return
        progress(
            {
                "engine": "datalab",
                "page": 0,
                "pages_total": 0,
                "percent": _percent(value),
            }
        )

    if progress:
        progress({"engine": "datalab", "page": 0, "pages_total": 0, "percent": 0})

    try:
        client = _client()
        result = client.convert(
            str(pdf_path),
            options=options,
            max_polls=config.DATALAB_MAX_POLLS,
            poll_interval=config.DATALAB_POLL_INTERVAL,
            on_progress=on_progress,
        )
    except Exception as error:
        raise RuntimeError(
            f"Datalab failed to read {pdf_path.name}: {_describe(error)}"
        ) from error

    if not getattr(result, "success", True):
        message = str(getattr(result, "error", "") or "conversion failed")
        raise RuntimeError(f"Datalab could not read {pdf_path.name}: {message}")

    pages = _pages_from_result(result)
    if not pages:
        raise RuntimeError("Datalab returned no pages for the PDF")
    if progress:
        progress({"engine": "datalab", "page": 0, "pages_total": 0, "percent": 100})
    return DocumentOCR(source=pdf_path, pages=pages, engine="datalab")


def ocr_pdf(pdf_path: Path, mode: str | None = None) -> DocumentOCR:
    """Backwards-compatible single-document entry point."""
    return convert_pdf(pdf_path, mode=mode)
