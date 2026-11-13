"""Compare OCR engines per field and per page.

The old comparison picked one whole extraction: "most required fields, then
fewest flags, then confidence". That is the wrong question. A model that
hallucinates one more number wins even when the other engine read the page
correctly, and an engine that only saw page 1 can never be compared fairly with
one that read all pages.

This module compares the two things a reviewer can actually check:

* **Per field.** Each engine votes for a value. Agreement is the strongest
  signal; disagreement keeps the base engine's unflagged value and records the
  other value so the reviewer sees it.
* **Per page.** Text overlap between engines on the *same page number*, plus
  page coverage, so a partial read is visible instead of hidden behind one
  whole-document confidence number.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
from dataclasses import dataclass, field

from .extract import Extraction, finalise

# The tracker columns produced by reading the document. Server-owned columns
# (source_pdf, created_by, ...) and review-only columns are not compared.
COMPARE_COLUMNS = (
    "Company Name",
    "Project Name",
    "Toll Plaza Name",
    "Types of BG",
    "Nature of Service",
    "Name of Contractor",
    "BG No",
    "BG Amount",
    "From",
    "To",
    "Claim Period",
    "Bank Name",
    "LOA Number",
    "PO Number",
)

# Preferred base engine when the same evidence exists. Indra reads every page
# with layout; Tesseract remains the comparison and fallback engine.
_BASE_ORDER = ("indra", "tesseract", "azure")

_WORD_RE = re.compile(r"[a-z0-9]+")


def _key(value) -> str:
    """Comparison key: dates as ISO, everything else squashed and lowercased."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _overlap(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


def _filled(field) -> bool:
    return field is not None and field.value not in (None, "") and str(field.value).strip() != ""


def _engine_stats(results: dict[str, Extraction], expected_pages: int) -> dict:
    stats = {}
    for name, result in results.items():
        found = 0
        unflagged = 0
        for column in COMPARE_COLUMNS:
            field = result.fields.get(column)
            if not _filled(field):
                continue
            found += 1
            if not field.flags:
                unflagged += 1
        pages = len(result.ocr.pages)
        stats[name] = {
            "pages": pages,
            "pages_expected": expected_pages or pages,
            "page_coverage": (
                round(pages / expected_pages, 4) if expected_pages else 1.0
            ),
            "confidence": result.ocr.mean_conf,
            "confidence_source": result.ocr.confidence_source,
            "fields_found": found,
            "fields_unflagged": unflagged,
            "flags": sum(len(f.flags) for f in result.fields.values()),
        }
    return stats


def choose_base(results: dict[str, Extraction], requested: str) -> str:
    """Pick the extraction to start from.

    Page coverage comes first: an engine that read 1 of 22 pages must not win
    because it happened to extract one more field from that page. Then comes
    the number of unflagged fields, then raw field count and confidence.
    """
    stats = _engine_stats(results, max(len(r.ocr.pages) for r in results.values()))
    preferred = {
        "indra": 0,
        "both": 0,
        "tesseract": 1,
        "azure": 2,
    }.get(requested, 0)

    def rank(name: str):
        info = stats[name]
        position = _BASE_ORDER.index(name) if name in _BASE_ORDER else len(_BASE_ORDER)
        # A non-preferred single engine still beats a preferred one when its
        # coverage is materially better.
        return (
            info["page_coverage"],
            info["fields_unflagged"],
            info["fields_found"],
            info["confidence"],
            -abs(position - preferred),
            -position,
        )

    return max(results, key=rank)


def _pick_field(
    column: str, results: dict[str, Extraction], base: str
) -> tuple[object | None, str | None, bool, dict, dict, str]:
    """Return (field, chosen_engine, agreement, values, flags, note)."""
    values: dict[str, object] = {}
    flags: dict[str, list[str]] = {}
    present: dict[str, object] = {}
    for name, result in results.items():
        field = result.fields.get(column)
        if field is None:
            continue
        flags[name] = list(field.flags)
        if _filled(field):
            values[name] = field.value
            present[name] = field

    if not present:
        return None, None, True, values, flags, ""

    groups: dict[str, list[str]] = {}
    for name, field in present.items():
        groups.setdefault(_key(field.value), []).append(name)

    agreement = len(groups) == 1 and len(present) > 1
    # Majority first: two engines that agree beat one that does not. A tie
    # prefers the base engine's value.
    def group_rank(key: str):
        engines = groups[key]
        return (len(engines), 1 if base in engines else 0)

    winner_key = max(groups, key=group_rank)
    winner_engines = groups[winner_key]
    if len(winner_engines) > 1:
        choice = base if base in winner_engines else winner_engines[0]
        return (
            present[choice],
            choice,
            True,
            values,
            flags,
            "",
        )

    if len(groups) == 1:
        # Only one engine produced a value for this field.
        only = winner_engines[0]
        note = "" if only == base else f"only {only} read this"
        return present[only], only, False, values, flags, note

    # Disagreement. Prefer the base engine when its value is unflagged; then
    # any unflagged value; then the base; then the first engine.
    candidates = list(present)
    base_field = present.get(base)
    if base_field is not None and not base_field.flags:
        choice = base
    else:
        clean = [name for name in candidates if not present[name].flags]
        choice = clean[0] if clean else (base if base in present else candidates[0])
    others = ", ".join(
        f"{name}: {present[name].value}" for name in candidates if name != choice
    )
    return (
        present[choice],
        choice,
        False,
        values,
        flags,
        f"engines disagree ({others})",
    )


def merge(
    results: dict[str, Extraction],
    requested: str,
    today: dt.date | None = None,
) -> tuple[str, Extraction, dict]:
    """Merge the best field from each engine and describe the comparison."""
    base_name = choose_base(results, requested)
    base = results[base_name]
    merged: Extraction = copy.deepcopy(base)

    columns: dict[str, dict] = {}
    changed: list[str] = []
    disagreements: list[str] = []
    for column in COMPARE_COLUMNS:
        field, engine, agreement, values, flags, note = _pick_field(
            column, results, base_name
        )
        if field is None:
            continue
        base_field = base.fields.get(column)
        chose_other = engine != base_name and (
            not _filled(base_field) or _key(base_field.value) != _key(field.value)
        )
        if chose_other:
            replacement = copy.deepcopy(field)
            replacement.note(f"from_{engine}")
            if not agreement and note:
                replacement.flag("engine_disagrees")
                replacement.note(note)
            merged.fields[column] = replacement
            changed.append(column)
        else:
            if note:
                merged.fields[column].note(note)
            if not agreement and len(values) > 1:
                # The base value is kept, but the reviewer must see that the
                # other engine read something different.
                merged.fields[column].flag("engine_disagrees")
        if not agreement and len(values) > 1:
            disagreements.append(column)
        columns[column] = {
            "chosen": engine,
            "agreement": agreement,
            "values": {
                name: _json_value(value) for name, value in values.items()
            },
            "flags": flags,
            "note": note,
        }

    pages = max((len(result.ocr.pages) for result in results.values()), default=0)
    stats = _engine_stats(results, pages)
    page_rows = _page_rows(results, pages)
    comparison = {
        "base": base_name,
        "engines": stats,
        "columns": columns,
        "merged_fields": changed,
        "disagreements": disagreements,
        "pages": page_rows,
    }
    finalise(merged, today)
    return base_name, merged, comparison


def _json_value(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _page_rows(results: dict[str, Extraction], pages: int) -> list[dict]:
    rows: list[dict] = []
    names = list(results)
    for number in range(1, pages + 1):
        entry: dict = {"page": number, "engines": {}}
        texts: dict[str, str] = {}
        for name, result in results.items():
            page = next((p for p in result.ocr.pages if p.number == number), None)
            if page is None:
                entry["engines"][name] = None
                continue
            texts[name] = page.text
            entry["engines"][name] = {
                "chars": len(page.text),
                "confidence": page.mean_conf,
                "confidence_source": page.confidence_source,
            }
        if len(texts) > 1:
            pairs = {}
            for i, left in enumerate(names):
                for right in names[i + 1 :]:
                    if left in texts and right in texts:
                        pairs[f"{left}/{right}"] = _overlap(texts[left], texts[right])
            entry["overlap"] = pairs
        rows.append(entry)
    return rows


@dataclass
class Comparison:
    """Small holder used by tests and callers that want named results."""

    base: str
    extraction: Extraction
    report: dict = field(default_factory=dict)
