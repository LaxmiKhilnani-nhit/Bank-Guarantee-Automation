"""Runtime configuration for the local BG ingestion pipeline.

Everything is environment-driven so the same code can later run on the VM
(where Tesseract will be installed system-wide) without edits.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# --- Tesseract discovery -------------------------------------------------

def _candidates() -> list[Path]:
    env = os.environ.get("TESSERACT_CMD")
    if env:
        return [Path(env)]
    local = os.environ.get("LOCALAPPDATA", "")
    out = [Path(local) / "bgauto" / "tesseract" / "Library" / "bin" / "tesseract.exe"]
    out.append(Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
    out.append(Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"))
    which = shutil.which("tesseract")
    if which:
        out.append(Path(which))
    return out


def find_tesseract() -> Path:
    for path in _candidates():
        if path.is_file():
            return path
    raise FileNotFoundError(
        "tesseract.exe not found. Set TESSERACT_CMD or install Tesseract 5."
    )


def find_tessdata(tesseract_cmd: Path) -> Path | None:
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    # conda-forge layout: <prefix>/Library/bin/tesseract.exe + <prefix>/share/tessdata
    candidate = tesseract_cmd.parents[2] / "share" / "tessdata"
    if candidate.is_dir():
        return candidate
    # installer layout: <dir>/tesseract.exe + <dir>/tessdata
    candidate = tesseract_cmd.parent / "tessdata"
    if candidate.is_dir():
        return candidate
    return None


# --- Paths ---------------------------------------------------------------

DATA_DIR = Path(os.environ.get("BG_DATA_DIR", REPO_ROOT / "data"))
OCR_ARCHIVE_DIR = DATA_DIR / "ocr-json"
PDF_ARCHIVE_DIR = DATA_DIR / "pdfs"
TRACKER_PATH = Path(os.environ.get("BG_TRACKER", REPO_ROOT / "BG Tracker.xlsx"))
DB_PATH = Path(os.environ.get("BG_DB", REPO_ROOT / "db" / "sqlite" / "bg_automation.sqlite3"))

# --- Outputs -------------------------------------------------------------

OUT_DIR = Path(os.environ.get("BG_OUT_DIR", REPO_ROOT / "out"))
# The app writes a review copy of the tracker; the source template is read-only.
WORKBOOK_PATH = Path(os.environ.get("BG_WORKBOOK", OUT_DIR / "BG Tracker_ingested.xlsx"))
RUNS_DIR = OUT_DIR / "web"
MAIL_DIR = OUT_DIR / "mail"
LOG_DIR = OUT_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_KEEP = int(os.environ.get("BG_BACKUP_KEEP", "7"))

# --- Identity ------------------------------------------------------------
# Server-side sessions: no JWT, no token in browser storage.
SESSION_MINUTES = int(os.environ.get("BG_SESSION_MINUTES", "30"))
# Loopback runs over plain HTTP, so the cookie must not demand TLS.
COOKIE_SECURE = os.environ.get("BG_COOKIE_SECURE", "0").strip().lower() not in {
    "0", "false", "no", "off", "",
}
BOOTSTRAP_ADMIN_USER = os.environ.get("BG_BOOTSTRAP_ADMIN_USER", "admin")
BOOTSTRAP_ADMIN_PASS = os.environ.get("BG_BOOTSTRAP_ADMIN_PASS", "")
BOOTSTRAP_ADMIN_EMAIL = os.environ.get("BG_BOOTSTRAP_ADMIN_EMAIL", "")

# --- Mail (T-7 reminders) ------------------------------------------------

def _list_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


SMTP_HOST = os.environ.get("BG_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("BG_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("BG_SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("BG_SMTP_PASS", "")
MAIL_FROM = os.environ.get("BG_MAIL_FROM", "bg-automation@localhost")
MAIL_FROM_NAME = os.environ.get("BG_MAIL_FROM_NAME", "BG Automation")
FINANCE_DL = _list_env("BG_FINANCE_DL")
USER_TEAM_DL = _list_env("BG_USER_TEAM_DL")
# No SMTP host configured means "never send": write the message to out/mail.
MAIL_DRY_RUN = os.environ.get("BG_MAIL_DRY_RUN", "1" if not SMTP_HOST else "0").strip().lower() in {
    "1", "true", "yes", "on",
}
REMINDER_DAYS = int(os.environ.get("BG_REMINDER_DAYS", "7"))
APP_URL = os.environ.get("BG_APP_URL", "http://127.0.0.1:8000").rstrip("/")

# --- Scheduler -----------------------------------------------------------
SCHEDULER_ENABLED = os.environ.get("BG_SCHEDULER", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
# IST never observes DST, so a fixed offset avoids depending on tzdata.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")
SCHEDULER_TIME = os.environ.get("BG_SCHEDULER_TIME", "09:00")
INTERNAL_TOKEN = os.environ.get("BG_INTERNAL_TOKEN", "")

# --- OCR settings --------------------------------------------------------

# indra | tesseract | azure | both | all. Azure sends document bytes to the
# configured Document Intelligence endpoint; local engines stay on this machine.
OCR_ENGINE = os.environ.get("BG_OCR_ENGINE", "indra").strip().lower()
OCR_ENGINES = ("indra", "tesseract", "azure", "both", "all")
OCR_DPI = int(os.environ.get("BG_OCR_DPI", "300"))
OCR_PSM = int(os.environ.get("BG_OCR_PSM", "3"))
MIN_CONFIDENCE = float(os.environ.get("BG_MIN_CONFIDENCE", "0.75"))
INDRA_THREADS = int(os.environ.get("BG_INDRA_THREADS", str(max(1, (os.cpu_count() or 4) - 1))))
INDRA_MODE = os.environ.get("BG_INDRA_MODE", "auto").strip().lower()
AZURE_DI_ENDPOINT = os.environ.get("AZURE_DI_ENDPOINT", "").strip().rstrip("/")
AZURE_DI_KEY = os.environ.get("AZURE_DI_KEY", "").strip()
AZURE_DI_KEY_2 = os.environ.get("AZURE_DI_KEY_2", "").strip()
AZURE_DI_MODEL = os.environ.get("AZURE_DI_MODEL", "prebuilt-layout").strip()
AZURE_DI_PAGE_BATCH_SIZE = int(os.environ.get("AZURE_DI_PAGE_BATCH_SIZE", "2"))
AZURE_DI_MAX_BYTES = int(os.environ.get("AZURE_DI_MAX_BYTES", "3800000"))

# --- Official-name lookup (Firecrawl agent) ------------------------------
# Optional. Without a key the app stays fully local and keeps the OCR reads.
# Only the two OCR'd name strings and any IFSC codes leave the machine.
FIRECRAWL_API_KEY = os.environ.get("BG_FIRECRAWL_API_KEY", "").strip()
FIRECRAWL_URL = os.environ.get(
    "BG_FIRECRAWL_URL", "https://api.firecrawl.dev/v2/agent"
).rstrip("/")
LOOKUP_ENABLED = os.environ.get("BG_LOOKUP", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
LOOKUP_TIMEOUT = float(os.environ.get("BG_LOOKUP_TIMEOUT", "120"))
LOOKUP_POLL = float(os.environ.get("BG_LOOKUP_POLL", "2"))
LOOKUP_TTL_DAYS = int(os.environ.get("BG_LOOKUP_TTL_DAYS", "30"))
LOOKUP_MAX_MATCHES = int(os.environ.get("BG_LOOKUP_MAX_MATCHES", "5"))
LOOKUP_MAX_CREDITS = int(os.environ.get("BG_LOOKUP_MAX_CREDITS", "100"))
