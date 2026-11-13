#
# BG ingestion app in one image: FastAPI review UI with Datalab OCR.
# The register and review data stay local; each uploaded PDF is sent to the
# Datalab API for OCR. The optional Firecrawl official-name lookup sends just
# two short strings per PDF and is off when BG_FIRECRAWL_API_KEY is empty.
#
#   docker compose build
#   docker compose up -d          -> http://127.0.0.1:8000
#
# The layout mirrors the repo (/app/backend, /app/db, /app/docs, /app/BG Tracker.xlsx)
# because bgauto/config.py resolves its default paths from the package location.
ARG BG_BASE_IMAGE=python:3.13-slim
FROM ${BG_BASE_IMAGE}

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    BG_WEB_HOST=0.0.0.0 \
    BG_WEB_PORT=8000

WORKDIR /app

# Dependencies first: this layer is reused until requirements.txt changes.
# postgresql-client rides along in the same layer for `pg_dump`, which the
# backup archive uses; the apt lists are dropped again to keep it lean.
COPY backend/requirements.txt backend/requirements.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY ["BG Tracker.xlsx", "BG Tracker.xlsx"]
COPY db/ db/
# Master data of record: the one-shot migrate service seeds the dropdowns from
# it, and `python -m bgauto.cli import-masters` re-reads it on updates.
COPY docs/ docs/

# Non-root runtime user. The app only ever writes under out/ (review workbooks,
# runs, OCR archive under data/); the database lives in Postgres.
RUN if ! id -u bgauto >/dev/null 2>&1; then useradd --create-home --uid 10001 bgauto; fi \
 && mkdir -p out data \
 && chown -R bgauto:bgauto /app/out /app/data /app/db
USER bgauto

EXPOSE 8000

# Readiness: the API serves and the database answers. `degraded` means the OCR
# key is absent, which is a configuration state, not a dead process.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import json,sys,urllib.request; h=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8)); db=(h.get('database') or {}).get('version'); sys.exit(0 if h.get('status') in ('ok','degraded') and db else 1)"

CMD ["python", "-m", "bgauto.web"]
