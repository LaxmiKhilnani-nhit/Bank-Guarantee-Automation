"""Server-side state for the review UI: runs, edits and confirmed rows.

A *run* is one sitting of "upload a batch of PDFs, check the fields, save".
Everything it produces lives under ``out/web/<run_id>/`` so a reviewer can see
exactly what was read, what was changed, and what was written.

The real ``BG Tracker.xlsx`` is only ever read (as a template). Confirmed rows
land in ``out/BG Tracker_ingested.xlsx`` - the same copy the CLI uses - and in
the PostgreSQL register that feeds the Register screen and the reminders.

The uploader owns the run and can save it. Ownership is checked here, not just
in the UI; admins can access all runs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .. import config, excel, lookup, pipeline, register, store
from ..auth import User
from ..extract import HEADERS, Extraction, extract_from_pipeline
from ..masters import Masters
from ..normalize import normalise_key

log = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
MAX_PDF_BYTES = 25 * 1024 * 1024
DATE_COLUMNS = {"From", "To", "Claim Period"}
DATETIME_COLUMNS = {"created_at", "reminder_sent_at"}
AMOUNT_COLUMNS = {"BG Amount"}
# Mandatory before a row can reach the register or the workbook. Status is
# derived, and Toll Plaza/Claim Period/Remarks stay optional on purpose.
REQUIRED_COLUMNS = (
    "BG No",
    "Company Name",
    "Project Name",
    "Name of Contractor",
    "Types of BG",
    "Nature of Service",
    "BG Amount",
    "From",
    "To",
    "Bank Name",
)
RUN_STATUSES = ("draft", "confirmed")

_masters_cache: tuple[float, Masters] | None = None
# The UI asks for masters on every upload; a short TTL keeps that cheap while
# still showing an admin's master edit within a minute.
_MASTERS_TTL = 60.0


def masters() -> Masters:
    """Load masters at most once per TTL (the UI asks on every upload)."""
    global _masters_cache
    now = time.monotonic()
    if _masters_cache and now - _masters_cache[0] < _MASTERS_TTL:
        return _masters_cache[1]
    loaded = Masters.load()
    _masters_cache = (now, loaded)
    return loaded


# --- cross-process locks -------------------------------------------------


if os.name == "nt":

    def _lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:

    def _lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _file_lock(path: Path):
    """Hold an exclusive lock on ``path`` for the duration of the block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)


# --- run storage ---------------------------------------------------------


def _check_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid run id")
    return run_id


def run_dir(run_id: str) -> Path:
    return config.RUNS_DIR / _check_id(run_id)


def _manifest_path(run_id: str) -> Path:
    return run_dir(run_id) / "manifest.json"


def _run_lock_path(run_id: str) -> Path:
    return run_dir(run_id) / ".lock"


def _workbook_lock_path() -> Path:
    return config.WORKBOOK_PATH.parent / ".workbook.lock"


def create_run(owner: User) -> dict:
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    directory = run_dir(run_id)
    (directory / "uploads").mkdir(parents=True, exist_ok=True)
    (directory / "ocr").mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "owner_id": owner.id,
        "owner": owner.username,
        "status": "draft",
        "files": [],
    }
    save_manifest(run_id, manifest)
    return manifest


def load_manifest(run_id: str) -> dict:
    path = _manifest_path(run_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown run {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(run_id: str, manifest: dict) -> None:
    _manifest_path(run_id).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def is_owner(manifest: dict, user: User) -> bool:
    return user.role == "admin" or manifest.get("owner_id") == user.id


def require_run(run_id: str, user: User, *, write: bool = False) -> dict:
    """Load a run and enforce ownership; raises PermissionError/FileNotFoundError."""
    manifest = load_manifest(run_id)
    if not is_owner(manifest, user):
        raise PermissionError("This run belongs to another uploader.")
    if write and manifest.get("status") == "confirmed":
        raise PermissionError("This run is already confirmed and cannot be edited.")
    if write and user.role == "viewer":
        raise PermissionError("Viewers cannot edit runs.")
    return manifest


def list_runs(user: User, *, limit: int = 50) -> list[dict]:
    if not config.RUNS_DIR.is_dir():
        return []
    manifests: list[dict] = []
    for path in config.RUNS_DIR.glob("*/manifest.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not is_owner(manifest, user):
            continue
        manifests.append(manifest)
    manifests.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    result = []
    for manifest in manifests[:limit]:
        files = manifest.get("files") or []
        result.append(
            {
                "run_id": manifest["run_id"],
                "created_at": manifest.get("created_at"),
                "owner": manifest.get("owner", ""),
                "status": manifest.get("status", "draft"),
                "files": len(files),
                "open_flags": sum(
                    1 for entry in files if open_flags(entry, entry.get("values") or {})
                ),
                "confirmed_at": manifest.get("confirmed_at"),
            }
        )
    return result


# --- extraction ----------------------------------------------------------


def _json_value(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


# Columns a reviewer may change. The rest are server-owned and must not come
# from the browser.
_REVIEW_FIELDS = set(HEADERS) - {
    "source_pdf", "created_by", "created_at", "reminder_sent_at",
}


def _values_from(extraction: Extraction) -> dict:
    row = extraction.row()
    return {name: _json_value(row.get(name)) for name in HEADERS}


def public_entry(entry: dict) -> dict:
    """What the browser needs; keeps server-only paths out of responses."""
    return {
        "source_pdf": entry["source_pdf"],
        "pages": entry["pages"],
        "ocr_confidence": entry["ocr_confidence"],
        "ocr_confidence_source": entry.get("ocr_confidence_source", "ocr"),
        "ocr_engine": entry.get("ocr_engine"),
        "ocr_requested_engine": entry.get("ocr_requested_engine"),
        "ocr_variants": entry.get("ocr_variants") or {},
        "ocr_comparison": entry.get("ocr_comparison") or {},
        "ocr_errors": entry.get("ocr_errors") or {},
        "fields": entry["fields"],
        "values": entry["values"],
        "extracted_values": entry["extracted_values"],
        "extras": entry.get("extras") or {},
        "pipeline": entry.get("pipeline") or {},
        "lookup": entry.get("lookup") or {},
        "plazas": entry.get("plazas") or [],
    }


def _unique_name(directory: Path, name: str) -> str:
    candidate = directory / name
    if not candidate.exists():
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    for counter in range(2, 100):
        alt = f"{stem} ({counter}){suffix}"
        if not (directory / alt).exists():
            return alt
    raise ValueError("too many files with the same name")


def _entry_with_hash(manifest: dict, digest: str) -> dict | None:
    for entry in manifest["files"]:
        if entry.get("sha256") == digest:
            return entry
    return None


def _reason(error: Exception) -> str:
    text = " ".join(str(error).split())
    return text[:200] if text else error.__class__.__name__


def _quality(result: Extraction) -> tuple[int, int, float]:
    """Rank an extraction: most required fields, fewest flags, then confidence."""
    found = sum(1 for column in REQUIRED_COLUMNS if not _blank(result.value(column)))
    flags = sum(len(field.flags) for field in result.fields.values())
    return found, -flags, result.ocr.mean_conf


def _progress_set(run_id: str, name: str, payload: dict) -> None:
    """Best-effort progress for the review UI. Never breaks an upload."""
    try:
        with _file_lock(_run_lock_path(run_id)):
            manifest = load_manifest(run_id)
            progress = manifest.setdefault("progress", {})
            if payload is None:
                progress.pop(name, None)
            else:
                progress[name] = payload
            save_manifest(run_id, manifest)
    except Exception:
        log.debug("could not write OCR progress for %s", name, exc_info=True)


def add_pdf(
    run_id: str,
    filename: str,
    data: bytes,
    user: User,
) -> dict:
    """Store one upload, run the Datalab pipeline and append its extraction."""
    original = Path(filename or "upload.pdf").name
    name = original
    if not name.lower().endswith(".pdf"):
        raise ValueError("only PDF files are accepted")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError(f"{name} is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF"):
        raise ValueError(f"{name} does not look like a PDF")

    digest = hashlib.sha256(data).hexdigest()
    manifest_path = _manifest_path(run_id)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"unknown run {run_id}")
    require_run(run_id, user, write=True)

    with _file_lock(_run_lock_path(run_id)):
        manifest = load_manifest(run_id)
        existing = _entry_with_hash(manifest, digest)
        if existing is not None:
            return {**public_entry(existing), "duplicate": True}
        upload_dir = run_dir(run_id) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        name = _unique_name(upload_dir, name)
        pdf_path = upload_dir / name
        pdf_path.write_bytes(data)

    requested = (config.OCR_ENGINE or "datalab").strip().lower()
    started = dt.datetime.now()
    _progress_set(
        run_id,
        name,
        {
            "engine": requested,
            "page": 0,
            "pages_total": 0,
            "percent": 0,
            "started_at": started.isoformat(timespec="seconds"),
            "elapsed": 0,
            "stage": "starting",
        },
    )

    def progress(update: dict) -> None:
        _progress_set(
            run_id,
            name,
            {
                "engine": update.get("engine", requested),
                "page": update.get("page", 0),
                "pages_total": update.get("pages_total", 0),
                "percent": update.get("percent"),
                "page_seconds": update.get("seconds"),
                "started_at": started.isoformat(timespec="seconds"),
                "elapsed": round((dt.datetime.now() - started).total_seconds(), 1),
                "stage": update.get("stage", "reading"),
            },
        )

    try:
        masters_obj = masters()
        pipeline_result = pipeline.run(pdf_path, progress=progress)
        document = pipeline_result.document
        result = extract_from_pipeline(
            document,
            pipeline_result.data,
            masters_obj,
            pipeline_meta=pipeline_result.meta(),
        )
        _progress_set(run_id, name, None)
        _archive(run_id, result, requested, pipeline_result.raw())
    except Exception as error:
        _progress_set(run_id, name, None)
        pdf_path.unlink(missing_ok=True)
        raise ValueError(f"Could not read {original}: {_reason(error)}") from error

    extracted = _values_from(result)
    extracted["source_pdf"] = name
    values = dict(extracted)

    # Optional official-name lookup. Only the two names and IFSC codes leave
    # the machine; failures leave the OCR values untouched. The upload waits
    # for both answers so the review card is complete when it appears.
    text = "\n".join(page.text for page in document.pages)
    try:
        lookup_result = lookup.lookup_entry(values, text)
        lookup.apply(values, lookup_result)
    except Exception as error:  # never block a run on the optional lookup
        log.warning("name lookup failed for %s: %s", name, error)
        lookup_result = {}

    entry = {
        "source_pdf": name,
        "stored_pdf": f"uploads/{name}",
        "sha256": digest,
        "pages": result.pipeline.get("pages") or len(document.pages),
        "ocr_confidence": document.mean_conf,
        "ocr_confidence_source": document.confidence_source,
        "ocr_engine": result.ocr.engine,
        "ocr_mode": pipeline_result.extraction_mode or config.DATALAB_MODE,
        "extraction_mode": pipeline_result.extraction_mode,
        "pipeline": pipeline_result.meta(),
        "ocr_requested_engine": requested,
        "ocr_variants": {
            result.ocr.engine: {
                "confidence": result.ocr.mean_conf,
                "confidence_source": result.ocr.confidence_source,
                "pages": len(result.ocr.pages),
                "found_fields": _quality(result)[0],
                "flags": -_quality(result)[1],
                "selected": True,
            }
        },
        "ocr_comparison": {},
        "ocr_errors": {},
        "extracted_at": dt.datetime.now().isoformat(timespec="seconds"),
        "fields": result.report()["fields"],
        "values": values,
        "extracted_values": extracted,
        "extras": result.extras,
        "lookup": lookup_result,
        "plazas": result.plazas,
    }

    with _file_lock(_run_lock_path(run_id)):
        manifest = load_manifest(run_id)
        existing = _entry_with_hash(manifest, digest)
        if existing is not None:
            pdf_path.unlink(missing_ok=True)
            return {**public_entry(existing), "duplicate": True}
        manifest["files"].append(entry)
        save_manifest(run_id, manifest)
    with store.connect() as conn:
        store.audit(
            conn,
            "upload",
            user_id=user.id,
            new={"run_id": run_id, "source_pdf": name, "sha256": digest, "pages": entry["pages"]},
        )
        store.audit(
            conn,
            "extract",
            user_id=user.id,
            new={"run_id": run_id, "source_pdf": name, "confidence": entry["ocr_confidence"]},
        )
    return {**public_entry(entry), "duplicate": False}


def _archive(
    run_id: str,
    result: Extraction,
    requested: str | None = None,
    raw_pipeline: dict | None = None,
) -> None:
    target = run_dir(run_id) / "ocr" / f"{result.source.stem}.json"
    payload = {
        "source_pdf": result.source.name,
        "engine": result.ocr.engine,
        "engine_requested": requested or result.ocr.engine,
        "mode": result.pipeline.get("extraction_mode") or config.DATALAB_MODE,
        "pipeline": result.pipeline,
        "page_count": result.pipeline.get("pages"),
        "engine_errors": {},
        "engines": {
            result.ocr.engine: {
                "confidence": result.ocr.mean_conf,
                "confidence_source": result.ocr.confidence_source,
                "pages": len(result.ocr.pages),
                "found_fields": _quality(result)[0],
                "flags": -_quality(result)[1],
                "selected": True,
            }
        },
        "comparison": {},
        "ocr": {
            "mean_confidence": result.ocr.mean_conf,
            "pages": [
                {
                    "page": page.number,
                    "mean_confidence": page.mean_conf,
                    "confidence_source": page.confidence_source,
                    "text": page.text,
                }
                for page in result.ocr.pages
            ],
        },
        "extraction": result.report(),
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if raw_pipeline:
        pipeline_dir = run_dir(run_id) / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        raw_target = pipeline_dir / f"{result.source.stem}.json"
        raw_target.write_text(
            json.dumps(raw_pipeline, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# --- review helpers ------------------------------------------------------


def _blank(value) -> bool:
    return value is None or str(value).strip() == ""


def open_flags(entry: dict, values: dict) -> dict[str, list[str]]:
    """Flags that still stand: the field failed AND the reviewer did not change it."""
    still_open: dict[str, list[str]] = {}
    for column, meta in entry["fields"].items():
        flags = meta.get("flags") or []
        if not flags:
            continue
        before = entry["extracted_values"].get(column)
        after = values.get(column)
        same = (before or "") == (after or "")
        if same or _blank(after):
            still_open[column] = flags
    return still_open


def _web_origins(entry: dict, values: dict) -> dict[str, str]:
    """Master columns whose confirmed value came from a Firecrawl match."""
    result: dict[str, str] = {}
    lookup_info = entry.get("lookup") or {}
    for field, column in lookup.FIELDS.items():
        info = lookup_info.get(field) or {}
        if info.get("status") != "ok":
            continue
        names = {
            str(match.get("name") or "").strip()
            for match in (info.get("matches") or [])
            if str(match.get("name") or "").strip()
        }
        if str(values.get(column) or "").strip() in names:
            result[column] = "web"
    return result


def _coerce(column: str, value):
    if _blank(value):
        return None
    if column in DATE_COLUMNS:
        if isinstance(value, dt.date):
            return value
        return dt.date.fromisoformat(str(value)[:10])
    if column in DATETIME_COLUMNS:
        if isinstance(value, dt.datetime):
            return value
        text = str(value).replace("Z", "")
        try:
            return dt.datetime.fromisoformat(text)
        except ValueError:
            return dt.datetime.fromisoformat(text[:10])
    if column in AMOUNT_COLUMNS:
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).replace(",", "").strip())
    return str(value).strip()


def _same_value(column: str, before, after) -> bool:
    """Compare two review values the way the workbook would store them."""
    if _blank(before) and _blank(after):
        return True
    if _blank(before) or _blank(after):
        return False
    try:
        return _coerce(column, before) == _coerce(column, after)
    except (TypeError, ValueError):
        return str(before).strip() == str(after).strip()


def _not_written(
    source: str,
    reason: str,
    missing: list[str] | None = None,
    flags: dict | None = None,
    error: str | None = None,
    existing: dict | None = None,
) -> dict:
    return {
        "source_pdf": source,
        "reason": reason,
        "missing": missing or [],
        "flags": flags or {},
        "error": error,
        "existing": existing,
    }


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(path)


# --- confirm -------------------------------------------------------------


class ConfirmError(Exception):
    def __init__(self, message: str, payload: dict | None = None, status: int = 409):
        super().__init__(message)
        self.payload = payload or {}
        self.status = status


def _plaza_rows(row: dict, entry: dict, edits: dict) -> list[dict]:
    """One workbook row per plaza, unless the reviewer picked a single plaza."""
    reviewed = edits.get("Toll Plaza Name")
    if reviewed not in (None, "") and not _same_value(
        "Toll Plaza Name", entry["values"].get("Toll Plaza Name"), reviewed
    ):
        return [row]
    plazas = [str(p) for p in (entry.get("plazas") or []) if p]
    if len(plazas) <= 1:
        return [row]
    primary = row.get("Toll Plaza Name")
    ordered: list[str] = []
    if primary:
        ordered.append(str(primary))
    for plaza in plazas:
        if normalise_key(plaza) not in {normalise_key(item) for item in ordered}:
            ordered.append(plaza)
    if not ordered:
        return [row]
    workbook_rows: list[dict] = []
    for plaza in ordered:
        copy = dict(row)
        copy["Toll Plaza Name"] = plaza
        workbook_rows.append(copy)
    return workbook_rows


def confirm(
    run_id: str,
    user: User,
    rows: list[dict],
    include_flagged: bool = False,
) -> dict:
    """Validate reviewed rows, then write the workbook copy and the register."""
    if user.role not in {"admin", "uploader"}:
        raise PermissionError("Only an uploader can write their run into the workbook.")
    if not rows:
        raise ConfirmError("Nothing to confirm.")

    manifest = require_run(run_id, user, write=True)
    by_name = {entry["source_pdf"]: entry for entry in manifest["files"]}

    prepared: list[dict] = []
    problems: dict[str, dict] = {}
    not_written: list[dict] = []
    edits: dict[str, dict] = {}
    status_recomputed: list[str] = []
    seen: set[str] = set()
    origins: dict[str, dict[str, str]] = {}
    today = dt.date.today()

    for update in rows:
        source = update.get("source_pdf")
        entry = by_name.get(source)
        if entry is None:
            raise ConfirmError(f"{source} is not part of this run.", status=400)
        values = {**entry["values"], **(update.get("values") or {})}

        changes = {
            column: {
                "from": _json_value(entry["extracted_values"].get(column)),
                "to": _json_value(values.get(column)),
            }
            for column in HEADERS
            if column != "source_pdf"
            and not _same_value(
                column, entry["extracted_values"].get(column), values.get(column)
            )
        }
        if changes:
            edits[source] = changes

        # Missing mandatory fields are never bypassable; include_flagged only
        # waves through OCR warnings the admin has checked against the paper.
        missing = [c for c in REQUIRED_COLUMNS if _blank(values.get(c))]
        if missing:
            problems[source] = {"missing": missing, "flags": {}}
            not_written.append(_not_written(source, "missing_required", missing=missing))
            continue

        flags = open_flags(entry, values)
        if flags:
            problems[source] = {"missing": [], "flags": flags}
            if not include_flagged:
                not_written.append(_not_written(source, "open_flags", flags=flags))
                continue

        key = normalise_key(str(values.get("BG No")))
        if key in seen:
            not_written.append(_not_written(source, "duplicate"))
            continue
        seen.add(key)

        row = {}
        try:
            for column in HEADERS:
                row[column] = _coerce(column, values.get(column))
        except (ValueError, TypeError) as error:
            message = f"bad value: {error}"
            problems[source] = {"missing": [], "flags": {}, "error": message}
            not_written.append(_not_written(source, "bad_value", error=message))
            continue

        if row.get("BG Amount") is not None and row["BG Amount"] <= 0:
            message = "bad value: BG Amount must be greater than zero"
            problems[source] = {"missing": [], "flags": {}, "error": message}
            not_written.append(_not_written(source, "bad_value", error=message))
            continue

        # Status is derived at OCR time; refresh it from the confirmed expiry
        # date unless the reviewer set it themselves.
        status_edited = not _same_value(
            "Status", entry["extracted_values"].get("Status"), values.get("Status")
        )
        if not status_edited:
            to_date = row.get("To")
            status = "" if to_date is None else ("Expired" if to_date < today else "Active")
            if status != (row.get("Status") or ""):
                status_recomputed.append(source)
            row["Status"] = status or None

        row["source_pdf"] = source
        row["created_by"] = user.username
        row["created_at"] = dt.datetime.now()
        row["reminder_sent_at"] = None
        row["_plaza_rows"] = _plaza_rows(row, entry, update.get("values") or {})
        origins[source] = _web_origins(entry, values)
        prepared.append(row)

    if problems and not include_flagged:
        raise ConfirmError(
            "Some rows need attention before they can be confirmed.",
            {"rows": problems},
        )
    if not prepared and not include_flagged:
        raise ConfirmError("No rows passed validation.", {"rows": problems})

    open_flags_by_file = {
        source: info["flags"] for source, info in problems.items() if info.get("flags")
    }
    workbook_path = config.WORKBOOK_PATH
    written: list[str] = []
    skipped: list[str] = []
    closed: list[dict] = []
    stored: list[dict] = []
    now = dt.datetime.now()
    audit_file = None

    with store.connect() as conn:
        # Database first: a constraint failure must stop the workbook write.
        for row in prepared:
            source = str(row.get("source_pdf"))
            existing = register.find_existing(conn, row.get("BG No"))
            if existing:
                not_written.append(
                    _not_written(
                        source,
                        "duplicate",
                        existing={
                            "id": existing["id"],
                            "BG No": existing["BG No"],
                            "Status": existing["Status"],
                            "created_at": existing["created_at"],
                        },
                    )
                )
                continue
            outcome, detail = register.store_row(
                conn, row, user.id, origins.get(source)
            )
            if outcome != "written":
                not_written.append(
                    _not_written(source, "duplicate", existing=detail)
                )
                continue
            bg_id = str(detail["id"])
            row["bg_id"] = bg_id
            stored.append(
                {
                    "source_pdf": source,
                    "bg_id": bg_id,
                    "BG No": detail["bg_no"],
                    "loa_id": detail.get("loa_id"),
                }
            )
            store.audit(
                conn,
                "confirm",
                user_id=user.id,
                bg_no=detail["bg_no"],
                old={"run_id": run_id},
                new={column: _json_value(row.get(column)) for column in HEADERS},
            )
            if detail.get("loa_id"):
                for old in register.apply_extension(conn, detail["loa_id"], bg_id):
                    closed.append(old)

        if stored:
            with _file_lock(_workbook_lock_path()):
                excel.prepare_workbook(config.TRACKER_PATH, workbook_path)
                wb, ws = excel.open_tracker(workbook_path)
                known = excel.existing_row_keys(ws)

                for row in prepared:
                    source = str(row.get("source_pdf"))
                    if not row.get("bg_id"):
                        continue  # duplicate, already reported
                    appended = False
                    target = None
                    for book_row in row.get("_plaza_rows") or [row]:
                        key = excel.row_key(book_row)
                        if key in known:
                            continue
                        target = excel.append_row(ws, book_row)
                        known.add(key)
                        appended = True
                        book_row["_workbook_row"] = target
                    if not appended:
                        not_written.append(_not_written(source, "duplicate"))
                        continue
                    if source not in written:
                        written.append(source)
                    row["_workbook_row"] = target

                # A new BG under the same LOA closes the earlier guarantee in Excel
                # too - never deletes it.
                for old in closed:
                    changed_status = excel.update_row_by_bg_no(
                        ws, old["BG No"], {"Status": "Closed", "Extended": "Yes"}
                    )
                    if changed_status:
                        old["workbook_row"] = changed_status

                excel.finalise(ws)
                excel.save(wb, workbook_path)

        skipped = [item["source_pdf"] for item in not_written if item["reason"] == "duplicate"]
        audit = {
            "run_id": run_id,
            "reviewer": user.username,
            "reviewer_id": user.id,
            "confirmed_at": now.isoformat(timespec="seconds"),
            "workbook": _display_path(workbook_path),
            "written": written,
            "not_written": not_written,
            "skipped_duplicate": skipped,
            "include_flagged": include_flagged,
            "open_flags": open_flags_by_file,
            "edits": edits,
            "status_recomputed": status_recomputed,
            "closed_by_extension": closed,
        }
        audit_path = run_dir(run_id) / f"confirm-{now:%Y%m%d-%H%M%S}.json"
        audit_path.write_text(
            json.dumps(audit, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        audit_file = audit_path.name
        config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        with (config.RUNS_DIR / "confirmations.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit, ensure_ascii=False, default=str) + "\n")

    with _file_lock(_run_lock_path(run_id)):
        manifest = load_manifest(run_id)
        if written:
            manifest["status"] = "confirmed"
            manifest["confirmed_at"] = now.isoformat(timespec="seconds")
            manifest["confirmed_by"] = user.username
        save_manifest(run_id, manifest)

    return {
        "written": written,
        "not_written": not_written,
        "skipped_duplicate": skipped,
        "closed_by_extension": closed,
        "workbook": workbook_path.name,
        "path": str(workbook_path.resolve()),
        "audit": audit_file,
        "open_flags": open_flags_by_file,
        "stored": stored,
    }


def reset_workbook() -> None:
    """Start the review workbook again from the tracker template."""
    with _file_lock(_workbook_lock_path()):
        if config.WORKBOOK_PATH.exists():
            shutil.copy2(config.TRACKER_PATH, config.WORKBOOK_PATH)
        workbook, _ = excel.open_tracker(config.WORKBOOK_PATH)
        excel.save(workbook, config.WORKBOOK_PATH)


def update_workbook_row(bg_no, changes: dict) -> int | None:
    """Apply an admin correction to the review workbook, if it has the row."""
    if not config.WORKBOOK_PATH.exists():
        return None
    with _file_lock(_workbook_lock_path()):
        wb, ws = excel.open_tracker(config.WORKBOOK_PATH)
        target = excel.update_row_by_bg_no(ws, bg_no, changes)
        if target is None:
            wb.close()
            return None
        excel.finalise(ws)
        excel.save(wb, config.WORKBOOK_PATH)
        return target
