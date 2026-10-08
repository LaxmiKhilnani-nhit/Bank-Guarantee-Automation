"""Apply versioned SQL migrations to PostgreSQL and verify.

Usage:
    pip install -r db/requirements.txt
    docker compose -f db/docker-compose.yml up -d
    python db/apply.py [--verify-only]

Env (all optional, defaults = local docker-compose):
    DATABASE_URL              e.g. postgresql://bg_app:pw@localhost:5432/bg_automation
    PGHOST (localhost)  PGPORT (5432)  PGDATABASE (bg_automation)
    PGUSER (bg_app)     PGPASSWORD (bg_app_local_pw)

Behaviour:
  1. Creates PGDATABASE if missing (connects to maintenance db 'postgres').
  2. Applies db/migrations/V*.sql in filename order inside one
     transaction per file. Files are idempotent (IF NOT EXISTS / ON CONFLICT).
  3. Verifies: expected tables exist, seed counts, FK violations = 0,
     trigger + key indexes present. Prints summary; exits non-zero on failure.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import psycopg
    from psycopg import sql
except ImportError:
    print("ERROR: psycopg not installed. Run: pip install -r db/requirements.txt",
          file=sys.stderr)
    sys.exit(2)

BASE = Path(__file__).resolve().parent
MIGRATIONS = sorted((BASE / "migrations").glob("V*.sql"))

EXPECTED_TABLES = [
    "company", "project", "toll_plaza", "bank", "contractor", "bg_type",
    "nature_of_service", "bg_status", "roles", "users", "user_roles",
    "loa_records", "bg_records", "ocr_results", "reminders", "audit_logs",
]

# (table, min_rows) — masters must be seeded; transactional tables may be empty
SEED_MINIMUMS = {
    "bg_type": 2, "bg_status": 6, "roles": 3, "bank": 7,
    "project": 12, "contractor": 31, "nature_of_service": 25,
    "company": 1,
}


def conn_params(dbname: str | None = None) -> dict:
    if os.getenv("DATABASE_URL"):
        return {"conninfo": os.environ["DATABASE_URL"]}
    return {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": dbname or os.getenv("PGDATABASE", "bg_automation"),
        "user": os.getenv("PGUSER", "bg_app"),
        "password": os.getenv("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", "bg_app_local_pw")),
        "connect_timeout": 10,
    }


def ensure_database() -> None:
    target = os.getenv("PGDATABASE", "bg_automation") \
        if not os.getenv("DATABASE_URL") else None
    if target is None:  # DATABASE_URL mode: assume DB exists / managed
        return
    try:
        with psycopg.connect(**conn_params("postgres"), autocommit=True) as c:
            exists = c.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
            if not exists:
                c.execute(sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(target)))
                print(f"created database {target}")
            else:
                print(f"database {target} exists")
    except psycopg.OperationalError as e:
        print(f"ERROR: cannot reach Postgres: {e}", file=sys.stderr)
        print("Hint: docker compose -f db/docker-compose.yml up -d", file=sys.stderr)
        sys.exit(1)


def apply_all(verify_only: bool = False) -> int:
    failures: list[str] = []
    if not verify_only:
        if not MIGRATIONS:
            print("ERROR: no migration files found in db/migrations", file=sys.stderr)
            return 1
        with psycopg.connect(**conn_params()) as conn:
            for f in MIGRATIONS:
                text = f.read_text(encoding="utf-8")
                with conn.transaction():
                    conn.execute(text)
                print(f"applied {f.name}")

    with psycopg.connect(**conn_params()) as conn:
        # 1. tables present
        have = {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
        for t in EXPECTED_TABLES:
            ok = t in have
            print(f"  [{'OK' if ok else 'MISSING'}] table {t}")
            if not ok:
                failures.append(f"missing table {t}")

        # 2. seed minimums
        for t, minimum in SEED_MINIMUMS.items():
            if t not in have:
                continue
            n = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
            ok = n >= minimum
            print(f"  [{'OK' if ok else 'FAIL'}] {t}: {n} rows (min {minimum})")
            if not ok:
                failures.append(f"seed shortfall {t}: {n} < {minimum}")

        # 3. FK violations = 0 (all FKs referencing existing rows)
        bad = conn.execute("""
            SELECT conrelid::regclass AS tbl, conname
            FROM pg_constraint c
            WHERE contype = 'f'
              AND NOT EXISTS (
                SELECT 1 FROM pg_constraint v
                WHERE v.conname = 'fk_violation_check') -- placeholder, always true path below
        """).fetchall()  # informational only
        viol = conn.execute("""
            SELECT 'bg_records_project' AS chk,
                   count(*) FROM bg_records b
                   LEFT JOIN project p ON p.id = b.project_id
                   WHERE b.project_id IS NOT NULL AND p.id IS NULL
            UNION ALL SELECT 'bg_records_bank', count(*) FROM bg_records b
                   LEFT JOIN bank k ON k.id = b.bank_id
                   WHERE b.bank_id IS NOT NULL AND k.id IS NULL
            UNION ALL SELECT 'bg_records_loa', count(*) FROM bg_records b
                   LEFT JOIN loa_records l ON l.id = b.loa_id
                   WHERE b.loa_id IS NOT NULL AND l.id IS NULL
            UNION ALL SELECT 'reminders_bg', count(*) FROM reminders r
                   LEFT JOIN bg_records b ON b.id = r.bg_id
                   WHERE b.id IS NULL
        """).fetchall()
        for name, n in viol:
            ok = n == 0
            print(f"  [{'OK' if ok else 'FAIL'}] fk {name}: {n} violations")
            if not ok:
                failures.append(f"fk violations {name}: {n}")

        # 4. trigger + hot-path indexes
        trg = conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname='trg_bg_records_updated_at'"
            " AND NOT tgisinternal").fetchone()[0]
        print(f"  [{'OK' if trg else 'FAIL'}] trigger trg_bg_records_updated_at")
        if not trg:
            failures.append("missing trigger trg_bg_records_updated_at")
        for idx in ("idx_bg_to_date", "idx_rem_sched", "idx_audit_ts"):
            n = conn.execute(
                "SELECT count(*) FROM pg_indexes WHERE indexname=%s", (idx,)).fetchone()[0]
            print(f"  [{'OK' if n else 'FAIL'}] index {idx}")
            if not n:
                failures.append(f"missing index {idx}")

        # 5. view
        v = conn.execute(
            "SELECT count(*) FROM pg_views WHERE viewname='v_bg_register'").fetchone()[0]
        print(f"  [{'OK' if v else 'FAIL'}] view v_bg_register")
        if not v:
            failures.append("missing view v_bg_register")

    print("VERIFY PASSED" if not failures else f"VERIFY FAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    ensure_database()
    sys.exit(apply_all(verify_only="--verify-only" in sys.argv))
