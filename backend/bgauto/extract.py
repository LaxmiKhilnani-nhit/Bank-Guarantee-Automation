"""Turn OCR text into the 21 tracker columns.

The rules follow ``plan/04-ocr-extraction.md``: label-anchored parsing, hard
validators, and a blank cell (plus a flag) whenever a check fails.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .masters import Masters, company_from_text
from .normalize import (
    Field,
    amount_in_words_to_number,
    bg_no_from_filename,
    match_bg_no_hint,
    normalise_bank,
    normalise_bg_no,
    parse_amount,
    parse_date,
    squash,
)
from .ocr import DocumentOCR

HEADERS = [
    "Company Name", "Project Name", "Toll Plaza Name", "Types of BG",
    "Nature of Service", "Name of Contractor", "BG No", "BG Amount",
    "From", "To", "Claim Period", "Extended", "Bank Name", "Status",
    "Remarks", "LOA Number", "PO Number", "source_pdf", "created_by", "created_at",
    "reminder_sent_at",
]

# Fields the machine never fills on its own. Project and Toll Plaza are matched
# against the masters when the document names them; otherwise left blank.
REVIEW_ONLY = ["Extended", "Remarks", "created_by"]
# Order matters: the most specific phrase wins. "General Management
# Consultancy Services for Operation and Management" must not fall through to
# the generic OPERATION hint, and "Toll Management System" must beat OPERATION.
_NATURE_HINTS = [
    (r"GENERAL\s+MANAGEMENT\s+CONSULTANCY", "General Management Consultancy Service"),
    (r"INCIDENT\s+MANAGEMENT", "Incident Management"),
    (r"BRIDGE\s+REPAIR|REPAIR\s*&\s*REHAB|REHABILITATION\s+OF\s+BRIDGE|REHABILITATION\s*-\s*BAREILLY", "Repair & rehabilititation"),
    (r"OVERHEAD\s+GANTRY", "Supply & Installation of Overhead Gantry"),
    (r"ROUTINE\s+MAINTENANCE", "Routine Maintenance"),
    (r"TOLL\s+MANAGEMENT\s+SYSTEM|\bTMS\b", "TMS"),
    (r"TRAFFIC\s+MANAGEMENT", "TMS"),
    (r"MANPOWER", "Manpower"),
    (r"ATMS", "ATMS"),
    (r"ROAD\s+MARKING", "Road Marking"),
    (r"SIGN\s*BOARD|SIGNBOARD", "Signboard Installation"),
    (r"STREET\s+LIGHT", "Street Light_ABG"),
    (r"TOLL\s+OPERATION|\bOPERATION\b", "Operation"),
    (r"HIGHWAY\s+PATROL|AMBULANCE|CRANE", "Incident Management"),
]


def _lines(ocr: DocumentOCR) -> list[tuple[int, str]]:
    lines = [(page.number, line) for page in ocr.pages for line in page.lines]
    for page in ocr.pages:
        lines.extend(
            (page.number, f"{key}: {value}")
            for key, value, _confidence in page.key_values
            if key.strip() and value.strip()
        )
    return lines


def _label_candidates(
    lines: list[tuple[int, str]], patterns: list[str]
) -> list[tuple[int, str, int]]:
    """Yield (line_index, value, page) for label/value pairs."""
    out: list[tuple[int, str, int]] = []
    for idx, (page, line) in enumerate(lines):
        upper = line.upper()
        for pattern in patterns:
            match = re.search(pattern, upper)
            if not match:
                continue
            rest = line[match.end():].strip(" :-|>\u00bb\t")
            if rest:
                out.append((idx, rest, page))
            elif idx + 1 < len(lines):
                nxt_page, nxt = lines[idx + 1]
                if nxt and not re.search(r"[A-Z ]{6,}\s*:", nxt):
                    out.append((idx + 1, nxt, nxt_page))
            break
    return out


def _first_parsed(candidates, parser):
    for idx, raw, page in candidates:
        value = parser(raw)
        if value is not None:
            return value, raw, page
    return None, None, None


def _best_master(value: str | None, options: list[str], cutoff: float = 0.82) -> str | None:
    if not value:
        return None
    match = difflib.get_close_matches(value, options, n=1, cutoff=cutoff)
    return match[0] if match else None


_CONTRACTOR_REJECT = re.compile(
    r"\b(?:and\s+his\s+details|and\s+any\s+request|including\s+claim|"
    r"based\s+on|hereinafter|agreement|guarantee|invocation|"
    r"beneficiary|concessionaire|the\s+contractor|the\s+bank|"
    r"registered\s+office)\b",
    re.IGNORECASE,
)


def _clean_contractor(raw: str | None) -> str | None:
    """Turn a label value or deed fragment into a company name, or None."""
    text = squash(raw or "")
    text = re.split(
        r"\s{2,}|\s+P\.?A\.?\s+NO|DESIGNATION|\bHAVING\s+ITS\b|"
        r"\bREGISTERED\s+OFFICE\b|\b2ND\s+FLOOR\b|\b3RD\s+FLOOR\b|"
        r"\b(?:ABOVE|NEAR|BEHIND|OPP\.?|PLOT|SECTOR|UNIT|FLAT|STREET)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.sub(r"^(?:m/s\.?|messrs\.?)\s+", "", text, flags=re.IGNORECASE)
    text = text.strip(" .,;:-")
    words = text.split()
    if len(words) < 2 or len(words) > 12 or len(text) > 90:
        return None
    if not re.search(r"[A-Z]{3,}", text.upper()):
        return None
    if _CONTRACTOR_REJECT.search(text):
        return None
    return text


def _contractor(lines, masters: Masters) -> Field:
    field = Field()
    candidates: list[tuple[int, str, int, str, str]] = []

    # 1. Deed wording: "(A) <name>, ... (hereinafter called the Contractor)".
    # Check this before labels so an SFMS ":7033: Name of applicant and his
    # details" page can never win.
    for idx, (page, line) in enumerate(lines):
        upper = line.upper()
        if "HEREINAFTER CALLED" not in upper or "CONTRACTOR" not in upper:
            continue
        for back in range(idx, max(idx - 6, -1), -1):
            text = lines[back][1]
            match = re.search(r"\(A\)\s*([A-Za-z0-9][A-Za-z0-9&.,'()\/\- ]{4,110}?)(?:,|$)", text)
            if not match:
                continue
            name = _clean_contractor(match.group(1))
            if name:
                candidates.append((100, name, lines[back][0], text, "from_deed_clause"))
                break
        if candidates:
            break

    # 2. "M/s. <name>" / "M/S <name>" appears in the WHEREAS clause of most
    # bank formats and in the letterhead of covering letters.
    if not candidates:
        for page, line in lines:
            for match in re.finditer(
                r"\bM/S\.?\s+([A-Za-z][A-Za-z&.,'()\- ]{3,110}?)"
                r"(?=\s+having\b|\s+\(|,|$)",
                line,
                re.IGNORECASE,
            ):
                name = _clean_contractor(match.group(1))
                if name:
                    candidates.append((80, name, page, match.group(0), "from_ms_clause"))
                    break

    # 3. ICICI wording: "... favouring yourself on behalf of: <NAME>, <address>".
    if not candidates:
        for idx, (page, line) in enumerate(lines):
            combined = line
            match = re.search(
                r"on\s+behalf\s+of:?\s*([A-Z][A-Z&.,'()\- ]{4,110}?)(?:,|\(|$)",
                combined,
                re.IGNORECASE,
            )
            if match and match.end() == len(line) and idx + 1 < len(lines):
                # The company name often wraps to the next OCR line.
                tail = match.group(1)
                if not re.search(
                    r"\b(?:PVT|PRIVATE|LIMITED|LTD|LLP|ENTERPRISES|SOLUTIONS|"
                    r"INDUSTRIES|INFRA|CONSTRUCTION)\b\.?$",
                    tail,
                    re.IGNORECASE,
                ):
                    combined = f"{line} {lines[idx + 1][1]}"
            match = re.search(
                r"on\s+behalf\s+of:?\s*([A-Z][A-Z&.,'()\- ]{4,110}?)(?:,|\(|$)",
                combined,
                re.IGNORECASE,
            )
            if not match:
                continue
            name = _clean_contractor(match.group(1))
            if name:
                candidates.append((75, name, page, match.group(0), "from_on_behalf_of"))
                break

    # 4. Explicit labels. "Name of applicant and his details" is an SFMS
    # column header, not a name, and is rejected by _clean_contractor.
    if not candidates:
        for page, line in lines:
            if ":7033:" in line:
                continue
            match = re.search(
                r"(?:APPLICANT|NAME\s+OF\s+(?:THE\s+)?CONTRACTOR)\s*[:>\-]\s*(.+)",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue
            name = _clean_contractor(match.group(1))
            if name:
                candidates.append((70, name, page, line, "from_label"))
                break

    # 4. SFMS advice: the applicant is on the line after ":7033:".
    if not candidates:
        for idx, (page, line) in enumerate(lines):
            if ":7033:" not in line:
                continue
            for _, nxt in lines[idx + 1 : idx + 4]:
                if ":7034:" in nxt:
                    break
                # The name is followed by the address on the same OCR line.
                name_part = re.split(
                    r"\s+(?:\d|\b(?:2ND|3RD|4TH|PLOT|SECTOR|UNIT|FLAT|FLOOR|"
                    r"ROAD|STREET|COMPLEX)\b)",
                    nxt,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0]
                name = _clean_contractor(name_part)
                if name:
                    candidates.append((60, name, page, nxt, "from_sfms"))
                    break
            break

    # 5. Letterhead fallback: a company suffix in the first lines of a covering
    # letter. Bank and beneficiary letterheads are rejected explicitly.
    if not candidates:
        for page, line in lines[:12]:
            upper = line.upper()
            if not re.search(
                r"\b(?:ENTERPRISES|INDUSTRIES|SOLUTIONS|CONSTRUCTION|INFRA|"
                r"LLP|PVT\.?\s+LTD|PRIVATE\s+LIMITED|LIMITED)\b",
                upper,
            ):
                continue
            if re.search(r"\b(?:NHIT|BANK|BENEFICIARY|GUARANTEE|INSURANCE)\b", upper):
                continue
            name = _clean_contractor(line)
            if name:
                candidates.append((40, name, page, line, "from_letterhead"))
                break

    if not candidates:
        field.flag("contractor_not_found")
        return field

    candidates.sort(key=lambda item: (-item[0], item[2]))
    _, name, page, raw, note = candidates[0]
    field.value = _best_master(name, masters.contractors) or name
    field.raw = raw
    field.page = page
    field.note(note)
    return field



def _is_company_registration(token: str) -> bool:
    """Company CIN fragments share the BG-number shape and must not be voted in."""
    return bool(
        re.fullmatch(r"\d{4}PLC\d{6}", token)
        or re.fullmatch(r"[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}", token)
    )


def _bg_no(ocr: DocumentOCR, lines) -> Field:
    field = Field()
    hint = bg_no_from_filename(ocr.source.stem)

    candidates = _label_candidates(lines, [
        r"OUR\s+REFERENCE", r"BG\s*NUMBER", r"BANK\s+GUARANTEE\s+NO\.?",
        r"\bBG\s+NO\.?", r"\bREF(?:ERENCE)?\s*:",
    ])
    votes: dict[str, int] = {}
    repaired_ids: set[str] = set()

    def record(token: str, weight: int) -> None:
        if _is_company_registration(token):
            return
        cleaned = normalise_bg_no(token)
        if cleaned:
            votes[cleaned] = votes.get(cleaned, 0) + weight
            repaired_ids.discard(cleaned)
            return
        fixed = match_bg_no_hint(token, hint)
        if fixed:
            votes[fixed] = votes.get(fixed, 0) + weight
            repaired_ids.add(fixed)

    for _, raw, page in candidates:
        token = re.search(r"[A-Z0-9][A-Z0-9 \-]{8,30}", raw.upper())
        if token:
            record(token.group(0), 2)

    # Global scan: any token shaped like a BG number (after OCR repair).
    for page, line in lines:
        for token in re.findall(r"[0-9OQILSBZG]{4,6}\s*[A-Z0-9]{2,5}\s*[0-9OQILSBZG]{6,12}", line.upper()):
            record(token, 1)

    if hint:
        votes[hint] = votes.get(hint, 0) + 3

    if not votes:
        field.flag("bg_no_not_found")
        return field

    best = max(votes, key=votes.get)
    field.value = best
    field.raw = str(votes)
    if hint and best == hint:
        field.confidence = 0.85 if best in repaired_ids else 0.95
        if best in repaired_ids:
            field.note("bg_no_repaired_from_filename")
    elif hint:
        field.flag("bg_no_differs_from_filename")
        field.confidence = 0.6
    else:
        field.confidence = 0.7
    return field


def _amount(lines) -> tuple[Field, Field]:
    amount = Field()
    words = Field()

    word_candidates = _label_candidates(lines, [r"AMOUNT\s+IN\s+WORDS"])
    word_value, word_raw, word_page = _first_parsed(word_candidates, lambda s: s or None)
    if word_value:
        words.raw = word_raw
        words.page = word_page
        words.value = amount_in_words_to_number(word_raw)

    candidates = _label_candidates(lines, [
        r"GUARANTEE\s+AMOUNT", r"AMOUNT\s+OF\s+BANK\s+GUARANTEE", r"BG\s+AMOUNT",
    ])
    value, raw, page = _first_parsed(candidates, parse_amount)

    if value is None:
        for p, line in lines:
            if "WORDS" in line.upper():
                continue
            match = re.search(r"(?:INR|RS\.?)\s*([\d,\s]+\.\d{2})", line.upper())
            if match:
                value = parse_amount(match.group(1))
                raw, page = match.group(0), p
                break

    if value is None:
        amount.flag("amount_not_found")
        return amount, words

    amount.value = value
    amount.raw = raw
    amount.page = page
    if words.value and abs(words.value - value) > 1:
        amount.flag("amount_vs_words_mismatch")
    return amount, words


def _date_field(lines, patterns: list[str], name: str) -> Field:
    field = Field()
    candidates = _label_candidates(lines, patterns)
    value, raw, page = _first_parsed(candidates, parse_date)
    if value is None:
        field.flag(f"{name}_not_found")
        return field
    field.value = value
    field.raw = raw
    field.page = page
    return field


def _scan_date(lines, patterns: list[str]) -> Field | None:
    """Find a pattern and parse the date that follows it on the same line."""
    for page, line in lines:
        upper = line.upper()
        for pattern in patterns:
            for match in re.finditer(pattern, upper):
                value = parse_date(upper[match.end(): match.end() + 45])
                if value:
                    return Field(value=value, raw=line, page=page, notes=["date_scanned"])
    return None


def _date_triplet(lines) -> tuple[list, int, str] | None:
    """Cover letters print issue/expiry/claim dates in one table row."""
    for page, line in lines:
        found = re.findall(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{4}", line)
        if len(found) >= 3:
            dates = [parse_date(token) for token in found[:3]]
            if all(dates) and dates[0] <= dates[1] <= dates[2]:
                return dates, page, line
    return None


def _fill(field: Field, value, raw: str, page: int, flag: str) -> None:
    field.value = value
    field.raw = raw
    field.page = page
    field.flags = [f for f in field.flags if not f.endswith("_not_found")]
    field.note(flag)


def _dates(lines) -> tuple[Field, Field, Field]:
    frm = _date_field(lines, [r"DATE\s+OF\s+ISSUE", r"ISSUANCE\s+DATE", r"DATE\s+OF\s+ISSUANCE"], "from")
    to = _date_field(lines, [r"EXPIRY\s+DATE", r"DATE\s+OF\s+EXPIRY", r"VALID\s+(?:TILL|UPTO|UNTIL)"], "to")
    claim = _date_field(lines, [r"CLAIM\s+EXPIRY\s+DATE", r"CLAIM\s+DATE", r"CLAIM\s+PERIOD"], "claim")

    scans = [
        (frm, [r"DATE\s+OF\s+ISSUE", r"ISSUANCE\s+DATE", r"DATE\s+OF\s+ISSUANCE"]),
        (to, [r"VALID\s+UP\s*TO", r"VALID\s+UPTO", r"VALID\s+TILL", r"VALID\s+UNTIL"]),
        (claim, [r"CLAIM\s+EXPIRY", r"CLAIM\s+DATE", r"CLAIM\s+PERIOD"]),
    ]
    for field, patterns in scans:
        if field.value is None:
            scanned = _scan_date(lines, patterns)
            if scanned:
                _fill(field, scanned.value, scanned.raw, scanned.page, "date_scanned")

    if any(f.value is None for f in (frm, to, claim)):
        triplet = _date_triplet(lines)
        if triplet:
            dates, page, line = triplet
            for field, value in zip((frm, to, claim), dates):
                if field.value is None:
                    _fill(field, value, line, page, "from_table_row")
    return frm, to, claim


_LINE_NO = re.compile(r"^\s*\d{1,2}\s*[.)]?\s+(?=[^0-9])")


def _flatten(lines) -> str:
    """Join lines into one string, dropping printed margin line numbers.

    Deeds are typeset with numbers down the left margin ("11 into an agreement
    ... WO No NSSPL/... / 12 Rehabilitation - Bareilly ..."). Joining raw lines
    would splice those numbers into the reference we are trying to read.
    """
    cleaned = []
    for _, line in lines:
        text = line.strip()
        if _LINE_NO.match(text):
            text = _LINE_NO.sub("", text, count=1).strip()
        if text:
            cleaned.append(text)
    return " ".join(cleaned)


_LOA_PATTERNS = [
    r"LETTER\s+OF\s+ACCEPTANCE\s*(?:NO\.?|NUMBER)?\s*[:\-]?\s*",
    r"LETTER\s+OF\s+AWARD\s*(?:NO\.?|NUMBER)?\s*[:\-]?\s*",
    r"LOA\s*(?:NO\.?|NUMBER)\s*[:\-]?\s*",
    r"LOA\s*[:\-]\s*",
    r"CONTRACT\s*(?:NO\.?|NUMBER)\s*[:\-]?\s*",
    r"AGREEMENT\s*(?:NO\.?|NUMBER)\s*[:\-]?\s*",
]
_PO_PATTERNS = [
    r"PURCHASE\s+ORDER\s*(?:NO\.?|NUMBER)?\s*[:\-]?\s*",
    r"P\.?O\.?\s*(?:NO\.?|NUMBER)\s*[:\-]?\s*",
    r"P\.?O\.?\s*[:\-]\s*",
    r"WORK\s+ORDER\s*(?:NO\.?|NUMBER)?\s*[:\-]?\s*",
    r"W\.?O\.?\s*(?:NO\.?|NUMBER)\s*[:\-]?\s*",
]

# The value ends at the first of these. "dated"/"dtd" deliberately terminate
# the number: the date is not part of the LOA/PO reference.
_LOA_STOP = re.compile(
    r'["”�]|\s+TO\s+EXECUTE\s+THE\s+CONTRACT|\s+FOR\s+THE\s+WORK\b|'
    r'\s+FOR\s+PROVIDING\b|\s+FOR\s+EXECUTION\b|\s+FOR\s+THE\s+\[|'
    r'\s+DATED\b|\s+DTD\b|\s+DT\.?\s*\d|\s+DEAR\s+SIR|\s+SUBJECT\s+TO\b|'
    r'\s+WITH\s+REFERENCE\b|\s+IN\s+ACCORDANCE\b',
    re.IGNORECASE,
)


def _clean_loa_tail(tail: str) -> str:
    tail = _LOA_STOP.split(tail, maxsplit=1)[0]
    tail = squash(tail).strip(" .,;:'\"-")
    # OCR often leaves a printed margin number and a stray quote in front.
    tail = re.sub(r"^\d{1,2}\s*[^\w/]+", "", tail).strip()
    tail = re.sub(r"^[^\w]+", "", tail).strip()
    return tail


def _extract_reference_field(lines, patterns, missing_flag: str) -> Field:
    """Read an explicitly labelled reference, preserving its field type."""
    flat = _flatten(lines)
    for pattern in patterns:
        match = re.search(pattern, flat, re.IGNORECASE)
        if not match:
            continue
        tail = _clean_loa_tail(flat[match.end():])
        if len(tail) < 5 or not re.search(r"\d", tail):
            continue
        tail = re.sub(r"\bBRIDAE\b", "Bridge", tail, flags=re.IGNORECASE)
        field = Field(value=tail[:160].strip(" .,;:'\"-"), raw=match.group(0) + tail)
        if re.search(r"\b(?:BRIDAE|LN|REHABILITATON)\b", tail, re.IGNORECASE):
            field.flag("loa_ocr_noise")
        return field

    return Field().flag(missing_flag)


def _loa(lines) -> Field:
    # A tender number stays a flagged LOA fallback for older source formats.
    found = _extract_reference_field(lines, _LOA_PATTERNS, "loa_not_found")
    if found.value is not None:
        return found
    flat = _flatten(lines)
    match = re.search(r"TENDER\s*(?:NO\.?)?\s*[:\-]\s*", flat, re.IGNORECASE)
    if match:
        tail = _clean_loa_tail(flat[match.end():])
        if len(tail) >= 5 and re.search(r"\d", tail):
            found = Field(value=tail[:160], raw=match.group(0) + tail)
            found.note("from_tender_no")
            return found
    return found


def _po(lines) -> Field:
    return _extract_reference_field(lines, _PO_PATTERNS, "po_not_found")


def _nature(lines, masters: Masters) -> Field:
    field = Field()
    # Deeds pad lines with margin numbers ("11 Rehabilitation - Bareilly").
    # Flatten them away first, or "Repair &" / "11 Rehabilitation" never joins
    # into the keyword the hint table looks for.
    text = _flatten(lines).upper()
    for pattern, canonical in _NATURE_HINTS:
        if re.search(pattern, text):
            match = _best_master(canonical, masters.natures, cutoff=0.75)
            field.value = match or canonical
            field.raw = pattern
            field.note("derived_from_text")
            if not match:
                field.flag("nature_not_in_master")
            return field
    field.flag("nature_not_found")
    return field


def _company(lines, masters: Masters, project: Field) -> Field:
    """Company from the resolved project, falling back to the OCR beneficiary.

    ``NSPPL`` and the other short forms are aliases, not companies: the stored
    value is always the full legal SPV name from the master.
    """
    text = " ".join(line for _, line in lines)
    ocr_company = company_from_text(text)
    project_company = masters.company_for_project(project.value)

    if project_company:
        field = Field(
            value=project_company,
            raw=f"project {project.value}",
            page=1,
        ).note("derived_from_project")
        if ocr_company and ocr_company != project_company:
            field.note(
                f"company_conflict: ocr={ocr_company}, project={project_company}"
            )
        if project_company not in masters.companies:
            field.flag("company_not_in_master")
        return field

    if ocr_company:
        field = Field(
            value=ocr_company, raw="beneficiary in document", page=1
        ).note("derived_from_beneficiary")
        if ocr_company not in masters.companies:
            field.flag("company_not_in_master")
        return field

    return Field().flag("company_not_found")


def _words(text: str) -> str:
    """Normalised, space-separated words: case, punctuation and OCR spacing go away."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).split())


def _mentions(corpus: str, phrase: str, min_len: int = 4) -> bool:
    """Whole-word containment, so 'Bassi' does not match 'Bassiana'."""
    if not phrase or len(phrase) < min_len:
        return False
    return f" {phrase} " in f" {corpus} "


def _reference(text: str, code: str) -> bool:
    """A project code only counts inside a reference: ``/KRK/``, ``[KRK]``, ``Project: KRK``."""
    token = re.escape(code.upper())
    if re.search(rf"[/\[(]{token}[/\])]", text):
        return True
    return (
        re.search(rf"(?:PROJECT|PACKAGE|CORRIDOR)\s*(?:CODE)?\s*[:\-]\s*{token}\b", text)
        is not None
    )


# Corridor names are sometimes seeded as toll-plaza rows. Separators ("-", "to")
# and OCR slips ("Bareily" for "Bareilly") must not stop the match.
_PROJECT_STOPWORDS = {
    "to", "and", "of", "the", "section", "from", "via", "nh", "sh", "km",
}


def _project_tokens(text: str) -> list[str]:
    return [
        word
        for word in _words(text).split()
        if len(word) >= 3 and word not in _PROJECT_STOPWORDS
    ]


def _fuzzy_word(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if len(left) < 3 or len(right) < 3:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _best_token_match(token: str, corpus_tokens: list[str]) -> float:
    best = 0.0
    for word in corpus_tokens:
        score = _fuzzy_word(token, word)
        if score > best:
            best = score
            if best == 1.0:
                break
    return best


def _project_candidates(
    lines, loa: Field, masters: Masters
) -> list[tuple[float, str]]:
    """Score project codes from corridor names and reference codes.

    The caller combines these with plaza hits: a plaza is the more specific
    signal, so it must be matched against *all* projects, not just this guess.
    """
    corpus_tokens = _project_tokens(" ".join(line for _, line in lines))
    references = "\n".join([loa.value or ""] + [line for _, line in lines]).upper()
    scored: list[tuple[float, str]] = []

    for option in masters.project_options or []:
        code = str(option.get("code") or "").strip()
        if not code:
            continue
        tokens = list(dict.fromkeys(_project_tokens(option.get("name") or "")))
        name_score = 0.0
        if tokens:
            ratios = [_best_token_match(token, corpus_tokens) for token in tokens]
            matched = sum(1 for ratio in ratios if ratio >= 0.84)
            if matched == len(tokens):
                name_score = 1.0
            elif matched >= 2 and matched / len(tokens) >= 0.6:
                # A long corridor name can be shortened in the document, but
                # two agreeing place names are strong enough to accept.
                name_score = 0.9 * matched / len(tokens)
        if name_score:
            scored.append((name_score, code))
        elif _reference(references, code):
            scored.append((0.95, code))

    scored.sort(key=lambda item: -item[0])
    unique: list[tuple[float, str]] = []
    seen: set[str] = set()
    for score, code in scored:
        if code not in seen:
            seen.add(code)
            unique.append((score, code))
    return unique


def _project(lines, loa: Field, masters: Masters) -> Field:
    """Project from text alone; kept for callers that do not have plazas."""
    scored = _project_candidates(lines, loa, masters)
    if not scored:
        return Field().note("project_not_found")
    top = scored[0][0]
    best = [code for score, code in scored if score == top]
    if len(best) == 1:
        return Field(value=best[0], raw="project name in document", page=1).note(
            "derived_from_text"
        )
    return Field().note("candidates: " + ", ".join(best))


_PLAZA_SPLIT = re.compile(r"\s*(?:&|/|,|\band\b)\s*", re.IGNORECASE)
_PLAZA_STOPWORDS = {
    "the", "at", "of", "near", "this", "said", "above", "a", "an", "and",
    "toll", "plaza", "bank", "guarantee", "performance", "advance", "our",
    "their", "its", "by", "for", "on", "in", "to", "from", "as", "is",
}
_PLAZA_WORD = re.compile(r"(?:Toll\s+)?Plazas?\b", re.IGNORECASE)
_PLAZA_PREFIX = re.compile(
    r"(?P<prefix>[A-Za-z][A-Za-z0-9.&'\-]*(?:\s+[A-Za-z][A-Za-z0-9.&'\-]*){0,3})\s*$",
    re.IGNORECASE,
)
_PLAZA_LABEL = re.compile(
    r"(?:Toll\s+)?Plazas?\s*(?:Name)?\s*[:\-]\s*(?P<value>.+)", re.IGNORECASE
)


def _trim_plaza_phrase(text: str) -> str:
    words = squash(text).strip(" .,;:-\u2013\u2014&/").split()
    while words and words[0].casefold() in _PLAZA_STOPWORDS:
        words.pop(0)
    while words and words[-1].casefold() in _PLAZA_STOPWORDS:
        words.pop()
    return " ".join(words)


def _split_plaza_phrases(text: str) -> list[str]:
    return [
        phrase
        for phrase in (_trim_plaza_phrase(part) for part in _PLAZA_SPLIT.split(text))
        if phrase and re.search(r"[A-Za-z]{3,}", phrase)
    ]


def _document_plazas(lines) -> list[tuple[str, bool]]:
    """Plaza names in document order, with a flag for explicit toll-plaza wording.

    ``True`` means the text said "Toll Plaza" (or used a "Plaza Name:" label), so
    an unmatched phrase must leave the cell blank. A bare "Plaza" ("American
    Plaza" in a bank branch address) is only considered when it matches a master
    plaza name.
    """
    found: dict[str, tuple[str, bool]] = {}
    for _, line in lines:
        for match in _PLAZA_WORD.finditer(line):
            explicit = bool(re.match(r"(?i)toll\s+", match.group(0)))
            label = _PLAZA_LABEL.match(line[match.start():])
            if label:
                explicit = True
                phrases = _split_plaza_phrases(label.group("value"))
            else:
                prefix = _PLAZA_PREFIX.search(
                    line[: match.start()].rstrip(" :-\u2013\u2014")
                )
                phrases = (
                    _split_plaza_phrases(prefix.group("prefix")) if prefix else []
                )
            for phrase in phrases:
                key = _words(phrase)
                if not key:
                    continue
                if key not in found:
                    found[key] = (phrase, explicit)
                elif explicit and not found[key][1]:
                    found[key] = (phrase, True)
    return list(found.values())


def _plaza_components(masters: Masters) -> list[dict]:
    """Split combined master rows ("Bhojpuri & Mudipar") into single plazas."""
    components: list[dict] = []
    seen: set[tuple[str | None, str]] = set()
    for plaza in masters.toll_plazas:
        project = plaza.get("project")
        for part in _PLAZA_SPLIT.split(plaza.get("name") or ""):
            name = _trim_plaza_phrase(part)
            if not name:
                continue
            key = (project, _words(name))
            if key in seen:
                continue
            seen.add(key)
            components.append(
                {
                    "project": project,
                    "name": name,
                    "tokens": _words(name).split(),
                    "order": len(components),
                }
            )
    return components


def _is_corridor_alias(component: dict, masters: Masters) -> bool:
    """A plaza row that is really the corridor name must not become a plaza."""
    tokens = component["tokens"]
    if not tokens:
        return True
    for option in masters.project_options or []:
        if option.get("code") != component["project"]:
            continue
        project_tokens = list(dict.fromkeys(_project_tokens(option.get("name") or "")))
        if len(project_tokens) != len(tokens):
            continue
        if all(
            _fuzzy_word(token, project_token) >= 0.84
            for token, project_token in zip(tokens, project_tokens)
        ):
            return True
    return False


def _match_phrase(phrase: str, components: list[dict]) -> dict | None:
    tokens = _words(phrase).split()
    if not tokens:
        return None
    best: tuple[float, int, dict] | None = None
    for component in components:
        other = component["tokens"]
        if len(other) != len(tokens):
            continue
        ratios = [_fuzzy_word(left, right) for left, right in zip(tokens, other)]
        if min(ratios) < 0.84:
            continue
        score = sum(ratios) / len(ratios)
        candidate = (score, -component["order"], component)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


def _plaza_hits(
    lines, masters: Masters
) -> tuple[list[dict], list[str], bool, bool]:
    """Match every document plaza against all projects, in document order.

    Returns ``(hits, unmatched, first_matched, explicit)``. ``explicit`` is
    true when the document actually says "Toll Plaza"; the fallback branch only
    looks for a master name mentioned as plain text.
    """
    components = [c for c in _plaza_components(masters) if not _is_corridor_alias(c, masters)]
    phrases = _document_plazas(lines)
    hits: list[dict] = []
    unmatched: list[str] = []
    first_matched = True

    if phrases:
        # Keep only phrases that are explicitly "Toll Plaza" or match a master
        # row. A bare "Plaza" in a bank address ("American Plaza") must not
        # become a documented plaza or an unmatched note.
        considered: list[tuple[str, bool, dict | None]] = []
        for phrase, explicit in phrases:
            match = _match_phrase(phrase, components)
            if match is not None or explicit:
                considered.append((phrase, explicit, match))

        for index, (phrase, _explicit, match) in enumerate(considered):
            if match is None:
                unmatched.append(phrase)
                if index == 0:
                    first_matched = False
                continue
            if not any(
                hit["name"] == match["name"] and hit["project"] == match["project"]
                for hit in hits
            ):
                hits.append(match)
        return hits, unmatched, first_matched, True

    # No "Toll Plaza" wording: fall back to a master name mentioned as-is.
    corpus = _words(" ".join(line for _, line in lines))
    for component in components:
        if _mentions(corpus, _words(component["name"])):
            hits.append(component)
    return hits, unmatched, True, False


def _resolve_project_plaza(
    lines, loa: Field, masters: Masters
) -> tuple[Field, Field, list[dict]]:
    """Resolve Project and Toll Plaza together, in either direction.

    A plaza is matched against every project first. That is the important
    difference from project-first filtering: a wrong project guess can no
    longer blank a plaza that is plainly written in the document.
    """
    candidates = _project_candidates(lines, loa, masters)
    text_code: str | None = None
    top_codes: list[str] = []
    if candidates:
        top_score = candidates[0][0]
        top_codes = [code for score, code in candidates if score == top_score]
        if len(top_codes) == 1:
            text_code = top_codes[0]

    hits, unmatched, first_matched, explicit = _plaza_hits(lines, masters)
    hit_projects = list(
        dict.fromkeys(hit["project"] for hit in hits if hit.get("project"))
    )

    project = Field()
    if len(hit_projects) == 1:
        # The plaza is concrete and sits inside one corridor, so it wins even
        # if a noisy project reference points somewhere else.
        plaza_project = hit_projects[0]
        project = Field(
            value=plaza_project, raw="plaza in document", page=1
        ).note("derived_from_plaza")
        if text_code == plaza_project:
            project.raw = "project name in document"
            project.notes = ["derived_from_text"]
        elif text_code:
            project.note(f"project_conflict: text={text_code}, plaza={plaza_project}")
    elif len(hit_projects) > 1:
        if text_code in hit_projects:
            project = Field(
                value=text_code, raw="project name in document", page=1
            ).note("derived_from_text")
            project.note("plazas span projects; each matched plaza gets a row")
        else:
            project = Field().note("candidates: " + ", ".join(hit_projects))
    elif not candidates:
        project = Field().note("project_not_found")
    elif text_code:
        project = Field(
            value=text_code, raw="project name in document", page=1
        ).note("derived_from_text")
    else:
        project = Field().note("candidates: " + ", ".join(top_codes))

    plaza = Field()
    if not hits:
        plaza.note("plaza_not_found")
        if unmatched:
            plaza.note("unmatched: " + ", ".join(unmatched[:4]))
        return project, plaza, []

    if explicit and not first_matched:
        # The first plaza in the document is not in the master: leave the cell
        # blank rather than silently substituting a later plaza.
        plaza.note("plaza_not_found")
        plaza.note("unmatched: " + ", ".join(unmatched[:4]))
        return project, plaza, hits

    plaza.value = hits[0]["name"]
    plaza.raw = "plaza name in document"
    plaza.page = 1
    plaza.note("derived_from_text")
    if len(hits) > 1:
        plaza.note("also_covers: " + ", ".join(hit["name"] for hit in hits[1:]))
    if unmatched:
        plaza.note("unmatched: " + ", ".join(unmatched[:4]))
    return project, plaza, hits


def _type_of_bg(lines) -> Field:
    text = " ".join(line for _, line in lines).upper()
    if "PERFORMANCE SECURITY" in text or "PERFORMANCE GUARANTEE" in text:
        return Field(value="PBG", raw="performance security")
    if "ADVANCE" in text:
        return Field(value="ABG", raw="advance guarantee")
    return Field().flag("bg_type_not_found")


@dataclass
class Extraction:
    source: Path
    ocr: DocumentOCR
    fields: dict[str, Field] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Every master plaza named in the document, in document order. The review
    # card uses the first one; the workbook gets one row per plaza.
    plazas: list[str] = field(default_factory=list)

    def value(self, name: str):
        f = self.fields.get(name)
        return f.value if f else None

    def row(self, created_at: dt.datetime | None = None) -> dict:
        stamp = created_at or dt.datetime.now()
        row = {name: None for name in HEADERS}
        for name, f in self.fields.items():
            if name in HEADERS:
                row[name] = f.value
        row["source_pdf"] = self.source.name
        row["created_at"] = stamp
        return row

    def report(self) -> dict:
        return {
            "source_pdf": self.source.name,
            "ocr_mean_confidence": self.ocr.mean_conf,
            "pages": len(self.ocr.pages),
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "fields": {
                name: {
                    "value": f.value.isoformat() if isinstance(f.value, (dt.date, dt.datetime)) else f.value,
                    "raw": f.raw,
                    "page": f.page,
                    "confidence": f.confidence,
                    "flags": f.flags,
                    "notes": f.notes,
                }
                for name, f in self.fields.items()
            },
            "warnings": self.warnings,
        }


def extract(ocr: DocumentOCR, masters: Masters, today: dt.date | None = None) -> Extraction:
    today = today or dt.date.today()
    lines = _lines(ocr)
    result = Extraction(source=ocr.source, ocr=ocr)

    loa = _loa(lines)
    project, plaza, plaza_hits = _resolve_project_plaza(lines, loa, masters)
    result.fields["Company Name"] = _company(lines, masters, project)
    result.plazas = [hit["name"] for hit in plaza_hits]
    result.fields["Project Name"] = project
    result.fields["Toll Plaza Name"] = plaza
    result.fields["Extended"] = Field().note("review_dropdown")
    result.fields["Types of BG"] = _type_of_bg(lines)
    result.fields["Nature of Service"] = _nature(lines, masters)
    result.fields["Name of Contractor"] = _contractor(lines, masters)
    result.fields["BG No"] = _bg_no(ocr, lines)
    amount, words = _amount(lines)
    result.fields["BG Amount"] = amount
    frm, to, claim = _dates(lines)
    result.fields["From"] = frm
    result.fields["To"] = to
    result.fields["Claim Period"] = claim
    result.fields["Bank Name"] = Field(
        value=normalise_bank(ocr.text),
        raw="bank keyword match",
    )
    if result.fields["Bank Name"].value is None:
        result.fields["Bank Name"].flag("bank_not_matched")
    result.fields["Remarks"] = Field().note("review_free_text")
    result.fields["LOA Number"] = loa
    result.fields["PO Number"] = _po(lines)
    result.fields["source_pdf"] = Field(value=ocr.source.name)
    result.fields["created_by"] = Field().note("set_on_confirm")
    result.fields["created_at"] = Field(value=dt.datetime.now())
    result.fields["reminder_sent_at"] = Field().note("set_by_scheduler")

    # Status is deterministic but still flagged: the reviewer confirms it.
    finalise(result, today)
    return result


def finalise(result: Extraction, today: dt.date | None = None) -> Extraction:
    """Recompute derived fields and cross-field flags.

    Also called after the engine comparison swaps a field, so Status and the
    date checks always describe the values the reviewer is looking at.
    """
    today = today or dt.date.today()
    to_value = result.value("To")

    # Status is deterministic. It is always recomputed: a merged ``To`` value
    # would otherwise leave yesterday's status in place.
    if isinstance(to_value, dt.date):
        status = "Expired" if to_value < today else "Active"
        result.fields["Status"] = Field(
            value=status, raw="computed from To", notes=["auto_status"]
        )
    else:
        result.fields["Status"] = Field().note("status_needs_review")

    # Cross-field checks. Clear the flags this function owns before re-adding
    # them so a merged value can resolve a stale conflict.
    owned = {
        "From": {"from_after_to"},
        "To": {"to_before_from"},
        "Claim Period": {"claim_before_to"},
    }
    for name, flags in owned.items():
        field = result.fields.get(name)
        if field is not None:
            field.flags[:] = [flag for flag in field.flags if flag not in flags]

    from_value = result.value("From")
    claim_value = result.value("Claim Period")
    if isinstance(from_value, dt.date) and isinstance(to_value, dt.date) and to_value < from_value:
        result.fields["From"].flag("from_after_to")
        result.fields["To"].flag("to_before_from")
    if isinstance(to_value, dt.date) and isinstance(claim_value, dt.date) and claim_value < to_value:
        result.fields["Claim Period"].flag("claim_before_to")

    for f in result.fields.values():
        # A note means the blank is intentional (reviewer dropdown, scheduler
        # column), so it is not a blocking gap.
        if f.value is None and not f.flags and not f.notes:
            f.flag("blank")
    return result
