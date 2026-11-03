"""``python -m bgauto.web`` serves the review UI on loopback."""
from __future__ import annotations

import logging
import logging.handlers
import os

import uvicorn

from .. import config
from .api import app


def _configure_logging() -> None:
    """Console plus a rotating file (``out/logs/app.log``)."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            config.LOG_DIR / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        root.addHandler(rotating)
    except OSError:
        pass  # read-only volume: console logging is still enough to start


def main() -> None:
    _configure_logging()
    host = os.environ.get("BG_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("BG_WEB_PORT", "8000"))
    print(f"BG ingestion review UI -> http://{host}:{port}")
    print("Local only: no Azure, no outbound calls. Ctrl+C to stop.")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
