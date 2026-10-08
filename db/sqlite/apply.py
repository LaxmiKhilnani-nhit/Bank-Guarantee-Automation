"""Create/migrate the SQLite database and verify (stdlib only, no Docker).

Usage:
    python db/sqlite/apply.py [--verify-only] [--db PATH]

Defaults to db/sqlite/bg_automation.sqlite3. Applies schema.sql then seed.sql
(both idempotent), then verifies: tables, seed minimums, foreign_key_check,
trigger + key indexes, and the v_bg_register view.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / "bg_automation.sqlite3"

EXPECTED_TABLES = [
    "company", "project", "toll_plaza", "bank", "contractor", "bg_type",
    "nature_of_service", "bg_status", "roles", "users", "user_roles",
    "loa_records", "bg_records", "ocr_results", "reminders", "audit_logs",
]

SEED_MINIMUMS = {
    "bg_type": 2, "bg_status": 6, "roles": 3, "bank": 7,
    "project": 12, "contractor": 31, "nature_of_service": 25,
    "company": 1, "toll_plaza": 32,
}


def apply(db_path: Path, verify_only: bool = False) -> int:
    failures: list[str] = []
    if not verify_only:
        for name in ("schema.sql", "seed.sql"):
            text = (BASE / name).read_text(encoding="utf-8")
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.executescript(text)
            print(f"applied {name} -> {db_path.name}")

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in EXPECTED_TABLES:
            ok = t in have
            print(f"  [{'OK' if ok else 'MISSING'}] table {t}")
            if not ok:
                failures.append(f"missing table {t}")

        for t, minimum in SEED_MINIMUMS.items():
            if t not in have:
                continue
            n = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            ok = n >= minimum
            print(f"  [{'OK' if ok else 'FAIL'}] {t}: {n} rows (min {minimum})")
            if not ok:
                failures.append(f"seed shortfall {t}: {n} < {minimum}")

        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        print(f"  [{'OK' if not bad else 'FAIL'}] foreign_key_check: "
              f"{len(bad)} violations")
        if bad:
            failures.append(f"fk violations: {bad[:5]}")

        trg = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='trigger'"
            " AND name='trg_bg_records_updated_at'").fetchone()[0]
        print(f"  [{'OK' if trg else 'FAIL'}] trigger trg_bg_records_updated_at")
        if not trg:
            failures.append("missing trigger trg_bg_records_updated_at")

        for idx in ("idx_bg_to_date", "idx_rem_sched", "idx_audit_ts"):
            n = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='index'"
                " AND name=?", (idx,)).fetchone()[0]
            print(f"  [{'OK' if n else 'FAIL'}] index {idx}")
            if not n:
                failures.append(f"missing index {idx}")

        v = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='view'"
            " AND name='v_bg_register'").fetchone()[0]
        print(f"  [{'OK' if v else 'FAIL'}] view v_bg_register")
        if not v:
            failures.append("missing view v_bg_register")

    print("VERIFY PASSED" if not failures else f"VERIFY FAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    sys.exit(apply(Path(args.db), verify_only=args.verify_only))
