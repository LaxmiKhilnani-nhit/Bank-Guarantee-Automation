---
id: PLAN-LOG
title: Decision and Change Log
status: active
last_updated: 2026-11-13
version: 15
inputs: ["BG_Automation_Plan.md", "BG Tracker.xlsx", "BG_UseCase_Story_Tracking.xlsx", "plan/"]
outputs: ["plan/00-log.md"]
---

# Decision and Change Log

## 1. Purpose

This file records every important decision and change in one place. Any person
or agent can read it top to bottom and understand how the project reached its
current state. Newest entries are first.

Format for each entry: date, author, what changed, why, files affected.

## 2. Log (newest first)

### 2026-11-13 — Team — Deployment topology: Docker Compose on the Windows VM

- What: production deployment is recorded as Docker Compose on the Windows VM.
  The stack is the three-service compose file already in the repo: `db`
  (PostgreSQL 16, `bg_pgdata` named volume), the one-shot `migrate`
  (`db/bootstrap.py`, applies V1–V3 and seeds the masters before the app
  starts) and `app` (built from `Dockerfile`, publishes
  `127.0.0.1:${BG_WEB_PORT:-8000}:8000`). Docker Desktop with the WSL 2
  backend (or Docker Engine plus Compose v2) runs it; the app publishes only
  on the loopback interface, so TLS stays with the environment's reverse proxy
  or load balancer in front of that port.
- Why: IIS, the Windows Service wrapper and the Task Scheduler job are dropped
  — none of them is what actually runs today, and compose is already the tested
  deployment (`docker compose up -d`, `docker compose logs -f app`,
  `docker compose run --rm migrate`). This entry records that decision rather
  than changing the stack.
- Scheduling: the 09:00 IST T-7 run is the in-process daemon thread in the app
  container (`ReminderScheduler`, catches up a late start, writes
  `data/scheduler-last-run.txt`); `POST /internal/daily-reminder` stays as the
  token-guarded manual trigger for re-runs, not as the default scheduler.
- Reminders: the run writes `reminders` rows (`UNIQUE (bg_id, scheduled_for)`)
  and sets `bg_records.reminder_sent_at`; no `tracker_mirror` or
  `reminder_log` table exists.
- Config: `.env` changes need `docker compose up -d app` (recreate), not
  `docker compose restart app`.
- Files: `plan/01-architecture.md`, `plan/05-flows.md`,
  `plan/06-email-scheduler-hosting.md`, `BG_Automation_Plan.md`.

### 2026-11-13 — Team — Rollback window closed; SQLite removed from the tree

- What: the rollback window opened at the 2026-11-12 cutover and closed after the
  smoke checks passed (browser flows, export, reminders, Audit, a real PDF
  upload through to review). PostgreSQL is now the only database of record.
- Deleted: `db/sqlite/` (`apply.py`, `schema.sql`, `seed.sql`,
  `bg_automation.sqlite3`), the `bg-automation:pre-postgres` image,
  `data/backups/bg_automation.pre-postgres.sqlite3` and
  `data/backups/docker-compose.pre-postgres.yml`. No rollback path to SQLite
  remains; recovery is `pg_restore` from
  `data/backups/bg_automation.post-cutover.dump`.
- Docs: `plan/07-postgres-migration.md` marked complete (v3, Phase 8 ticked),
  `plan/02-data-model.md` rewritten to the real Postgres model,
  `plan/01-architecture.md` and `BG_Automation_Plan.md` describe PostgreSQL,
  `db/README.md` replaces its rollback section with history, and the
  `docs/BG_*Diagram.mmd` / `docs/BG_Database_Diagrams.html` diagrams were
  regenerated with the Postgres types and tables.
- Code: `db/import_sqlite.py` now requires `--source` (its old default pointed
  at the deleted `db/sqlite/` file); SQLite prose was removed from the
  `auth.py`, `web/security.py`, `store.py` and register-test docstrings, and
  `app.db` was dropped from the data-folder lists in `plan/01` and `plan/06`.
- Config: `.dockerignore` no longer excludes `db/sqlite`; compose builds
  `DATABASE_URL` from `${POSTGRES_USER}`/`${POSTGRES_PASSWORD}`/`${POSTGRES_DB}`,
  and both healthchecks accept `ok`/`degraded` but require a database version.
- Health: `DATALAB_API_KEY` is set, so `/api/health` reports `ok` and OCR
  extraction works; `.env` changes need `docker compose up -d app`, not
  `restart`.
- Tests: 190 tests pass against the Postgres container on `127.0.0.1:5433`.
- Files: `db/README.md`, `db/import_sqlite.py`, `.dockerignore`,
  `plan/01-architecture.md`, `plan/02-data-model.md`,
  `plan/06-email-scheduler-hosting.md`, `plan/07-postgres-migration.md`,
  `docs/BG_ER_Diagram.mmd`, `docs/BG_Schema_Diagram.mmd`,
  `docs/BG_Database_Diagrams.html`, `backend/bgauto/{auth,store,web/security}.py`,
  `BG_Automation_Plan.md`; `db/sqlite/` removed.

### 2026-11-12 — Team — Cutover executed; the live stack runs PostgreSQL

- What: the deployment stack was migrated in place during a maintenance window.
  The SQLite app was stopped and its image kept as `bg-automation:pre-postgres`;
  the SQLite file was copied to
  `data/backups/bg_automation.pre-postgres.sqlite3` (SHA-256 verified) and the
  old compose file to `data/backups/docker-compose.pre-postgres.yml`.
- Database: a fresh `bg_pgdata` volume was created, `docker compose run --rm
  migrate` applied V1–V3 and seeded the canonical masters (3 companies, 28
  projects, 46 plazas, 27 natures), and `db/import_sqlite.py --apply` (file
  mounted read-only at `/legacy`) moved 3 users, 3 roles, 1 LOA, 1 BG and 46
  audit rows. `ICICI Bank Limited` and `ARYA OMNITALK WIRELESS SOLUTIONS PVT LTD`
  were created with `source='migrated'`.
- Verification: `db/apply.py --verify-only` passes on the live database; the
  live password hashes match the SQLite ones for `admin`, `uploader` and
  `viewer`; `bg_records` resolves to `NHIT Southern Projects Private Limited` /
  `SB` / `Bhojpuri` / `Manpower`; a post-cutover `pg_dump` (custom format,
  `--no-owner`) is in `data/backups/`; the full suite still passes (190 tests).
- Note: `/api/health` is `degraded` while `DATALAB_API_KEY` is empty (the UI
  shows "Datalab not configured"). The compose and image healthchecks require a
  database version and accept `degraded`, so the container reports `healthy`
  when the API serves and Postgres answers; set the key in `.env` for `ok` and
  working OCR. Compose now builds `DATABASE_URL` from the `POSTGRES_*` values, so
  changing the password cannot desync the app.
- Live check: an `admin` sign-in after the cutover added audit row 47 and a
  session, confirming authentication against the migrated users.
- Remaining: the rollback window, then Phase 8 (delete `db/sqlite/`, rewrite
  `plan/01` and `plan/02`, drop the `pre-postgres` image and backup).
- Files: `plan/07-postgres-migration.md` (§12.3 records the run),
  `data/backups/*` (not tracked).

### 2026-11-12 — Team — SQLite retired; application runs on PostgreSQL

- What: the backend now talks to PostgreSQL through `psycopg` + `psycopg_pool`
  (`dict_row`, UTC session via pool `configure`); `backend/bgauto/schema.py` and
  every `sqlite3` import are gone. A new `V3__schema.sql` closes the parity gap
  (state/round/project manager/chainage, login lockout, `TEXT[]` recipients)
  while V1/V2 stay untouched.
- Masters: `docs/master-data.md` is the single source (3 companies, 28 projects,
  46 plazas, 27 natures). `db/bootstrap.py` applies V1–V3, imports the document
  through the existing CLI importer, then verifies counts, FKs, trigger,
  indexes and the view.
- Data: `db/import_sqlite.py` moves only the referenced live rows (3 users,
  3 roles, 1 LOA, 1 BG, 46 audit rows) into the canonical masters. It is a dry
  run by default, `--apply` commits in one transaction, and it opens the SQLite
  file read-only. The live BG `0024NDLG00226526` maps `NSPPL` → `NHIT Southern
  Projects Private Limited`, plaza `Bhojpuri & Mudipar` → `Bhojpuri`, and
  creates the ICICI bank and ARYA contractor with `source='migrated'`.
- Correctness: audit rows are deduplicated by multiplicity, not set membership,
  so the three identical legacy logins all survive; UUID columns are compared in
  canonical form, so a second import inserts nothing. `PLANNED_COUNTS` is now
  informational (one target row per source row is the requirement), and
  `preflight()` pins the session to UTC so timestamps keep their SQLite reading.
- Deployment: root `docker-compose.yml` runs Postgres 16 (`bg_pgdata`), a
  one-shot `migrate` service (`db/bootstrap.py`) and the app; `db/docker-compose.yml`
  is development/CI only. `db/README.md` and `backend/README.md` are
  PostgreSQL-first.
- Verification: the full suite passes against a disposable Postgres schema
  (190 tests), the importer has its own regression module (dry run, apply,
  duplicate audit rows, master resolution, second-apply no-op), the imported
  data was smoke-tested through health/sign-in/register/audit, and a
  `pg_dump`/`pg_restore` rehearsal into a scratch database succeeded.
- Remaining: cutover on the deployment host and the Phase 8 cleanup (delete
  `db/sqlite/`, update `plan/01`/`plan/02`) after the rollback window. Rollback
  is the previous image plus the untouched SQLite file.
- Files: `db/{migrations/V3__schema.sql,apply.py,bootstrap.py,import_sqlite.py,README.md,docker-compose.yml,requirements.txt}`,
  `backend/bgauto/**`, `backend/tests/**`, `backend/{requirements.txt,Dockerfile,README.md}`,
  `docker-compose.yml`, `.env.example`, `.dockerignore`, `plan/07-postgres-migration.md`.

### 2026-11-10 — Team — Datalab convert → extract pipeline replaces text-rule extraction

- What: uploads and CLI ingestion now run the published Datalab pipeline
  `pl_INVNggnY7MaE` (development pipeline; production deploys the same schema on
  a fresh account as version 1): `convert` for page text, then structured
  `extract` in `turbo` mode. The extract JSON is authoritative; local code only
  normalises values and resolves company/project/plaza/nature/contractor/bank
  against the masters. There is no OCR-text rule fallback: a missing or invalid
  value becomes a blank cell with a flag.
- Schema: production schema **v1** (18 fields), maintained manually in the
  Datalab UI as pipeline `BG_Fields_Extraction_dev`; the fresh production
  account will publish it as version 1 (`v3` was only an internal working label
  on the development account). No schema file or sync script is executed by the
  app; the v1 field contract is mirrored for readers in
  `plan/04-ocr-extraction.md` §3.1. The exact schema and the full pipeline
  definition are preserved as reference snapshots in
  `docs/datalab-schema-v1.json` and `docs/datalab-pipeline-v1.json`; the original
  field wish-list and its coverage table live in
  `docs/Fields To Capture From BG Through D.md`. The pipeline version is pinned
  with `BG_DATALAB_PIPELINE_VERSION` (default `1`); the extraction mode is
  informational via `BG_DATALAB_EXTRACTION_MODE`.
- Extras: authorised signatories, stamp-duty certificates, amount in words,
  type wording and the printed project name are kept in
  `Extraction.extras`, the run archive and a read-only **Document extras**
  section in the review card. No DB or workbook columns were added.
- Results: both raw step payloads are archived immediately under
  `out/web/<run>/pipeline/`; Datalab expires them after about a day. The adapter
  sets `skip_cache=true` because a cached convert checkpoint has no downloadable
  step result.
- Confidence: turbo returns no citations or per-field scores. The UI shows a
  document-level `parse_quality_score` when present and otherwise no confidence
  (never 0%). Failed extract steps fail the upload; there is no blank-row
  fallback.
- Files: `backend/bgauto/{pipeline,extract,ocr,normalize,config,cli}.py`,
  `backend/bgauto/web/{api,runs,index.html}`, `.env.example`,
  `docker-compose.yml`, `backend/tests/{test_pipeline,test_extract,test_web,test_config}.py`,
  `backend/README.md`, `plan/04-ocr-extraction.md`, `docs/datalab-schema-v1.json`,
  `docs/datalab-pipeline-v1.json`, `docs/Fields To Capture From BG Through D.md`.

### 2026-11-04 — Team — Datalab replaces Tesseract, Indra and Azure OCR

- What: removed the Tesseract, Indra and Azure Document Intelligence adapters
  and their dependencies. OCR now goes through the Datalab API using
  `datalab-python-sdk` with a single fixed mode (`fast` by default,
  `BG_DATALAB_MODE`). The upload UI no longer has an engine selector; Datalab is
  the only engine. `bgauto/compare.py` and its tests are kept for reference but
  the upload flow no longer runs or displays an engine comparison.
- Confidence: Datalab's `parse_quality_score` (0–5), when provided, is mapped
  to `score / 5` with source `datalab_quality`. `fast` often omits it, so the UI
  and CLI simply show no confidence instead of 0%. Image extraction and captions
  are disabled.
- Health: `GET /api/health` is now a local configuration check (SDK installed
  plus key or saved login); it never spends an API call.
- Privacy: the PDF leaves the machine for Datalab. The register, workbook and
  archive stay local; the optional Firecrawl lookup still sends only the OCR'd
  names and IFSC codes.
- Config: `DATALAB_API_KEY`, `BG_DATALAB_MODE`, `BG_DATALAB_BASE_URL`,
  `BG_DATALAB_TIMEOUT`, `BG_DATALAB_MAX_POLLS`, `BG_DATALAB_POLL_INTERVAL`.
  The key lives only in the git-ignored `.env` (or the SDK's saved login).
  `config.py` now applies that `.env` for local runs as well as Compose; a real
  environment variable still wins.
- Files: `backend/bgauto/{ocr,config,cli}.py`,
  `backend/bgauto/web/{api,runs,index.html,__main__}.py`,
  `backend/tests/{test_ocr,test_web,test_config}.py`, `backend/requirements.txt`,
  `Dockerfile`, `docker-compose.yml`, `.env.example`, `backend/README.md`,
  `db/migrations/V1__schema.sql`, `plan/01-architecture.md`,
  `plan/04-ocr-extraction.md`, `plan/05-flows.md`.

### 2026-11-02 — Team — Optional Azure Document Intelligence OCR test engine

- What: added Azure DI as an opt-in engine beside local Indra and Tesseract,
  including a three-engine comparison choice, secondary-key auth retry, and
  F0-safe batches of up to two pages and 3.8 MB per request.
- Why: compare OCR quality on sample BGs while retaining local Indra as default.
- Credential handling: endpoint and two keys live in the ignored local `.env`;
  Azure only receives PDFs when Azure or All is selected.
- Files: OCR adapter, runtime config, upload selector, Docker Compose, `.env`
  example, OCR and architecture plans.

### 2026-11-02 — Team — Local Indra OCR and direct uploader save

- What: Indra is the default local OCR engine, with Tesseract fallback and
  optional comparison. LOA Number and PO Number are distinct fields. Uploaders
  can save their own reviewed rows to the register and workbook without admin
  approval; the workbook is viewable in the frontend.
- Why: support local document processing and a shorter review-to-register flow.
- Files: OCR adapter, extraction, schema, register, API, frontend and docs.

### 2026-11-02 — Team — Bank and contractor start empty; names come from OCR then Firecrawl

- What: removed the 7 prefilled banks and 31 prefilled contractors from
  `db/sqlite/seed.sql`. Neither list is a master: both tables are now registries
  that start empty and gain one row when a BG is confirmed (after the optional
  Firecrawl name check), with `source = 'web'` or `'manual'`.
- Extraction: `normalise_bank()` no longer requires the canonical name to exist
  in `masters.banks`; the keyword map still resolves HDFC/ICICI/PNB/etc. on a
  fresh database. The bank dropdown became a datalist with suggestions from
  confirmed BGs, matching the contractor field.
- Data: the 38 seeded rows were deleted from the live DB (none referenced by
  `bg_records`); `apply.py` no longer requires bank/contractor seed minimums.
- Why: the seeded lists were stale (typos, duplicate TBR spellings) and the
  intended flow is OCR once -> Firecrawl refine -> save on confirm.
- Files: `db/sqlite/seed.sql`, `db/sqlite/apply.py`, `backend/bgauto/masters.py`,
  `backend/bgauto/normalize.py`, `backend/bgauto/extract.py`,
  `backend/bgauto/web/index.html`, `backend/tests/test_extract.py`,
  `backend/tests/test_master_seed.py`, `backend/README.md`.

### 2026-11-02 — Team — Company Name now derived from the project (full SPV legal name)

- What: removed the last document-specific hardcode. `Company Name` is now the
  full legal SPV of the resolved project
  (`NHIT Southern Projects Private Limited` for `BS`, and the Eastern/Western
  SPVs for their corridors) instead of the fixed `NSPPL`. `NSPPL`, `NEPPL` and
  `NWPPL` are treated as aliases of Southern, Eastern and Western, and are only
  used when no project resolves. If the OCR beneficiary disagrees with the project, the project wins
  and the field carries `company_conflict: ocr=..., project=...`.
- Masters: the company master now has exactly the three SPVs. The legacy
  `NSPPL` row is deactivated (not deleted) by `db/sqlite/seed.sql`; the
  Postgres `V2__seed.sql` no longer inserts it; `apply.py` expects 3 companies.
- UI/Excel: the Company hint warns only when the selected value differs from
  the project's company; the workbook `Company Name` column is widened.
- Why: the review card showed `NSPPL` while its own hint said the project
  belonged to the full Southern SPV, so the stored company was wrong for every
  project outside Southern.
- Files: `backend/bgauto/extract.py`, `backend/bgauto/masters.py`,
  `backend/bgauto/web/index.html`, `backend/bgauto/register.py`,
  `db/sqlite/seed.sql`, `db/migrations/V2__seed.sql`, `db/sqlite/apply.py`,
  `backend/tests/test_extract.py`, `backend/tests/test_master_seed.py`,
  `backend/tests/test_lookup.py`, `backend/README.md`.

### 2026-11-02 — Team — Project/plaza master verified; bidirectional resolver

- What: audited the operations master document against the live masters. The
  canonical dataset is 28 corridors across three SPVs (Eastern 7/14 plazas,
  Southern 11/15, Western 10/17) with 46 plazas, each attached to exactly one
  corridor and the correct company. The live DB already had the right 28 active
  projects and 46 active plazas, but also carried 24 inactive alias/corridor
  rows and an inactive `RS,SB` data-entry project; those were purged because no
  `bg_records` referenced them. `db/sqlite/seed.sql` and
  `db/migrations/V2__seed.sql` were rewritten to the same canonical dataset
  (name, state, round, project manager, chainage, site managers) so a fresh
  database cannot regress. Combined spellings (`Bhojpuri & Mudipar`,
  `Chappar/Bahadrabad`), corridor rows (`Raipur - Simga`, `Simga to Bilaspur`)
  and OCR aliases (`Chappar`, `Gadanki`, `Veeravali`, `Nathavalsa`,
  `Mahasamundaram`, `Dukkavaniplem`) are no longer seeded; fuzzy matching still
  accepts them.
- Resolver: Project and Toll Plaza are resolved together in either direction.
  Every documented plaza is matched against every corridor first, so a wrong
  project guess cannot blank a plaza that is plainly written; an explicit plaza
  wins over a conflicting text project (`project_conflict` note). A project can
  also be derived from a later plaza. If the first documented plaza is not in
  the master the cell stays blank rather than substituting a later one. A bare
  `Plaza` in a bank branch address (`American Plaza`, `Fee-Plazas`) is ignored
  unless it matches a master name, so it no longer appears as an unmatched note.
### 2026-10-30 — Team — Extraction accuracy pass: corridor, plaza, LOA, contractor, nature

- What: corridor matching now uses fuzzy place-name tokens, so OCR slips and
  `-`/`to`/space separators still resolve (`Bareily to Sitapur` -> `BS`).
  Corridor rows seeded as toll plazas are ignored. When a BG names several
  plazas, the first one is shown and the rest are listed in `also_covers`;
  confirm writes one workbook row per plaza while the register stays one row
  per guarantee. A first plaza absent from the masters leaves the cell blank.
  Nature hints now prefer General Management Consultancy and Toll Management
  System over the generic `Operation`; `Routine Maintenance` and
  `Supply & Installation of Overhead Gantry` were added to the master and the
  fallback list. Contractor parsing rejects SFMS column headers
  (`and his details`, `and any request...`), accepts `APPLICANT >`, survives an
  address on the deed line, and falls back to a covering-letter letterhead.
  LOA parsing understands `Letter of Acceptance`, `PO Number`, `for the work`,
  `dtd`/`dated` endpoints, margin numbers, and uses `Tender No` as a marked
  last resort.
- Register: LOA auto-close is scoped by contractor + corridor + plaza + BG type
  and a later `From` date, so one LOA covering several corridors/plazas no
  longer closes unrelated guarantees.
- Why: the 15-document review showed wrong corridors, a wrong plaza from a
  combined name, garbage contractor values that poisoned the Firecrawl lookup,
  LOA values that swallowed dates or text, and a cross-corridor close risk.
- Files: `backend/bgauto/extract.py`, `backend/bgauto/excel.py`,
  `backend/bgauto/register.py`, `backend/bgauto/masters.py`,
  `backend/bgauto/web/runs.py`, `db/sqlite/seed.sql`,
  `db/sqlite/bg_automation.sqlite3`, `backend/tests/test_extract.py`,
  `backend/tests/test_register.py`, `backend/tests/test_web.py`,
  `backend/README.md`.

### 2026-10-30 — Team — Optional Firecrawl check for official bank/company names

- What: after OCR, each PDF runs two Firecrawl agent jobs in parallel — Bank
  Name (with the IFSC codes found in the OCR text, issuing bank first) and
  Name of Contractor. The upload waits for both, so the review card opens complete.
  The agent's top match is selected by default; the field is a dropdown holding
  the other matches, each with its CIN/IFSC where known, plus
  `as read: <OCR text>`. A hint shows the identifier and source host. Timeouts,
  HTTP errors and empty answers keep the OCR value and never block submit or
  confirm. Answers are cached by exact text + IFSC context in `lookup_cache`.
  Web-sourced names are created on confirm with `source = 'web'`.
- Scope: Bank Name + Name of Contractor only. Company Name keeps its OCR value
  and master dropdown, with no web check.
- Privacy: only the two OCR'd name strings and up to three IFSC codes leave the
  machine; never the PDF or any other field. This is the one outbound call.
- Why: OCR spacing/spelling errors should be corrected to official Indian names
  without adding buttons or changing the maker-checker flow.
- Config: `BG_FIRECRAWL_API_KEY` (empty = off), `BG_LOOKUP`,
  `BG_LOOKUP_TIMEOUT`, `BG_LOOKUP_MAX_CREDITS`, `BG_LOOKUP_TTL_DAYS`,
  `BG_LOOKUP_MAX_MATCHES`.
- Files: `backend/bgauto/lookup.py`, `backend/bgauto/config.py`,
  `backend/bgauto/schema.py`, `db/sqlite/schema.sql`,
  `backend/bgauto/web/runs.py`, `backend/bgauto/web/api.py`,
  `backend/bgauto/web/index.html`, `backend/bgauto/register.py`,
  `backend/tests/test_lookup.py`, `backend/README.md`, `.env.example`,
  `docker-compose.yml`, `Dockerfile`.

### 2026-10-30 — Team — Local sign-ins for now: username + password, viewer by default

- What: the Users screen now asks only for a username and a temporary password
  and always creates a viewer; the role is changed in the users table. Removed
  the Full name and Email fields (and their table columns) until the planned
  SSO link supplies them. Added a git-ignored `credentials.txt` and set up the
  three pilot accounts `admin` / `viewer` / `uploader`.
- Policy: the pilot password floor is 8 characters (was 10) so the role-based
  demo passwords work; raise it again when SSO replaces local sign-in.
- Why: the pilot only needs simple local logins; fewer fields, less to explain.
- Files: `backend/bgauto/web/index.html`, `backend/bgauto/auth.py`,
  `backend/tests/test_auth.py`, `backend/README.md`, `plan/03-auth.md`,
  `.gitignore`, `credentials.txt` (local only).

### 2026-10-30 — Team — Auth, register, reminders, ops and the review UI shipped

- What: completed every remaining `plan/` item and wired it into one local app.
  Added SQLite-backed accounts with `hashlib.scrypt` hashes, server-side
  sessions (opaque cookie), five-attempt/15-minute lockout, and the
  admin/uploader/viewer matrix. Confirmed rows now land in the `bg_records`
  register (masters auto-create on first use), with search, status/project/
  overdue/30-day filters, 20-column export, admin corrections, extension
  closing the prior guarantee and workbook mirroring. T-7 reminders are
  idempotent, run at 09:00 IST in-process, go to `out/mail/*.eml` when no SMTP
  host exists, and can be re-run or resent. Ops: rotating log, `bgauto backup`
  zip (keep 7), audit log and `/internal/daily-reminder` token guard.
- UI: new `login.html` plus a rewritten `index.html` (Review, Runs, Register,
  Reminders, Users, Audit) with no external assets, role-aware navigation and
  an honest confirm result (`written` / `not_written` / closed-by-extension).
  Uploader corrections are saved when they submit, so the admin opens the
  maker's values, not raw OCR.
- Docker: compose passes accounts, mail/DL, scheduler and token settings
  through, and `.env.example` documents them. A brand-new `BG_DB` now
  initialises from `db/sqlite/schema.sql` instead of failing silently.
- Fixed while verifying live: failed-login counters and `login_failed`/`locked`
  audit rows were rolled back by the request transaction (lockout never
  persisted); confirming an already-confirmed run returned 400 instead of 403;
  a failed edit of `To` could violate the claim-period CHECK; the UI reported
  "already confirmed" after a confirm that wrote nothing.
- Verified: 90 tests pass. In an isolated Docker container (fresh DB, port
  8010) 74 live checks passed: bootstrap/login/401/403 matrix, cross-origin
  rejection, upload + OCR of the ICICI sample, uploader submit, missing-required
  block, admin confirm to DB + workbook, duplicate/frozen run, register
  filters/export/edit, dry-run reminder + resend, internal token, audit trail.
  The real register, workbook and `BG Tracker.xlsx` were not touched.
- Files: `backend/bgauto/{auth,store,register,scheduler,mailer,backup,schema,excel}.py`,
  `backend/bgauto/web/{api,runs,security,index.html,login.html,__main__}.py`,
  `backend/tests/*`, `backend/README.md`, `docker-compose.yml`,
  `.env.example`, `.gitignore`, `db/sqlite/*`, `plan/00-log.md`.

### 2026-10-29 — Team — Nature of Service missed when OCR keeps the margin line number

- What: `_nature` now scans `_flatten(lines)` (margin numbers stripped) instead of
  the raw line join. Two regression tests added.
- Why: found while checking the container logs and output. Debian Tesseract 5.5.0
  broke the deed line as `... Bridae Repair &` / `11 Rehabilitation - Bareilly`,
  so the hint `REPAIR & REHAB` never matched and the ICICI guarantee came back
  with `Nature of Service` blank + `nature_not_found`. `LOA Number` already
  flattened for exactly this reason; Nature did not.
- Verified: the same container OCR text now yields `Repair & rehabilititation`
  (`derived_from_text`, no flags), and a live upload on port 8000 returns zero
  open flags for the ICICI sample.
- Files: `backend/bgauto/extract.py`, `backend/tests/test_extract.py`.

### 2026-10-29 — Team — Whole app containerised (Docker + Tesseract)

- What: added a root `Dockerfile` (`python:3.14-slim`, Debian `tesseract-ocr` +
  `tesseract-ocr-eng`, pip deps first for layer caching, non-root uid 10001,
  healthcheck against `/api/health`), a root `docker-compose.yml` (`app` service
  on loopback `${BG_WEB_PORT:-8000}:8000`, bind mounts for `out/`, `data/`,
  `db/sqlite/`, read-only `BG Tracker.xlsx`) and `.dockerignore`.
  `backend/requirements.txt` now pins the UI stack (`fastapi`, `uvicorn`,
  `python-multipart`) it had been relying on from the venv only.
- Why: the user asked for the whole app in Docker; the only compose file
  (`db/docker-compose.yml`) is Postgres-only and there was no Dockerfile, so
  `docker compose build` from the repo root failed with "no configuration file".
- Verified in the container on host port 8010 (8000 is the live local server):
  `/api/health` -> `status: ok`, `ocr_ok: true`, `tesseract: /usr/bin/tesseract`;
  masters 4 companies / 28 projects / 46 plazas; real OCR uploads gave HDFC ->
  Project blank (34 s, 3 pages) and ICICI -> `BS`, `0816NDLG00007526`,
  4,699,752, Expired (45 s, 6 pages). UI loads with no external hosts and no
  console errors. Test run directory removed afterwards.
- Notes: the container runs as uid 10001, no secrets are copied (`.env` is in
  `.dockerignore`), and `BG Tracker.xlsx` is mounted read-only so the
  maker-checker rule survives containerisation. Debian ships Tesseract 5.5.0
  (host has 5.5.3); both samples read identically.
- Files: `Dockerfile`, `docker-compose.yml`, `.dockerignore`,
  `backend/requirements.txt`, `backend/README.md`.

### 2026-10-29 — Team — Project and toll-plaza masters imported from operations

- What: imported the operations master document
  (`Project_Site_Tracker/docs/master-data.md`) into the SQLite masters:
  3 SPVs (NHIT Eastern, Southern, Western Projects), 28 corridors and 46 toll
  plazas with chainage and site managers. `company`, `project` and `toll_plaza`
  gained `source`, `is_active`, and the corridor/plaza attributes; the import is
  idempotent and deactivates rows missing from the document instead of deleting
  them. New CLI: `python -m bgauto.cli import-masters <file.md> [--dry-run]`.
- Why: the dropdowns were fed from a 12-project / 32-plaza seed that did not
  match real corridors, so every real BG showed Project and Toll Plaza as blank.
  The seed also held plaza rows that were really corridor names
  ("Bareilly Sitapur", "Simga - Bilaspur") and OCR spelling variants.
- Extraction: `Project Name` is now derived when the document names the corridor
  (full name, or a code inside a reference such as `/KRK/`, `[KRK]`,
  `Project: KRK`). A single toll-plaza match also fills the corridor. Two-letter
  codes in prose are ignored on purpose: "AS" is not Abu Road-Swaroopganj.
  Verified on both samples: ICICI -> `BS` (Bareilly-Sitapur), HDFC -> blank
  (Incident Management is head-office work).
- Decision left open: `Company Name` still comes from the BG text (`NSPPL`), not
  from the corridor's SPV. The UI shows "BS belongs to NHIT Southern Projects
  Private Limited" as a hint only; changing the column's meaning needs sign-off.
- Files: `backend/bgauto/{masterdata,schema,masters,extract,cli}.py`,
  `backend/bgauto/web/{api.py,index.html}`, `db/sqlite/schema.sql`,
  `db/sqlite/bg_automation.sqlite3`, `backend/tests/test_masterdata.py`,
  `backend/README.md`.

### 2026-10-28 — Team — Local Tesseract ingestion pipeline (no Azure)

- What: built `backend/bgauto/`, a local-only pipeline that turns scanned BG
  PDFs into the 20 tracker columns. PyMuPDF renders each page at 300 dpi;
  Tesseract 5.5.3 OCRs every page twice (grayscale and a hard-threshold
  binarisation that removes bank watermarks) and the higher-confidence pass
  wins. Extraction is label-anchored (OUR REFERENCE, GUARANTEE AMOUNT, EXPIRY
  DATE, ...) with fallbacks for cover-letter tables and deed clauses.
- Why: Azure Document Intelligence is blocked for now, and both sample BGs are
  image-only (0 extractable characters). Without OCR there is no automation.
  Tesseract keeps every BG page on the machine.
- Policy exception: plan R6 prefers enterprise-only tooling; Tesseract is open
  source. The user explicitly accepted this exception for the current phase.
  Revisit when Azure DI is approved, or if licensing/support review requires
  it.
- Safety: output goes to a copy (`out/<run>/BG Tracker_ingested.xlsx`) plus
  review JSON/CSV and an OCR text archive. `BG Tracker.xlsx` is only ever read
  by this tool. A missing or failed field is left blank with a blocking
  `flags` entry; a usable but derived value carries a non-blocking `notes`
  entry (`auto_status`, `date_scanned`, `from_table_row`, `derived_from_text`).
- Validation: amount digits vs amount in words, date ordering
  (From <= To <= Claim), BG No shape plus filename cross-check, and duplicate
  BG No skipped on rerun. A damaged BG No token is only accepted when it
  matches the filename hint character for character apart from known OCR
  confusion pairs (0/O, 1/I, 5/S, 8/B, 6/G, 2/Z, 7/T); OCR alone cannot tell
  `2646T...` from `2646TO...`, so it is never guessed.
- Status: both sample PDFs extract end to end with no blocking flags. HDFC
  `264GT02252580004` (INR 40,78,080; 2025-09-15 to 2028-10-11, Active) and
  ICICI `0816NDLG00007526` (INR 46,99,752; 2025-11-15 to 2026-04-06, Expired).
- Files: `backend/bgauto/*`, `backend/tests/*`, `backend/requirements.txt`,
  `backend/README.md`, `.gitignore`.

### 2026-10-08 — Team + team — Documentation rewrite (v3)

- What: rewrote all `plan/` files and the master plan in a standard format
  (frontmatter, purpose, scope, acceptance criteria, references). Fixed grammar,
  headings, and inconsistent terms.
- Why: previous drafts were hard to follow. New format is readable by any
  engineer or AI agent without extra explanation.
- Files: `BG_Automation_Plan.md`, `plan/README.md`, `plan/01`–`06`, `plan/00-log.md` (this file).

### 2026-10-08  — Auth stories added (ST011–ST013)

- What: added three user stories under UC006, assigned to Team, Sprint 1.
  ST011 = admin confirm-only. ST012 = uploader drafts, no confirm.
  ST013 = viewer read and export only.
- Why: the three-role maker-checker model needed tracking in the story sheet.
- Files: `BG_UseCase_Story_Tracking.xlsx` (User Stories sheet, rows 12–14).

### 2026-10-08 — Team — Auth model locked (maker-checker)

- What: replaced "uploader can approve own work" with strict maker-checker.
  Uploader edits and saves their own rows to the register and workbook.
  Viewer reads and exports only.
- Why: separation of duties. Prevents one person from uploading and approving
  their own entry.
- Files: `BG_Automation_Plan.md` §7, `plan/03-auth.md`.

### 2026-10-08 — Team — Plan detail folder created

- What: created `plan/` with six detail files (architecture, data model, auth,
  OCR, flows, email/hosting) with Mermaid diagrams.
- Why: the master plan was too dense. Detail files let builders focus on one
  topic at a time.
- Files: `plan/01-architecture.md` … `plan/06-email-scheduler-hosting.md`.

### 2026-10-08 — Team — Tracker format locked (20 columns)

- What: confirmed `BG Tracker.xlsx` (Sheet1) as the format of record.
  Added 5 columns: LOA Number, PO Number, source_pdf, created_by, created_at,
  reminder_sent_at. Fixed typo `Contator` → `Contractor`. Trimmed trailing
  spaces from headers. New columns Company Name and Toll Plaza Name kept.
- Why: the old tracker missed audit fields and the LOA reference (ST004).
- Files: `BG Tracker.xlsx`.

### 2026-10-07 — Team — Initial build plan (v1)

- What: first version of the plan. Workflow (upload → OCR → review → Excel →
  T-7 mail). Constraints (enterprise-only, SMTP-only, local login, India-only
  data, Windows VM). Azure DI in Central India recommended.
- Files: `BG_Automation_Plan.md`, `BG_OCR_Options_Comparison.md`.

## 3. Pending decisions

| # | Decision needed | Owner | Status |
|---|----------------|-------|--------|
| 1 | SMTP mailbox, Finance DL, User Team DL, template sign-off | TBD | Open |
| 2 | Datalab plan/quota owner and cost-alert contact | TBD | Open |
| 3 | Confirm `To` (not Claim Period) as the T-7 anchor | TBD | Open |

## References

- Master plan: `../BG_Automation_Plan.md`
- Index: `README.md`
