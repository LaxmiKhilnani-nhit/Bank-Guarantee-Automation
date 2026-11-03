# Local BG ingestion (`bgauto`)

Scanned Bank Guarantee PDF -> the 21 tracker columns. Indra reads locally by
default; Tesseract is available locally, and Azure Document Intelligence is an
optional remote OCR engine for testing. Confirmed rows are appended to a
**copy** of `BG Tracker.xlsx`; the source tracker is never edited.

```
Sample_Bank_Guarantees/*.pdf
        |
        v
  local Indra / local Tesseract / optional Azure --> extraction + validators
        |
        v
  label-anchored extraction + validators (blank beats wrong)
        |
        +--> out/<run>/BG Tracker_ingested.xlsx   review workbook (21 columns)
        +--> out/<run>/review/*.json, summary.csv flags/notes per field
        +--> out/<run>/ocr/*.json                 raw page text + confidence
```

## Prerequisites

- Python 3.10+ (Docker uses Python 3.13) with the venv at `.venv`
- Tesseract 5 with `eng` language data

```powershell
.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Tesseract lookup order (`bgauto/config.py`):

1. `TESSERACT_CMD` environment variable
2. `%LOCALAPPDATA%\bgauto\tesseract\Library\bin\tesseract.exe` (the no-admin
   micromamba install used here)
3. `C:\Program Files\Tesseract-OCR\tesseract.exe` and the x86 equivalent
4. `tesseract` on `PATH`

Language data is found next to the binary (`../share/tessdata` or
`./tessdata`); override with `TESSDATA_PREFIX`.

No-admin install used on this machine:

```powershell
$dir = "$env:LOCALAPPDATA\bgauto"
# micromamba.exe from https://micro.mamba.pm/api/micromamba/win-64/latest
$env:MAMBA_ROOT_PREFIX = "$dir\mamba"
& "$dir\tools\Library\bin\micromamba.exe" create -y -p "$dir\tesseract" -c conda-forge tesseract
```

## Indra local OCR

The app uses the upstream `indra-ocr` Python package and runs its ONNX models
inside the app container. The model files download on first OCR use and remain
in `./data/huggingface`; PDFs and page text are not sent to an OCR service.
The default `auto` mode reads a valid PDF text layer and OCRs scanned pages.
Choose `ocr` with `BG_INDRA_MODE=ocr` to run OCR on every page regardless of
the text layer. `BG_INDRA_THREADS` controls local CPU use. Tesseract remains
available in the upload selector and is the fallback if Indra cannot run.

Indra's Python package and its model weights have separate license terms; the
weights are fetched from the upstream model repository at first use. Check the
upstream terms for the intended deployment.

## Azure Document Intelligence test engine

Set `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`, and optionally `AZURE_DI_KEY_2` in the
local git-ignored `.env` file. Compose passes these values only to the app
container. The Azure and All engines are enabled only when an endpoint and key
are configured. Azure uses the `prebuilt-layout` model by default; override it
with `AZURE_DI_MODEL` if needed. It sends at most two pages and 4 MB per
request by default, which supports the observed F0 test resource limits and
automatically covers longer PDFs in page batches. Set `AZURE_DI_PAGE_BATCH_SIZE`
and `AZURE_DI_MAX_BYTES` up to the S0 limits only if the resource is Standard.
The secondary key is tried only when Azure rejects the primary key with an
authentication error.

Selecting Azure sends the PDF bytes to the configured Azure endpoint.
Selecting All runs local Indra and Tesseract plus Azure, so the PDF is sent to
Azure as part of the comparison. The default remains local Indra. Use sample
documents for tests unless remote processing of a document has been approved.

## How the engine comparison works

The old comparison picked one whole extraction: "most required fields, then
fewest flags, then confidence". That rewards an engine for filling one more box,
even when the other engine read the page correctly, and it cannot compare a
one-page read with a full read.

`bgauto/compare.py` compares the things a reviewer can check:

1. **Page coverage first.** An engine that read 1 of 22 pages cannot become the
   base just because it filled one more field on that page.
2. **Per field.** Each engine votes for a value.
   - Both engines agree: the value is kept with no flag.
   - They disagree: the base engine's unflagged value is kept and flagged
     `engine_disagrees`; the other value stays visible in the comparison panel.
   - One engine is blank: the other engine's value fills the gap and is marked
     `from_<engine>`.
3. **Per page.** The panel shows how many characters each engine read on each
   page and the word overlap between engines on that page, so a page that only
   one engine read is obvious.

Indra line confidence is the OCR model's reported confidence. Tesseract
confidence is based on recognized words. The UI displays the source engine's
confidence value when it is available.

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
| `--archive-dir` | `data/ocr-json` | per-PDF OCR text and confidence |
| `--db` | `db/sqlite/bg_automation.sqlite3` | masters for dropdown matching |
| `--dpi` | `300` | render DPI |

Exit code 0 means every PDF was read; the summary and review files say which
fields need a human. Re-running into the same workbook skips duplicate BG
numbers, so delete the output workbook (or pass a new `--out`) to start clean.

## Review UI: sign-in and roles

`python -m bgauto.web` serves the same pipeline as a small local app at
`http://127.0.0.1:8000` (or the Docker port). Every page, stylesheet and script
is served by the process without a CDN. Outbound calls are limited to optional
name lookup and Azure OCR when those features are enabled or selected.

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

The same app (FastAPI UI + local Indra/Tesseract OCR) runs as one container.
The image installs Tesseract locally, and Indra weights are cached in the
mounted `./data/huggingface` directory.

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
- **Health.** `GET /api/health` performs a real OCR round-trip; the Docker
  healthcheck fails if Tesseract cannot read the test image (`status: degraded`).
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
codes leave the machine. The PDF, amounts, dates, addresses and beneficiary
details do not. This is the one outbound call in the app; keep the key
server-side in `.env`, and rotate it if it is ever pasted into a chat or ticket.

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
confirm + workbook writes, register corrections, reminders, and the Firecrawl
lookup with a fake agent (no network). No OCR runs, and the real
`BG Tracker.xlsx` is never touched.

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

`Extended`, `Remarks` and `created_by` are reviewer-owned and always blank from
OCR. `Project Name` and `Toll Plaza Name` are filled only when the document
names them (see below); otherwise they are blank with a `project_not_found` /
`plaza_not_found` note for the reviewer. Corridor matching tolerates OCR slips
and `-`/`to`/space separators (`Bareily to Sitapur` -> `BS`). When a guarantee
names several toll plazas the first one is shown, the rest are listed in an
`also_covers` note, and confirm writes one workbook row per plaza. The register
still keeps one row per guarantee so reminders stay per BG.

## What is extracted

| Column | Source |
|---|---|
| Company Name | full legal SPV of the resolved project (`NSPPL`/`NEPPL`/`NWPPL` are aliases of Southern/Eastern/Western); OCR beneficiary only when the project is unknown |
| Project Name | fuzzy corridor name in the text, all-plaza agreement, or a code inside a reference (`/KRK/`, `[KRK]`, `Project: KRK`) |
| Toll Plaza Name | first plaza in the document from the masters; other plazas go to `also_covers` and extra Excel rows |
| Types of BG | "Performance Security/Guarantee" -> PBG, "Advance" -> ABG |
| Nature of Service | keyword hints mapped to the `nature_of_service` master (`Routine Maintenance` and `Supply & Installation of Overhead Gantry` included) |
| Name of Contractor | `APPLICANT` (`:` or `>`), "on behalf of:", the `(A)` deed clause, SFMS `:7033:`, then a covering-letter letterhead |
| BG No | `OUR REFERENCE`/`BG NO` votes + global scan + filename cross-check |
| BG Amount | `GUARANTEE AMOUNT` digits, cross-checked against the words |
| From / To / Claim Period | labelled dates, `valid up to`, then the cover-letter date triplet |
| Bank Name | bank keyword -> canonical bank name (no seeded list), then the optional Firecrawl check |
| Status | `To` vs today (Expired/Active), flagged `auto_status` |
| LOA Number | `LOA`/`Letter of Acceptance` label; `Tender No` is a marked last resort |
| PO Number | Explicit `Purchase Order`/`P.O.`/`Work Order` label |

## Known limits

- English-only Tesseract models. Hindi/regional text is not read.
- The bank must print recognisable labels; a BG with no labels goes to review.
- Handwriting and rubber stamps are not parsed.
- BG number shape is assumed to be `NNN[A-Z]+NNNNNN` (e.g. `264GT02252580004`,
  `0816NDLG00007526`); other issuers may need the regex in
  `normalize.normalise_bg_no` widened.
- Status is a suggestion: Expiring/T‐7 reminders still come from the review
  step, not from OCR.
- The official-name check needs a Firecrawl key and internet access. Without
  either (or after a timeout) the reviewer sees the OCR text and any names
  already created by earlier confirmed BGs, so nothing is blocked but no web
  name is suggested.
