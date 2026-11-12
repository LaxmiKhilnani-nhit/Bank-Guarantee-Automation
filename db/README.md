# BG Automation — Database

Engine: **PostgreSQL 15+**. Migrations live in `db/migrations/` and are applied
by `db/apply.py`; `db/bootstrap.py` additionally seeds the canonical masters from
`docs/master-data.md` and verifies the result.

SQLite was retired on 2026-11-12 (cutover) and its rollback window closed on
2026-11-13. The application, the tests and the deployment only use PostgreSQL;
`db/sqlite/` no longer exists.

## Layout

```
db/
  docker-compose.yml      development/CI Postgres 16 (volume bg_pgdata)
  requirements.txt        psycopg[binary] for apply.py / bootstrap.py
  apply.py                create database, apply V*.sql in order, verify
  bootstrap.py            apply + seed masters from docs/master-data.md + verify
  import_sqlite.py        one-shot SQLite -> PostgreSQL data import
  migrations/
    V1__schema.sql        tables + PKs + FKs + CHECKs + indexes + view
    V2__seed.sql          canonical masters + lookups + roles
    V3__schema.sql        parity gaps (state/round/PM/chainage, lockout, TEXT[])
```

## Local quickstart

```powershell
pip install -r db/requirements.txt
docker compose -f db/docker-compose.yml up -d          # Postgres on :5432
$env:DATABASE_URL = "postgresql://bg_app:bg_app_local_pw@127.0.0.1:5432/bg_automation"
python db/bootstrap.py                                 # V1-V3 + masters + verify
python db/bootstrap.py --verify-only                   # verify only
```

`POSTGRES_PORT` moves the published port (`5432` is often taken):

```powershell
$env:POSTGRES_PORT = "5433"
docker compose -f db/docker-compose.yml up -d
$env:DATABASE_URL = "postgresql://bg_app:bg_app_local_pw@127.0.0.1:5433/bg_automation"
```

`db/docker-compose.yml` is **development/CI only**. The deployment stack is the
root `docker-compose.yml` (`db` + one-shot `migrate` + `app`); see
`backend/README.md`.

## Migrations

`python db/apply.py` applies every `db/migrations/V*.sql` in filename order, one
transaction per file. The scripts are idempotent (`IF NOT EXISTS` /
`ON CONFLICT`), so re-running is safe. It then verifies:

- every expected table exists;
- the canonical master minimums are met (3 companies, 28 projects, 46 plazas,
  27 natures, 3 roles, 2 BG types, 6 statuses);
- no foreign-key violations;
- the `updated_at` trigger, key indexes and the `v_bg_register` view exist.

It prints a summary and exits non-zero on failure. `--verify-only` skips the
migrations.

`db/bootstrap.py` is the normal entry point: it calls `apply.py`, imports the
masters from `docs/master-data.md` through the same importer the CLI uses, then
runs the verification. Do not seed masters by hand — the document is the single
source of truth.

## Legacy SQLite import (completed)

`db/import_sqlite.py` is the one-shot tool that copied the live rows during the
cutover. The import is done and the SQLite file is gone; the script is kept as
the record of exactly how the data was mapped (and for any future one-off file,
always via `--source`). It is a dry run by default:

```powershell
$env:DATABASE_URL = "postgresql://bg_app:bg_app_local_pw@127.0.0.1:5432/bg_automation"
python db/import_sqlite.py --source path\to\legacy.sqlite3          # plan only
python db/import_sqlite.py --source path\to\legacy.sqlite3 --apply  # one transaction
```

It reads the SQLite file with `mode=ro` and fails if its size or mtime changes.

Inside the deployment container the file is not baked into the image, so mount it
read-only and pass `--source`:

```powershell
docker compose run --rm --no-deps -v "${PWD}\legacy:/legacy:ro" app `
  python db/import_sqlite.py --source /legacy/bg_automation.sqlite3
```

What it copies: `users`, `user_roles`, `loa_records`, `bg_records`,
`audit_logs`. What it leaves behind: legacy masters, sessions, reminders, lookup
cache and `ocr_results`. A referenced bank or contractor missing from the target
is created with `source = 'migrated'`.

The live file resolves to canonical rows as follows (the legacy spellings are
never re-created):

| Legacy value | Canonical target |
|---|---|
| `NSPPL` | `NHIT Southern Projects Private Limited` |
| `SB` | project `SB` (`Simga-Bilaspur`) |
| `Bhojpuri & Mudipar` | `Bhojpuri` (project `SB`) |
| `Manpower` | `nature_of_service` `Manpower` |
| `ARYA OMNITALK WIRELESS SOLUTIONS PVT LTD` | contractor created with `source='migrated'` |
| `ICICI Bank Limited` | bank created with `source='migrated'` |
| `NSPPL/TMS/2025/01` | `loa_records.loa_po_number` |

The import is idempotent: a second `--apply` inserts nothing. A source database
smaller than the live file is not a failure — the plan prints a note, every
source row is still required to land. Duplicate audit rows are preserved
(counted, not deduplicated), and UUID/text spellings are normalised before the
idempotency comparison.

## Config (env-driven, VM-ready)

| Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | — | Full URL; wins over the split vars |
| `PGHOST` / `PGPORT` | `localhost` / `5432` | Host/port |
| `PGDATABASE` | `bg_automation` | Database (created if missing) |
| `PGUSER` | `bg_app` | Must match the compose user |
| `PGPASSWORD` / `POSTGRES_PASSWORD` | `bg_app_local_pw` | Local password; **change in production** |
| `BG_DB_POOL_MIN` / `BG_DB_POOL_MAX` | `1` / `8` | App connection-pool sizing |
| `BG_DB_POOL_TIMEOUT` | `10` | Seconds to wait for a pooled connection |

The application pins every pooled session to UTC; "today" is computed in IST.

## Backup and restore

`python -m bgauto.cli backup` (from `backend/`) runs `pg_dump --format=custom`
and zips the dump with the workbook, template and PDF archive under
`data/backups/`. The password is passed through `PGPASSWORD`, never on the
command line. `pg_dump`/`pg_restore` must exist on the host; the app image
installs `postgresql-client`.

Restore overwrites the register, so stop the app first:

```powershell
pg_restore --clean --if-exists --no-owner --dbname="$env:DATABASE_URL" data\backups\<dump>
```

Back up before an upgrade and rehearse the restore into a scratch database.

## Testing

Tests are PostgreSQL integration tests: each test creates a throwaway schema,
applies V1–V3, seeds the canonical masters and drops the schema afterwards.

```powershell
$env:PYTHONPATH = "backend;backend\tests"
$env:BG_TEST_DATABASE_URL = "postgresql://bg_app:bg_app_local_pw@127.0.0.1:5433/bg_automation"
.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```

## Schema notes

- `bg_records.bg_no` is `UNIQUE` (UC003 dedupe key). Excel `Sr` is not stored.
- `Extended`: Excel dates go to `extended_date`; a legacy `Yes` becomes
  `status='Extended'` with a NULL date.
- Excel `Status` `#REF!`/blank becomes `Draft` on import; lifecycle states live
  in `bg_status`.
- `ocr_results.bg_id` is nullable so failed extractions (ST008) exist without a
  BG row.
- `reminders(bg_id, scheduled_for)` is unique, preventing double T-7 sends.
- `reminders.recipients` is `TEXT[]`; `audit_logs.old_data`/`new_data` are
  `JSONB` (NULL when unchanged).
- `v_bg_register` and `ocr_results` are kept for V1 compatibility but are not
  used by the application or the import.

## History

The application ran on SQLite until 2026-11-12. The cutover imported the live
rows into PostgreSQL, and the rollback window closed on 2026-11-13: the
`bg-automation:pre-postgres` image, the SQLite copy and the old compose file have
been removed. `plan/07-postgres-migration.md` records the run and the mapping.
PostgreSQL is now the only database of record.
