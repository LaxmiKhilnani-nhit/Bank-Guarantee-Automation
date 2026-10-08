# BG Automation — Database

Default engine: **SQLite** (`db/sqlite/bg_automation.sqlite3`, stdlib only, no Docker).
Postgres files under `db/migrations/` + `db/docker-compose.yml` are retained for the
later VM/server move; both schemas implement the same approved design
(`DATE+flag` extended, `1 LOA:M BGs`, 6-state status, normalised masters).

## SQLite quickstart (default)

```powershell
python db/sqlite/apply.py                 # create + migrate + verify
python db/sqlite/apply.py --verify-only
python db/sqlite/apply.py --db path/to/custom.sqlite3
```

## Postgres (retained for server move)

```
db/
  docker-compose.yml      local Postgres 16 (volume bg_pgdata)

Versioned migrations + seeds for the approved normalised design
(`BG_Automation_Plan.md` §3 + DB design sign-off: `DATE+flag` extended,
`1 LOA:M BGs`, 6-state status, normalised masters).

## Layout

```
db/
  docker-compose.yml      local Postgres 16 (volume bg_pgdata)
  requirements.txt        psycopg[binary] for apply.py
  apply.py                creates DB, applies migrations in order, verifies
  migrations/
    V1__schema.sql        tables + PKs + FKs + CHECKs + indexes + view
    V2__seed.sql          company/project/toll/bank/contractor/type/nature/status/roles
```

## Quickstart (Windows PowerShell)

```powershell
pip install -r db/requirements.txt
docker compose -f db/docker-compose.yml up -d
python db/apply.py            # create DB + migrate + verify
python db/apply.py --verify-only
```

## Config (env-driven, VM-ready)

| Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | — | Full URL; wins over split vars |
| `PGHOST` / `PGPORT` | `localhost` / `5432` | Host/port |
| `PGDATABASE` | `bg_automation` | Database (created if missing) |
| `PGUSER` | `bg_app` | Must match compose user |
| `PGPASSWORD` / `POSTGRES_PASSWORD` | `bg_app_local_pw` | Local pw; **change in prod / VM** |

## Notes

- `bg_records.bg_no` is `UNIQUE` (UC003 dedupe key). Excel `Sr` is not stored.
- `Extended`: Excel dates → `extended_date`; legacy `Yes` → `status='Extended'`, date NULL.
- Excel `Status` `#REF!`/blank → `Draft` on import; lifecycle states live in `bg_status`.
- `ocr_results.bg_id` nullable so failed extractions (ST008) exist without a BG row.
- `reminders(bg_id, scheduled_for)` unique prevents double T-7 sends.
- Import of the 85 Excel rows is a separate step (maps names→master IDs); ask the build agent.
