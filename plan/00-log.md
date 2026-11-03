---
id: PLAN-LOG
title: Decision and Change Log
status: active
last_updated: 2026-11-02
version: 7
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
| 2 | Azure DI S0 resource group in Central India + cost-alert owner | TBD | Open |
| 3 | Confirm `To` (not Claim Period) as the T-7 anchor | TBD | Open |

## References

- Master plan: `../BG_Automation_Plan.md`
- Index: `README.md`
