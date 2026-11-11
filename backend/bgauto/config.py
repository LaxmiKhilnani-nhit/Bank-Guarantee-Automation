"""Runtime configuration for the BG ingestion pipeline.

Everything is environment-driven so the same code can run locally or in the
container without edits.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Apply a local ``.env`` file without overriding real environment vars.

    Docker Compose reads the same file for ``${VARIABLES}``; this loader makes
    ``python -m bgauto.web`` and the CLI see it too. Values already present in
    the process environment always win. The file is git-ignored and is the
    documented home for ``DATALAB_API_KEY``.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")

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

# --- OCR settings (Datalab) ---------------------------------------------
# Datalab reads each uploaded PDF in the cloud: the convert step supplies page
# text, the extract step supplies the field JSON. The API key is read from
# DATALAB_API_KEY (preferred) or the SDK's saved `datalab login` config.
OCR_ENGINE = "datalab"
DATALAB_API_KEY = os.environ.get("DATALAB_API_KEY", "").strip()
DATALAB_BASE_URL = os.environ.get(
    "BG_DATALAB_BASE_URL", "https://www.datalab.to"
).rstrip("/")
# lite | fast | balanced | accurate. Only used by `ocr.convert_pdf`;
# pipeline runs take their parse mode from the pipeline's convert step.
DATALAB_MODE = os.environ.get("BG_DATALAB_MODE", "fast").strip().lower()
DATALAB_MODES = ("lite", "fast", "balanced", "accurate")
DATALAB_TIMEOUT = int(os.environ.get("BG_DATALAB_TIMEOUT", "600"))
DATALAB_MAX_POLLS = int(os.environ.get("BG_DATALAB_MAX_POLLS", "300"))
DATALAB_POLL_INTERVAL = float(os.environ.get("BG_DATALAB_POLL_INTERVAL", "2"))

# --- Pipeline (convert -> extract) ---------------------------------------
# The pipeline owns the convert/extract settings and the extraction schema.
# The production pipeline is deployed on a fresh account, so its first
# published version is 1. Pin that version so a manual edit in Datalab cannot
# silently change results; leave BG_DATALAB_PIPELINE_VERSION empty to always
# run the active published version.
DATALAB_PIPELINE_ID = os.environ.get(
    "BG_DATALAB_PIPELINE_ID", "pl_INVNggnY7MaE"
).strip()
_pipeline_version = os.environ.get("BG_DATALAB_PIPELINE_VERSION", "1").strip()
DATALAB_PIPELINE_VERSION = (
    int(_pipeline_version) if _pipeline_version.isdigit() else None
)
DATALAB_PIPELINE_MODES = ("turbo", "fast", "balanced", "accurate")
# Informational: the extract step's mode is stored in the Datalab pipeline, not
# sent per run. This value is only reported by ``/api/health`` so an operator can
# confirm which pipeline release is pinned.
DATALAB_EXTRACTION_MODE = (
    os.environ.get("BG_DATALAB_EXTRACTION_MODE", "turbo").strip().lower()
)

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
