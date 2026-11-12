"""Create and seed the PostgreSQL database in one step.

Usage::

    python db/bootstrap.py            # apply V1-V3, seed masters, verify
    python db/bootstrap.py --verify-only

This is what the one-shot ``migrate`` service runs in ``docker-compose.yml``.
Masters come from ``docs/master-data.md`` - the canonical source - through the
same importer the CLI uses, so the seed is never duplicated.

Connection settings match ``db/apply.py``: ``DATABASE_URL`` wins, otherwise the
``PG*`` environment variables (defaults: local docker-compose).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "db"))
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _url_from_pg_env() -> str:
    user = os.getenv("PGUSER", "bg_app")
    password = os.getenv("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", "bg_app_local_pw"))
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    name = os.getenv("PGDATABASE", "bg_automation")
    return f"postgresql://{quote(user)}:{quote(password)}@{host}:{port}/{name}"


# bgauto.config reads DATABASE_URL at import time, so synthesise it first.
if not os.environ.get("DATABASE_URL", "").strip():
    os.environ["DATABASE_URL"] = _url_from_pg_env()

import apply  # noqa: E402  (db/ is on sys.path)
from bgauto import store  # noqa: E402
from bgauto.masterdata import import_master_data  # noqa: E402


def _seed_masters(source: Path) -> int:
    if not source.is_file():
        print(f"ERROR: master data not found: {source}", file=sys.stderr)
        return 2
    report = import_master_data(source, dry_run=False)
    for group in ("companies", "projects", "plazas"):
        counts = {
            key: len(value)
            for key, value in report[group].items()
            if isinstance(value, list)
        }
        print(f"seeded {group:9}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for key in ("projects", "plazas"):
        gone = report[key]["deactivated"]
        if gone:
            print(f"deactivated {key}: {', '.join(gone)}")
    store.reset_pool()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="db/bootstrap.py",
        description="Apply migrations, seed masters from docs/master-data.md, verify",
    )
    parser.add_argument(
        "--master-data",
        default=str(REPO_ROOT / "docs" / "master-data.md"),
        help="canonical master data file (default docs/master-data.md)",
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="skip migrations and seeding"
    )
    args = parser.parse_args(argv)

    if not args.verify_only:
        apply.ensure_database()
        try:
            apply.apply_migrations()
        except Exception as error:  # psycopg errors carry the useful detail
            print(f"ERROR: migrations failed: {error}", file=sys.stderr)
            return 1
        try:
            failed = _seed_masters(Path(args.master_data))
        except Exception as error:
            print(f"ERROR: master seeding failed: {error}", file=sys.stderr)
            return 1
        if failed:
            return failed

    try:
        return apply.apply_all(verify_only=True)
    except Exception as error:
        print(f"ERROR: verification could not run: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
