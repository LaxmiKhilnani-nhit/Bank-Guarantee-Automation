#
# Local BG ingestion app in one image: FastAPI review UI + Indra and Tesseract OCR.
# OCR and the register stay local. The only outbound call is the optional
# Firecrawl official-name lookup (two short strings per PDF); it is off when
# BG_FIRECRAWL_API_KEY is empty. Indra runs locally; its model cache is on data/.
#
#   docker compose build
#   docker compose up -d          -> http://127.0.0.1:8000
#
# The layout mirrors the repo (/app/backend, /app/db/sqlite, /app/BG Tracker.xlsx)
# because bgauto/config.py resolves its default paths from the package location.
ARG BG_BASE_IMAGE=python:3.13-slim
FROM ${BG_BASE_IMAGE}

ARG BG_INSTALL_OS_DEPS=1
USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    BG_WEB_HOST=0.0.0.0 \
    BG_WEB_PORT=8000

# Both OCR engines run inside the image. Indra weights download on first use;
# Tesseract remains available as a fallback and comparison engine.
# The language data is a separate Debian package; the apt lists are dropped so
# the layer stays small.
RUN if [ "${BG_INSTALL_OS_DEPS}" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
      && rm -rf /var/lib/apt/lists/*; \
    fi

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

# The health endpoint runs a real OCR round-trip, so allow it a few seconds.
HEALTHCHECK --interval=30s --timeout=20s --start-period=20s --retries=3 \
  CMD python -c "import json,sys,urllib.request; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=15)).get('status') == 'ok' else 1)"

CMD ["python", "-m", "bgauto.web"]
