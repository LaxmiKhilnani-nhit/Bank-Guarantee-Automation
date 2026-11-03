"""PDF OCR adapters for local engines and Azure Document Intelligence."""
from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps
import pytesseract

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
    engine: str = "tesseract"

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
        return self.pages[0].confidence_source if self.pages else "ocr"

    def page_texts(self) -> list[str]:
        return [p.text for p in self.pages]


def _render(pdf_path: Path, dpi: int) -> list[Image.Image]:
    images: list[Image.Image] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(img)
    return images


def _binarise(img: Image.Image, threshold: int = 150) -> Image.Image:
    gray = ImageOps.autocontrast(ImageOps.grayscale(img))
    return gray.point(lambda px: 0 if px < threshold else 255)


def _ocr_image(img: Image.Image, psm: int) -> PageOCR:
    data = pytesseract.image_to_data(
        img,
        lang="eng",
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    words: list[Word] = []
    lines: dict[tuple[int, int, int], list[str]] = {}
    for i, raw in enumerate(data["text"]):
        text = (raw or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        words.append(
            Word(
                text=text,
                conf=conf,
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
            )
        )
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(text)

    text = "\n".join(" ".join(parts) for _, parts in sorted(lines.items()))
    return PageOCR(
        number=0,
        width=img.width,
        height=img.height,
        text=text,
        words=words,
    )


def _tesseract_pdf(
    pdf_path: Path, dpi: int | None = None, psm: int | None = None
) -> DocumentOCR:
    """OCR every page with Tesseract and keep the better of two passes."""
    configure()
    dpi = dpi or config.OCR_DPI
    psm = psm or config.OCR_PSM
    pages: list[PageOCR] = []
    for number, image in enumerate(_render(pdf_path, dpi), start=1):
        plain = _ocr_image(image, psm)
        clean = _ocr_image(_binarise(image), psm)
        best = clean if clean.mean_conf > plain.mean_conf else plain
        best.number = number
        pages.append(best)
    return DocumentOCR(source=pdf_path, pages=pages, engine="tesseract")


@lru_cache(maxsize=1)
def _indra_converter():
    try:
        from indra_ocr import load
    except ImportError as error:
        raise RuntimeError("Indra OCR is not installed; install backend/requirements.txt") from error
    return load(threads=config.INDRA_THREADS, mode=config.INDRA_MODE)


def indra_available() -> bool:
    try:
        from indra_ocr import load  # noqa: F401
        return True
    except ImportError:
        return False


def _indra_pdf(pdf_path: Path, progress=None) -> DocumentOCR:
    """Read every page locally with Indra; no PDF is sent to a service."""
    converter = _indra_converter()
    pages: list[PageOCR] = []
    try:
        converted = converter.convert(str(pdf_path))
        for number, (_, page) in enumerate(converted, start=1):
            text = str(getattr(page, "markdown", "") or getattr(page, "text", "") or "")
            words: list[Word] = []
            for line in getattr(page, "lines", []) or []:
                line_text = str(getattr(line, "text", "") or "").strip()
                if not line_text:
                    continue
                box = getattr(line, "box", None) or (0, 0, 0, 0)
                try:
                    left, top, right, bottom = (int(float(v)) for v in box[:4])
                except (TypeError, ValueError):
                    left = top = right = bottom = 0
                confidence = getattr(line, "conf", None)
                try:
                    score = float(confidence) if confidence is not None else -1.0
                    if 0 <= score <= 1:
                        score *= 100
                except (TypeError, ValueError):
                    score = -1.0
                words.append(Word(line_text, score, left, top, max(0, right-left), max(0, bottom-top)))
            width = int(getattr(page, "width", 0) or 0)
            height = int(getattr(page, "height", 0) or 0)
            pages.append(PageOCR(number, width, height, text, words, confidence_source="ocr"))
            if progress:
                progress({"engine": "indra", "page": number, "pages_done": number, "pages_total": None})
    except Exception as error:
        raise RuntimeError(f"Indra failed to read {pdf_path.name}: {error}") from error
    if not pages:
        raise RuntimeError("Indra returned no pages for the PDF")
    return DocumentOCR(source=pdf_path, pages=pages, engine="indra")


@lru_cache(maxsize=2)
def _azure_client(key: str):
    if not config.AZURE_DI_ENDPOINT or not key:
        raise RuntimeError(
            "Azure Document Intelligence is not configured; set AZURE_DI_ENDPOINT and AZURE_DI_KEY"
        )
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError as error:
        raise RuntimeError(
            "Azure Document Intelligence SDK is not installed; rebuild the app image"
        ) from error
    return DocumentIntelligenceClient(
        endpoint=config.AZURE_DI_ENDPOINT,
        credential=AzureKeyCredential(key),
    )


def azure_available() -> bool:
    if not config.AZURE_DI_ENDPOINT or not (config.AZURE_DI_KEY or config.AZURE_DI_KEY_2):
        return False
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient  # noqa: F401
        from azure.core.credentials import AzureKeyCredential  # noqa: F401
        return True
    except ImportError:
        return False


def _azure_pdf(pdf_path: Path, progress=None) -> DocumentOCR:
    """Analyze a PDF remotely with Azure Document Intelligence (prebuilt-layout)."""
    keys = list(dict.fromkeys(key for key in (config.AZURE_DI_KEY, config.AZURE_DI_KEY_2) if key))
    if not keys or not config.AZURE_DI_ENDPOINT:
        raise RuntimeError(
            "Azure Document Intelligence is not configured; set AZURE_DI_ENDPOINT and an AZURE_DI_KEY"
        )
    batch_size = max(1, config.AZURE_DI_PAGE_BATCH_SIZE)
    max_bytes = max(1, config.AZURE_DI_MAX_BYTES)
    pages: list[PageOCR] = []
    with pymupdf.open(pdf_path) as source:
        total_pages = len(source)
        if not total_pages:
            raise RuntimeError("Azure Document Intelligence received a PDF with no pages")
        first_page = 0
        while first_page < total_pages:
            last_page = min(first_page + batch_size, total_pages)
            while True:
                chunk = pymupdf.open()
                chunk.insert_pdf(source, from_page=first_page, to_page=last_page - 1)
                chunk_bytes = chunk.tobytes(garbage=4, deflate=True)
                chunk.close()
                if len(chunk_bytes) <= max_bytes:
                    break
                if last_page - first_page == 1:
                    raise RuntimeError(
                        f"Azure's configured {max_bytes // (1024 * 1024)} MB per-request limit "
                        f"is smaller than page {first_page + 1}; use a local OCR engine or raise AZURE_DI_MAX_BYTES for S0"
                    )
                last_page = first_page + max(1, (last_page - first_page) // 2)

            result = None
            last_error = None
            for key_index, key in enumerate(keys):
                try:
                    client = _azure_client(key)
                    poller = client.begin_analyze_document(
                        config.AZURE_DI_MODEL,
                        body=io.BytesIO(chunk_bytes),
                        content_type="application/pdf",
                        features=["keyValuePairs"],
                        output_content_format="markdown",
                    )
                    result = poller.result()
                    break
                except Exception as error:
                    last_error = error
                    if key_index + 1 >= len(keys) or getattr(error, "status_code", None) not in (401, 403):
                        raise
                    log.info("Azure OCR primary key was rejected; retrying with the configured secondary key")
            if result is None:
                raise RuntimeError(f"Azure analysis returned no result: {last_error}")

            result_content = str(getattr(result, "content", "") or "")
            pairs_by_page: dict[int, list[tuple[str, str, float | None]]] = {}
            for pair in getattr(result, "key_value_pairs", []) or []:
                key_element = getattr(pair, "key", None)
                value_element = getattr(pair, "value", None)
                key_text = str(getattr(key_element, "content", "") or "").strip()
                value_text = str(getattr(value_element, "content", "") or "").strip()
                if not key_text or not value_text:
                    continue
                confidence = getattr(pair, "confidence", None)
                try:
                    pair_confidence = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    pair_confidence = None
                regions = (
                    getattr(key_element, "bounding_regions", None)
                    or getattr(value_element, "bounding_regions", None)
                    or []
                )
                try:
                    relative_page = int(regions[0].page_number)
                except (IndexError, AttributeError, TypeError, ValueError):
                    relative_page = 1
                pairs_by_page.setdefault(relative_page, []).append(
                    (key_text, value_text, pair_confidence)
                )

            for offset, page in enumerate(getattr(result, "pages", []) or []):
                number = first_page + offset + 1
                lines = getattr(page, "lines", []) or []
                spans = getattr(page, "spans", []) or []
                text_parts = []
                for span in spans:
                    try:
                        start = int(span.offset)
                        end = start + int(span.length)
                        text_parts.append(result_content[start:end])
                    except (AttributeError, TypeError, ValueError):
                        continue
                text = "\n".join(part for part in text_parts if part.strip())
                if not text:
                    text = "\n".join(
                        str(getattr(line, "content", "") or "").strip()
                        for line in lines
                        if str(getattr(line, "content", "") or "").strip()
                    )
                scale = 1000
                words: list[Word] = []
                for word in getattr(page, "words", []) or []:
                    content = str(getattr(word, "content", "") or "").strip()
                    if not content:
                        continue
                    confidence = getattr(word, "confidence", None)
                    try:
                        score = float(confidence) * 100 if confidence is not None else -1.0
                    except (TypeError, ValueError):
                        score = -1.0
                    polygon = getattr(word, "polygon", None) or []
                    try:
                        xs = [float(polygon[index]) for index in range(0, len(polygon), 2)]
                        ys = [float(polygon[index]) for index in range(1, len(polygon), 2)]
                        left = int(min(xs) * scale) if xs else 0
                        top = int(min(ys) * scale) if ys else 0
                        width = int((max(xs) - min(xs)) * scale) if xs else 0
                        height = int((max(ys) - min(ys)) * scale) if ys else 0
                    except (TypeError, ValueError):
                        left = top = width = height = 0
                    words.append(Word(content, score, left, top, width, height))

                page_width = int(float(getattr(page, "width", 0) or 0) * scale)
                page_height = int(float(getattr(page, "height", 0) or 0) * scale)
                pages.append(
                    PageOCR(
                        number,
                        page_width,
                        page_height,
                        text,
                        words,
                        confidence_source="ocr",
                        key_values=pairs_by_page.get(offset + 1, []),
                    )
                )
                if progress:
                    progress({
                        "engine": "azure",
                        "page": number,
                        "pages_done": number,
                        "pages_total": total_pages,
                    })
            first_page = last_page

    if not pages:
        raise RuntimeError("Azure Document Intelligence returned no pages for the PDF")
    return DocumentOCR(source=pdf_path, pages=pages, engine="azure")


def ocr_variants(
    pdf_path: Path,
    engine: str | None = None,
    dpi: int | None = None,
    psm: int | None = None,
    progress=None,
) -> tuple[dict[str, DocumentOCR], dict[str, str]]:
    """OCR with the requested engine(s).

    Returns ``({engine: DocumentOCR}, {engine: error})``. A requested engine
    failure falls back to Tesseract when no requested engine produced text.
    """
    requested = (engine or config.OCR_ENGINE or "indra").strip().lower()
    if requested not in config.OCR_ENGINES:
        raise ValueError(
            f"unknown OCR engine {requested!r}; use one of {', '.join(config.OCR_ENGINES)}"
        )
    wanted = {
        "both": ["tesseract", "indra"],
        "all": ["tesseract", "indra", "azure"],
    }.get(requested, [requested])

    documents: dict[str, DocumentOCR] = {}
    errors: dict[str, str] = {}
    for name in wanted:
        if name == "tesseract":
            try:
                documents["tesseract"] = _tesseract_pdf(pdf_path, dpi=dpi, psm=psm)
            except Exception as error:
                errors["tesseract"] = " ".join(str(error).split())[:300]
                log.warning(
                    "Tesseract OCR failed for %s: %s", pdf_path.name, errors["tesseract"]
                )
        elif name == "indra":
            try:
                documents["indra"] = _indra_pdf(pdf_path, progress=progress)
            except Exception as error:
                errors["indra"] = " ".join(str(error).split())[:300]
                log.warning("Indra OCR failed for %s: %s", pdf_path.name, errors["indra"])
        elif name == "azure":
            try:
                documents["azure"] = _azure_pdf(pdf_path, progress=progress)
            except Exception as error:
                errors["azure"] = " ".join(str(error).split())[:300]
                log.warning("Azure OCR failed for %s: %s", pdf_path.name, errors["azure"])
    if not documents and requested != "tesseract" and "tesseract" not in errors:
        try:
            documents["tesseract"] = _tesseract_pdf(pdf_path, dpi=dpi, psm=psm)
            log.info("%s unavailable for %s; used Tesseract instead", requested, pdf_path.name)
        except Exception as error:
            errors["tesseract"] = str(error)

    if not documents:
        detail = "; ".join(f"{name}: {text}" for name, text in errors.items())
        raise ValueError(f"no OCR engine produced text ({detail})")
    return documents, errors


def ocr_pdf(
    pdf_path: Path,
    dpi: int | None = None,
    psm: int | None = None,
    engine: str | None = None,
) -> DocumentOCR:
    """Backwards-compatible single-document entry point.

    ``engine="both"`` keeps the result with the higher mean confidence; the
    upload path uses :func:`ocr_variants` so it can score the extractions too.
    """
    documents, _ = ocr_variants(pdf_path, engine=engine, dpi=dpi, psm=psm)
    if len(documents) == 1:
        return next(iter(documents.values()))
    return max(documents.values(), key=lambda document: document.mean_conf)


_configured = False


def configure() -> None:
    """Point pytesseract at the local Tesseract and language data.

    Called from every entry point (CLI, web startup, ``ocr_pdf``) so no caller
    can forget it; subsequent calls are a no-op.
    """
    global _configured
    if _configured:
        return
    cmd = config.find_tesseract()
    pytesseract.pytesseract.tesseract_cmd = str(cmd)
    tessdata = config.find_tessdata(cmd)
    if tessdata:
        import os

        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
    _configured = True
