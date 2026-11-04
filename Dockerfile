#
# BG ingestion app in one image: FastAPI review UI with Datalab OCR.
# The register and review data stay local; each uploaded PDF is sent to the
# Datalab API for OCR. The optional Firecrawl official-name lookup sends just
# two short strings per PDF and is off when BG_FIRECRAWL_API_KEY is empty.
#
#   docker compose build
#   docker compose up -d          -> http://127.0.0.1:8000
#
# The layout mirrors the repo (/app/backend, /app/db/sqlite, /app/BG Tracker.xlsx)
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
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY ["BG Tracker.xlsx", "BG Tracker.xlsx"]
COPY db/sqlite/ db/sqlite/

# Non-root runtime user. The app only ever writes under out/ (review workbooks,
# runs, OCR archive under data/) and, when the schema is behind, the database.
RUN if ! id -u bgauto >/dev/null 2>&1; then useradd --create-home --uid 10001 bgauto; fi \
 && mkdir -p out data \
 && chown -R bgauto:bgauto /app/out /app/data /app/db
USER bgauto

EXPOSE 8000

# Health is a configuration check (SDK installed + key present), not an API
# call, so this probe stays free.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import json,sys,urllib.request; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8)).get('status') == 'ok' else 1)"

CMD ["python", "-m", "bgauto.web"]
