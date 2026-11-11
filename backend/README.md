# Local BG ingestion (`bgauto`)

Scanned Bank Guarantee PDF -> the 21 tracker columns. Extraction runs through a
**Datalab pipeline** (convert -> structured extract); the register, review
workbook and masters stay on this machine. Confirmed rows are appended to a
**copy** of `BG Tracker.xlsx`; the source tracker is never edited.

```
Sample_Bank_Guarantees/*.pdf
        |
        v
  Datalab pipeline (convert -> extract JSON)
        |
        v
  master resolution + normalisation (blank beats wrong)
        |
        +--> out/<run>/BG Tracker_ingested.xlsx   review workbook (21 columns)
        +--> out/<run>/review/*.json, summary.csv flags/notes per field
        +--> out/<run>/pipeline/*.json            raw convert + extract results
        +--> out/<run>/ocr/*.json                 normalised report + page text
```

## Prerequisites

- Python 3.11+ (Docker uses Python 3.13) with the venv at `.venv`
- A Datalab API key (https://www.datalab.to/app/keys) or a saved `datalab login`

```powershell
.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

## Datalab pipeline

Every uploaded PDF (or CLI file) runs through one published Datalab pipeline:
first `convert`, then structured `extract`. The extract step's JSON is the
authoritative source for the tracker fields; the app only normalises values and
resolves company/project/plaza/bank against the local masters. There is no
OCR-text rule fallback. A missing or invalid value becomes a blank cell with a
flag so the reviewer sees it.

The pipeline and its extraction schema are maintained in the Datalab UI (the
schema is not in this repository; the v1 field contract is mirrored for readers
in `plan/04-ocr-extraction.md` §3.1). Pin the release with
`BG_DATALAB_PIPELINE_VERSION` so a manual edit cannot silently change results.
The key is read from `DATALAB_API_KEY` in the git-ignored `.env` file (loaded by
the app and by Compose), from a real environment variable, or from the SDK's
saved login at `~/.config/datalab/config.json`. The key is never written to
source, committed config or logs.

- **Pipeline.** `BG_DATALAB_PIPELINE_ID` (default `pl_INVNggnY7MaE`) and
  `BG_DATALAB_PIPELINE_VERSION` (default `1`: the production pipeline is
  deployed on a fresh Datalab account, so its first published version is 1).
  Leave the version empty to follow the active published version. While testing
  on the development account, that pipeline publishes the same schema as
  version 3, so use `3` there.
- **Snapshots.** `docs/datalab-schema-v1.json` holds the exact v1 schema and
  `docs/datalab-pipeline-v1.json` the full pipeline definition (both steps), so
  the setup can be rebuilt if the Datalab account or provider changes. The
  snapshot records the development account's active version (3) and the
  production target (1). They are reference files; the app does not read them.
  `docs/Fields To Capture From BG Through D.md` keeps the original wish-list and
  its v1 coverage table.
- **Extraction mode.** Stored in the pipeline itself (currently `turbo`);
  `BG_DATALAB_EXTRACTION_MODE` is informational and only reported by health.
- **Cache.** The adapter sets `skip_cache=true`. A cached convert checkpoint has
  no downloadable step result, so page text and the raw archive would be missing.
- **Extras.** Authorised signatories, stamp-duty certificates, amount in words,
  type wording and the printed project name are kept in the run archive and the
  review card's read-only **Document extras** section. They are not tracker
  columns.
- **Confidence.** When the convert step returns a `parse_quality_score` (0–5),
  the app shows `score / 5` with source `datalab_quality`. `turbo` usually
  omits it; the UI and CLI then show no confidence rather than 0%.
- **Failure.** A missing key, exhausted allowance (HTTP 402), failed convert or
  failed extract fails that upload with a readable message; the PDF is removed
  from the run and nothing is written to the register. There is no blank-row
  fallback.
- **Archive.** `out/web/<run>/pipeline/<pdf>.json` holds both raw step results;
  `out/web/<run>/ocr/<pdf>.json` holds the normalised report. Datalab expires
  results after about a day, so both are written immediately.
- **Health.** `GET /api/health` reports the pinned pipeline id/version and
  checks that the SDK is installed and a key or saved login exists. It does not
  spend an API call.

`bgauto/compare.py` is kept for reference and its unit tests still pass, but the
upload flow no longer runs or displays an engine comparison: the Datalab
pipeline is the only engine.

**Privacy.** The PDF leaves this machine on its way to Datalab. If that is not
acceptable for a document, do not upload it. The register, workbook, database
and pipeline archive stay local.

## Run

```powershell
$env:PYTHONPATH = "backend"
.venv\Scripts\python.exe -m bgauto.cli ingest "Sample_Bank_Guarantees/*.pdf" `
    --out "out/sample_run/BG Tracker_ingested.xlsx" `
    --report "out/sample_run/review" `
    --archive-dir "out/sample_run/ocr"
```

| Flag | Default | Meaning |
|---|---|---|
| `--out` | `out/BG Tracker_ingested.xlsx` | review workbook (template copied on first run) |
| `--report` | `out/review` | JSON + `summary.csv` for the reviewer |
| `--tracker` | `BG Tracker.xlsx` | template of record; only ever read |
| `--archive-dir` | `data/ocr-json` | per-PDF pipeline step results and report |
| `--db` | `db/sqlite/bg_automation.sqlite3` | masters for dropdown matching |

Exit code 0 means every PDF was read; the summary and review files say which
fields need a human. Re-running into the same workbook skips duplicate BG
numbers, so delete the output workbook (or pass a new `--out`) to start clean.

## Review UI: sign-in and roles

`python -m bgauto.web` serves the same pipeline as a small local app at
`http://127.0.0.1:8000` (or the Docker port). Every page, stylesheet and script
is served by the process without a CDN. Uploaded PDFs go to the Datalab
pipeline; the optional Firecrawl lookup sends only the extracted names and IFSC
codes.

| Action | admin | uploader | viewer |
|---|---|---|---|
| Upload, review, save a run | yes | yes, own runs only | no |
| Save own uploaded rows to register + workbook | yes | yes | no |
| View/download the review workbook | yes | yes | yes |
| Search the register | yes | no (their queue is **Runs**) | yes |
| Export the register | yes | no | yes |
| Read T-7 reminders / resend | read + resend | no | read |
| Users and audit log | yes | no | no |

- **First account.** On an empty database the app creates an admin from
  `BG_BOOTSTRAP_ADMIN_USER` / `BG_BOOTSTRAP_ADMIN_PASS`. If no password is
  configured, a random one is written once to the log
  (`docker compose logs app`); change it after the first sign-in.
- **Passwords.** At least 8 characters (local pilot; tighten again when SSO
  arrives) with upper case, lower case, a digit and a symbol. Hashes use
  `hashlib.scrypt` with a per-user salt.
- **Adding people (for now).** The Users screen asks only for a username and a
  temporary password and creates a **viewer**; change the role in the users
  table afterwards. Name and email stay in the schema for the planned SSO
  sign-in. The local logins for this deployment are in `credentials.txt`
  (git-ignored).
- **Sessions.** Opaque tokens stored in SQLite, not JWTs. The cookie is
  `httpOnly`, `SameSite=Lax`, and `Secure` only when `BG_COOKIE_SECURE=1`
  (plain-HTTP loopback keeps it off). Sessions expire after
  `BG_SESSION_MINUTES` (default 30); changing a password revokes the others.
- **Lockout.** Five failed sign-ins lock the account for 15 minutes; every
  attempt is in the audit log.
- **Uploader ownership.** Uploaders can save rows from their own runs directly
  to the register and review workbook. The API checks run ownership; admins can
  access all runs.
- **Drafts stay editable.** Uploaders review extracted values before saving;
  confirmed runs are frozen.

## Run in Docker

The same app (FastAPI UI + Datalab pipeline) runs as one container. The image
holds the SDK; PDFs are sent to the Datalab pipeline using the key from `.env`.

```powershell
docker compose build
docker compose up -d          # -> http://127.0.0.1:8000
docker compose ps             # waits for (healthy)
docker compose logs -f app
```

- **Port.** The container listens on 8000. The host side is loopback-only and
  configurable: `$env:BG_WEB_PORT="8010"; docker compose up -d` -> port 8010.
  If the port is taken, compose says `port is already allocated`; pick another.
- **Mounts.** `out/`, `data/` and `db/sqlite/` come from the host, so review
  workbooks, OCR archives and the masters database are the same files a local
  run produces. `BG Tracker.xlsx` is mounted read-only.
- **Template.** It must exist before `up`, because compose bind-mounts it.
- **Health.** `GET /api/health` checks that the Datalab SDK is installed and a
  key or saved login exists. It makes no paid call; a missing key reports
  `status: degraded`.
- **Rebuild after code changes.** `docker compose build && docker compose up -d`;
  `docker compose restart` reuses the old image.
- **CLI inside the container.** Same image, so the importer and ingest command
  work there too:

  ```powershell
  docker compose cp ..\Project_Site_Tracker\docs\master-data.md app:/tmp/m.md
  docker compose exec app python -m bgauto.cli import-masters /tmp/m.md --dry-run
  ```

The container runs as uid 10001 (`bgauto`), never as root. On Docker Desktop
the bind mounts are world-writable, so that works unchanged; on a Linux VM give
that uid write access to `out/`, `data/` and `db/sqlite/`.

`db/docker-compose.yml` is a **separate** compose file for the planned
PostgreSQL deployment; the app here does not use it. Start it only with
`-f db/docker-compose.yml`, and set `POSTGRES_PORT` because 5432 is often taken.

## Environment and compose settings

Copy `.env.example` to `.env` (git-ignored) and edit it; compose substitutes
those values into the container. The app still starts with an empty file.

| Variable | Default | Purpose |
|---|---|---|
| `BG_WEB_PORT` | `8000` | host loopback port |
| `BG_BOOTSTRAP_ADMIN_USER` | `admin` | first account, only when no users exist |
| `BG_BOOTSTRAP_ADMIN_PASS` | empty | empty => random password in the log |
| `BG_SESSION_MINUTES` | `30` | server-side session lifetime |
| `BG_COOKIE_SECURE` | `0` | set `1` only behind HTTPS |
| `BG_SMTP_HOST` / `BG_SMTP_PORT` | empty / `587` | empty host => reminders dry-run |
| `BG_SMTP_USER` / `BG_SMTP_PASS` | empty | SMTP credentials |
| `BG_MAIL_FROM` / `BG_MAIL_FROM_NAME` | local defaults | sender |
| `BG_MAIL_DRY_RUN` | `1` | `1` never sends even with SMTP; `0` sends |
| `BG_FINANCE_DL` / `BG_USER_TEAM_DL` | empty | comma-separated recipients |
| `BG_REMINDER_DAYS` | `7` | T-N window |
| `BG_APP_URL` | `http://127.0.0.1:8000` | link inside the reminder |
| `BG_SCHEDULER` / `BG_SCHEDULER_TIME` | `1` / `09:00` | in-process IST scheduler |
| `BG_INTERNAL_TOKEN` | empty | protects `/internal/daily-reminder` |
| `DATALAB_API_KEY` | empty | Datalab key for the pipeline; health is degraded without it |
| `BG_DATALAB_MODE` | `fast` | mode used only by `ocr.convert_pdf` (reference/test path); pipeline runs use the pipeline's convert settings |
| `BG_DATALAB_PIPELINE_ID` | `pl_INVNggnY7MaE` | published pipeline to run |
| `BG_DATALAB_PIPELINE_VERSION` | `1` | pinned production version (fresh account starts at 1); empty = active version |
| `BG_DATALAB_EXTRACTION_MODE` | `turbo` | informational only; reported by health |
| `BG_DATALAB_BASE_URL` | `https://www.datalab.to` | API base URL |
| `BG_DATALAB_TIMEOUT` | `600` | seconds allowed for one conversion |
| `BG_DATALAB_MAX_POLLS` / `BG_DATALAB_POLL_INTERVAL` | `300` / `2` | polling limits |
| `BG_FIRECRAWL_API_KEY` | empty | Firecrawl key for the official-name check; empty = off |
| `BG_LOOKUP` | `1` | `0` disables lookups even when a key is set |
| `BG_LOOKUP_TIMEOUT` | `120` | seconds to wait for one PDF's two lookups |
| `BG_LOOKUP_MAX_CREDITS` | `100` | credit cap per agent job |
| `BG_LOOKUP_TTL_DAYS` | `30` | reuse an answer for identical text |
| `BG_LOOKUP_MAX_MATCHES` | `5` | alternatives offered in the dropdown |

`BG_DB`, `BG_DATA_DIR` and `BG_OUT_DIR` are also honoured by `config.py`. A
brand-new `BG_DB` is initialised from `db/sqlite/schema.sql` on startup, so a
fresh Docker volume works without a manual import.

## Official-name lookup (optional)

OCR can misread spacing and spelling (`HDFC Bank Limtied`, `NHIT Southern`). With a
Firecrawl key the upload also asks the web for the official Indian names:

- **Two agent jobs per PDF, in parallel** - one for **Bank Name**, one for
  **Name of Contractor**. Both are part of the normal upload request, so the
  review card appears only after OCR *and* both checks finish. The queue says
  "reading… then checking official names".
- **Bank prompt gets the IFSC codes** found in the OCR text, labelled. The
  issuing bank's IFSC is listed first and the prompt says the issuing bank is
  the one we need, so a beneficiary bank named in the same document is not
  mistaken for it.
- **Top match is selected by default.** The field becomes a dropdown with the
  other matches (each with its IFSC/CIN/LLPIN where known) plus
  `as read: <OCR text>`. The hint under the field shows the identifier and the
  source host, or `no CIN/IFSC — check the source`.
- **India only.** Both prompts restrict the answer to Indian banks/companies
  (MCA registration for companies) and ask for an empty list when unsure.
- **Failures never block.** A timeout, HTTP error, empty answer or missing key
  leaves the OCR value selected and the uploader can still save the run.
  Errors appear as `web check unavailable — kept as read`.
- **Cache, not matcher.** The answer is cached in the `lookup_cache` table by
  the exact name text (and IFSC context) for `BG_LOOKUP_TTL_DAYS`; identical
  wording is not billed twice. No fuzzy matching happens locally.
- **Provenance.** A name chosen from the web that is not already in the
  registry is created on confirm with `source = 'web'` (companies, banks and
  contractors). The bank and contractor tables start empty; they are filled by
  confirmed BGs, not seeded from a list.

**Privacy.** Only the OCR'd bank name, contractor name and up to three IFSC
codes leave the machine for this lookup. The PDF, amounts, dates, addresses and
beneficiary details do not (the PDF was already sent to Datalab for OCR). Keep
the key server-side in `.env`, and rotate it if it is ever pasted into a chat or
ticket.

## Register, T-7 reminders and operations

- **Register.** Only confirmed rows appear. The admin can correct, extend or
  close a row; extensions close the earlier guarantee in the same scope
  (contractor + corridor + plaza + BG type, and a later `From` date) instead of
  deleting it, so one LOA covering several corridors or plazas is left alone.
  Every change is mirrored into `out/BG Tracker_ingested.xlsx` and recorded in
  `audit_logs`.
- **Reminders.** The daily check looks at `To` (T-7 by default) and is
  idempotent: a BG gets one reminder per due date. With no SMTP host each
  message is written to `out/mail/*.eml`; the Reminders screen shows the exact
  recipients and status. Admins can re-run or resend.
- **Manual trigger.** `POST /api/reminders/run` (admin session) or
  `POST /internal/daily-reminder` with `X-Internal-Token` when the token is set.
- **Audit.** Sign-ins (including failures and lockouts), uploads, submissions,
  confirms, register edits, exports and reminders are all in the admin Audit
  screen. The run folder also keeps `confirm-*.json` and
  `out/web/confirmations.jsonl`.
- **Backup.** `python -m bgauto.cli backup` zips the database, review workbook,
  template and PDF archive under `data/backups/`, keeping the last
  `--keep` archives (default 7). Logs rotate at `out/logs/app.log`.
- **Deviations from `plan/`.** Sessions are opaque server-side tokens in SQLite
  (not JWTs) and passwords use `hashlib.scrypt` (not bcrypt). Duplicate BG
  numbers are reported honestly in the save response instead of a blanket 409.
  Uploaders work in Review/Runs and can view the read-only workbook page.

## Import project and toll-plaza masters

The dropdowns are fed from the SQLite masters, not from a hard-coded list. The
operations master document (markdown: SPV -> corridor -> toll plaza, with chainage
and site managers) is the source of truth:

```powershell
$env:PYTHONPATH = "backend"
.venv\Scripts\python.exe -m bgauto.cli import-masters ..\Project_Site_Tracker\docs\master-data.md
```

Rules the importer follows:

- **Idempotent.** Codes are matched case-insensitively; running it twice reports
  no changes.
- **Additive.** Corridors and plazas that disappear from the document are set
  `is_active = 0`, never deleted, so old BGs still resolve.
- **Provenance.** Imported rows are tagged `source = 'imported'`; rows created by
  hand (`source = 'manual'`) are never touched.
- `--dry-run` reports the same plan without committing.

The database file is tracked, so re-running the importer is the normal way to
absorb an updated master document.

The seeded dataset (`db/sqlite/seed.sql`) mirrors the same document: 28
corridors across three SPVs and 46 plazas, each plaza attached to exactly one
corridor. Bank and contractor are not seeded at all: those tables are a registry
that starts empty and gains a row from each confirmed BG (after the optional
Firecrawl name check). Corridor names, combined spellings (`Bhojpuri & Mudipar`)
and OCR aliases are deliberately not seeded. The matcher resolves spelling drift
by similarity instead (`Chappar` -> `Chapar`, `Veeravali` -> `Veeravalli`,
`Nathavalsa` -> `Nathavalasa`, `Gadanki` -> `Gadnaki`,
`Mahasamundaram` -> `Mahasamudram`, `Dukkavaniplem` -> `Dukkavanipalem`).
`backend/tests/test_master_seed.py` re-applies the seed and asserts the whole
mapping, so a wrong or duplicate row fails the suite.

## Tests

Unit and HTTP tests run against a temp database and temp output directory:
extraction, masters, schema bootstrap, scrypt auth/lockout, the role matrix,
confirm + workbook writes, register corrections, reminders, the Datalab adapter
(client and options faked), and the Firecrawl lookup with a fake agent. No
network call is made, and the real `BG Tracker.xlsx` is never touched.

```powershell
$env:PYTHONPATH = "backend"
.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

## Reading the output

Each field carries two independent lists:

- **`flags`** - blocking. The value is missing or failed a check
  (`amount_vs_words_mismatch`, `claim_before_to`, `bg_no_differs_from_filename`,
  ...). Do not confirm the row until these are cleared.
- **`notes`** - informational. The value is usable but derived rather than
  printed (`auto_status`, `date_scanned`, `from_table_row`, `derived_from_text`,
  `review_dropdown`).

`Extended`, `Remarks`, `created_by` and `reminder_sent_at` are reviewer- or
scheduler-owned and always blank from the pipeline. `Project Name` and
`Toll Plaza Name` are filled only when Datalab names them and the local masters
resolve them; otherwise they are blank with a `project_not_found` /
`plaza_not_found` note for the reviewer. Corridor matching tolerates spelling
slips and `-`/`to`/space separators (`Bareily to Sitapur` -> `BS`). When a
guarantee names several toll plazas the first one is shown, the rest are listed
in an `also_covers` note, and confirm writes one workbook row per plaza. The
register still keeps one row per guarantee so reminders stay per BG.

## What is extracted

Datalab returns the values; the app normalises them and resolves masters. It
never re-derives a missing value from page text.

| Column | Source |
|---|---|
| Company Name | full legal SPV of the resolved project (`NSPPL`/`NEPPL`/`NWPPL` are aliases of Southern/Eastern/Western); Datalab's beneficiary when the project is unknown |
| Project Name | Datalab's printed corridor name matched to the project master, else the all-plaza agreement or the LOA reference |
| Toll Plaza Name | Datalab's ordered plaza list matched to the plaza master; other plazas go to `also_covers` and extra Excel rows |
| Types of BG | Datalab's `bg_type`, then the printed type wording (Performance -> PBG, Advance -> ABG) |
| Nature of Service | Datalab's printed service, fuzzy-matched to the `nature_of_service` master |
| Name of Contractor | Datalab's applicant/contractor name, cleaned and matched to the contractor master |
| BG No | Datalab's number, validated and cross-checked against the filename |
| BG Amount | Datalab's numeric amount, cross-checked against the amount in words |
| From / To / Claim Period | Datalab's three dates, normalised to `YYYY-MM-DD` |
| Bank Name | Datalab's issuing bank, matched to the bank master |
| Status | `To` vs today (Expired/Active), flagged `auto_status` |
| LOA Number | Datalab's `loa_or_tender_number` |
| PO Number | Datalab's `po_number` |
| Extras (not columns) | authorised signatories, stamp-duty certificates, amount in words, type wording, printed project name |

## Known limits

- Datalab reads printed text; handwritten notes and rubber stamps are still not
  parsed reliably.
- The bank must print recognisable labels; a BG with no labels goes to review.
- BG number shape is assumed to be `NNN[A-Z]+NNNNNN` (e.g. `264GT02252580004`,
  `0816NDLG00007526`); other issuers may need the regex in
  `normalize.normalise_bg_no` widened.
- Status is a suggestion: Expiring/T‐7 reminders still come from the review
  step, not from the pipeline.
- The official-name check needs a Firecrawl key and internet access. Without
  either (or after a timeout) the reviewer sees the extracted name and any names
  already created by earlier confirmed BGs, so nothing is blocked but no web
  name is suggested.
