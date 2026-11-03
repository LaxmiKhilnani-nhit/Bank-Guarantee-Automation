"""Official-name lookup through the Firecrawl agent.

Uploads are still read locally. This module sends *only* two short strings -
the OCR'd bank name (plus any IFSC codes found next to it) and the OCR'd
contractor name - to Firecrawl's agent API, which returns ranked official
Indian names. The reviewer picks one in the existing dropdown, or keeps the
OCR text.

Design rules (see ``plan/00-log.md``, 2026-10-30):

* Two separate agent jobs per PDF, run in parallel: bank and contractor.
* The bank prompt gets the IFSC codes found in the OCR text, with a note that
  the issuing bank is the one we need (a guarantee can name the beneficiary's
  bank too).
* The agent's top match is selected by default. Every other match is offered
  in the dropdown together with ``as read: <OCR>``.
* A failure, timeout or empty answer never blocks the upload: the OCR value
  stays and the field is left as it was.
* Answers are cached in SQLite by the exact ``(field, raw text + codes)`` so
  re-reading the same wording does not bill twice. This is a cache of answers,
  not a local matcher.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import config, store

log = logging.getLogger(__name__)

FIELDS = {
    "bank": "Bank Name",
    "contractor": "Name of Contractor",
}

# Indian Financial System Code: 4 letters, 0, 6 alphanumerics.
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
# CIN: one letter + 5 digits + 2-letter state + 4-digit year + 3-letter type +
# 6 digits (e.g. L74899DL1995PLC070296). Used only to label a returned id.
CIN_RE = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
# LLPIN: three letters, hyphen, four digits (e.g. AAA-5012).
LLPIN_RE = re.compile(r"\b[A-Z]{3}-\d{4}\b")

_MAX_ERROR = 200


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def enabled() -> bool:
    """Lookup is on only when a key is configured and it was not switched off."""
    return bool(config.FIRECRAWL_API_KEY) and config.LOOKUP_ENABLED


# --- IFSC context --------------------------------------------------------


def ifsc_codes(text: str, limit: int = 3) -> list[dict]:
    """IFSC-shaped codes with the label line they appeared next to.

    The issuing bank's IFSC is listed first: the prompt uses it to tell the
    issuing bank apart from a beneficiary bank that is also named in the
    guarantee. Duplicates collapse to the first (or issuing) sighting.
    """
    lines = _clean_lines(text)
    found: dict[str, dict] = {}
    for index, line in enumerate(lines):
        upper = line.upper()
        for code in IFSC_RE.findall(upper):
            label = line
            # Some documents put the code on its own line under the label.
            rest = IFSC_RE.sub("", upper).strip(" :.-")
            if not rest and index:
                label = lines[index - 1]
            label = label.strip(" :.-")[:80]
            issuing = "ISSUING" in upper or (
                index > 0 and "ISSUING" in lines[index - 1].upper()
            )
            item = {"code": code, "label": label, "issuing": bool(issuing)}
            previous = found.get(code)
            if previous is None or (item["issuing"] and not previous["issuing"]):
                found[code] = item
    ordered = sorted(found.values(), key=lambda item: (not item["issuing"],))
    return ordered[:max(1, limit)]


def _clean_lines(text: str) -> list[str]:
    return [_clean(line) for line in str(text or "").splitlines() if line.strip()]


# --- prompts -------------------------------------------------------------


_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "description": "Ranked official names, best first. Empty when unsure.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Official legal name, correct spacing and case",
                    },
                    "identifier": {
                        "type": "string",
                        "description": "IFSC for a bank, CIN/LLPIN for a contractor company; empty if unknown",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "Page that proves the name",
                    },
                    "confidence": {
                        "type": "string",
                        "description": "high, medium or low",
                    },
                },
                "required": ["name"],
            },
        }
    },
    "required": ["matches"],
}

_BANK_PROMPT = """You are helping an Indian bank-guarantee register.

The OCR text of the guarantee shows the bank name as: "{raw}".
{context}
The bank we need is the ISSUING bank - the bank that issued this guarantee - \
not the beneficiary's bank, even if another bank is named in the document.

Find the official legal name of that Indian bank as it is used in India today \
(for example "HDFC Bank Limited", not "HDFC Bank Limtied"). Correct only the \
spacing, case and obvious OCR spelling. Do not invent a bank.
Return up to 5 Indian banks, best match first. Only Indian banks. If you are \
not reasonably sure, return an empty list. Put the bank's IFSC in identifier \
and the page that proves the name in source_url."""

_CONTRACTOR_PROMPT = """You are helping an Indian bank-guarantee register.

The OCR text of the guarantee shows the contractor name as: "{raw}".
This is the contractor/applicant company in an Indian bank guarantee.

Find the contractor company's official legal name as registered with the \
Ministry of Corporate Affairs in India (for example "National Highways Infra \
Investment Managers Private Limited"). Correct only the spacing, case and \
obvious OCR spelling. Do not invent or translate a name.
Return up to 5 Indian companies, best match first. Only companies registered in \
India. If you are not reasonably sure, return an empty list. Put the company's \
CIN (or LLPIN for an LLP) in identifier and the page that proves the name in \
source_url."""


def prompt_for(field: str, raw: str, codes: list[dict] | None = None) -> str:
    if field == "contractor":
        return _CONTRACTOR_PROMPT.format(raw=raw)
    context = ""
    if codes:
        described = ", ".join(
            f"{item['code']} ({item['label'] or 'unlabelled'})" for item in codes
        )
        context = (
            f"The document also contains these IFSC codes: {described}. "
            "The issuing bank's IFSC is the one labelled 'issuing'; the other "
            "may belong to the beneficiary's bank.\n"
        )
    return _BANK_PROMPT.format(raw=raw or "(not read)", context=context)


# --- Firecrawl agent client ---------------------------------------------


class LookupFailed(Exception):
    """An agent call that should fall back to the OCR value."""


def _request(url: str, payload: dict | None = None, timeout: float = 30) -> dict:
    data = None
    headers = {"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:300]
        raise LookupFailed(f"Firecrawl HTTP {error.code}: {body}") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise LookupFailed(f"Firecrawl request failed: {error}") from error


def call_agent(prompt: str) -> dict:
    """Start one agent job, poll it, and return its ``data`` object."""
    payload = {
        "prompt": prompt,
        "schema": _SCHEMA,
        "location": {"country": "IN"},
        "maxCredits": config.LOOKUP_MAX_CREDITS,
    }
    started = _request(config.FIRECRAWL_URL, payload, timeout=30)
    job_id = started.get("id") or started.get("jobId") or started.get("job_id")
    if not job_id:
        raise LookupFailed(f"Firecrawl did not return a job id: {started}")
    deadline = time.monotonic() + config.LOOKUP_TIMEOUT
    delay = max(1.0, config.LOOKUP_POLL)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LookupFailed(f"Firecrawl did not finish within {config.LOOKUP_TIMEOUT:.0f}s")
        body = _request(
            f"{config.FIRECRAWL_URL}/{job_id}",
            timeout=max(5.0, min(30.0, remaining)),
        )
        status = str(body.get("status") or "").lower()
        if status == "completed":
            data = body.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError as error:
                    raise LookupFailed("Firecrawl returned unreadable data") from error
            return data if isinstance(data, dict) else {"matches": data}
        if status in {"failed", "cancelled", "canceled"}:
            reason = body.get("error") or body.get("message") or status
            raise LookupFailed(f"Firecrawl {status}: {reason}")
        # Still processing (or a status we do not know): wait, then poll again.
        time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 1.5, 5.0)


# --- response parsing ----------------------------------------------------


def _match_name(item) -> str:
    if isinstance(item, str):
        return _clean(item)
    if not isinstance(item, dict):
        return ""
    for key in ("name", "legal_name", "official_name", "company", "bank", "title"):
        value = _clean(item.get(key))
        if value:
            return value
    return ""


def _identifier(item) -> tuple[str, str]:
    if not isinstance(item, dict):
        return "", ""
    for key in ("identifier", "ifsc", "cin", "code"):
        value = _clean(item.get(key)).replace(" ", "").upper()
        if not value:
            continue
        if IFSC_RE.fullmatch(value):
            return value, "IFSC"
        if CIN_RE.fullmatch(value):
            return value, "CIN"
        if LLPIN_RE.fullmatch(value):
            return value, "LLPIN"
        return value, ""
    return "", ""


def _source(item) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("source_url", "sourceUrl", "url", "source"):
        value = _clean(item.get(key))
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _confidence(item) -> str:
    if not isinstance(item, dict):
        return ""
    return _clean(item.get("confidence"))[:20]


def _lists(data) -> list:
    """Find the candidate list in whatever shape the agent answered with."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("matches", "results", "banks", "companies", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _lists(value)
            if nested:
                return nested
    for value in data.values():
        if isinstance(value, list) and value and isinstance(value[0], (dict, str)):
            return value
    return []


def parse_matches(data, limit: int | None = None) -> list[dict]:
    """Normalise the agent's answer: names de-duplicated, whitespace collapsed."""
    cap = max(1, int(limit or config.LOOKUP_MAX_MATCHES))
    matches: list[dict] = []
    seen: set[str] = set()
    for item in _lists(data):
        name = _match_name(item)
        if not name or len(name) > 200:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        identifier, kind = _identifier(item)
        matches.append(
            {
                "name": name,
                "identifier": identifier,
                "kind": kind,
                "source_url": _source(item),
                "confidence": _confidence(item),
            }
        )
        if len(matches) >= cap:
            break
    return matches


# --- cache ---------------------------------------------------------------

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS lookup_cache (
  field      TEXT NOT NULL,
  query      TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (field, query)
)
"""


def _cache_get(field: str, query: str) -> dict | None:
    try:
        with store.connect() as conn:
            conn.executescript(_CACHE_DDL)
            row = conn.execute(
                "SELECT payload FROM lookup_cache WHERE field = ? AND query = ? "
                "AND created_at >= datetime('now', ?)",
                (field, query, f"-{max(0, config.LOOKUP_TTL_DAYS)} days"),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"])
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_put(field: str, query: str, payload: dict) -> None:
    try:
        with store.connect() as conn:
            conn.executescript(_CACHE_DDL)
            conn.execute(
                "INSERT INTO lookup_cache (field, query, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(field, query) DO UPDATE SET payload = excluded.payload, "
                "created_at = datetime('now')",
                (field, query, json.dumps(payload, ensure_ascii=False)),
            )
    except sqlite3.Error:
        log.warning("could not cache the %s lookup", field, exc_info=True)


def _cache_query(raw: str, codes: list[dict]) -> str:
    suffix = ",".join(item["code"] for item in codes)
    return f"{raw}|{suffix}"


# --- the two lookups -----------------------------------------------------


Agent = Callable[[str], dict]


def lookup_field(
    field: str,
    raw: str,
    text: str,
    agent: Agent | None = None,
) -> dict:
    """One field: cache, agent call, ranked matches. Never raises."""
    raw = _clean(raw)
    codes = ifsc_codes(text) if field == "bank" else []
    if not raw and not codes:
        return {"status": "skipped", "raw": raw, "matches": [], "chosen": raw}
    if not enabled():
        return {"status": "disabled", "raw": raw, "matches": [], "chosen": raw}

    query = _cache_query(raw, codes)
    cached = _cache_get(field, query)
    if cached is not None:
        return {**cached, "cached": True}

    try:
        data = (agent or call_agent)(prompt_for(field, raw, codes))
    except Exception as error:  # network, timeout, bad answer: keep the OCR value
        log.warning("%s name lookup failed: %s", field, error)
        return {
            "status": "failed",
            "raw": raw,
            "matches": [],
            "chosen": raw,
            "error": _clean(error)[:_MAX_ERROR],
        }

    matches = parse_matches(data)
    result = {
        "status": "ok" if matches else "no_match",
        "raw": raw,
        "matches": matches,
        "chosen": matches[0]["name"] if matches else raw,
        "cached": False,
    }
    _cache_put(field, query, result)
    return result


def lookup_entry(values: dict, text: str, agent: Agent | None = None) -> dict:
    """Both lookups for one PDF, in parallel. Returns the manifest ``lookup``."""
    raw_bank = _clean(values.get(FIELDS["bank"]))
    raw_contractor = _clean(values.get(FIELDS["contractor"]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        bank = pool.submit(lookup_field, "bank", raw_bank, text, agent)
        contractor = pool.submit(lookup_field, "contractor", raw_contractor, text, agent)
        return {"bank": bank.result(), "contractor": contractor.result()}


def apply(values: dict, result: dict) -> None:
    """Select the top web match in ``values``; leave the OCR value otherwise."""
    for field, column in FIELDS.items():
        info = result.get(field) or {}
        if info.get("status") == "ok" and info.get("matches"):
            chosen = _clean(info["matches"][0].get("name"))
        else:
            chosen = ""
        info["chosen"] = chosen or _clean(values.get(column))
        if chosen:
            values[column] = chosen
