---
id: PLAN-02
title: Data Model
status: active
last_updated: 2026-11-13
version: 4
inputs: ["BG Tracker.xlsx (Sheet1)", "docs/master-data.md", "db/migrations/V1__schema.sql", "db/migrations/V2__seed.sql", "db/migrations/V3__schema.sql"]
outputs: ["Tracker validation rules", "PostgreSQL schema"]
related: ["PLAN-01", "PLAN-04", "PLAN-05"]
---

# 02 — Data Model

## 1. Purpose

This document defines exactly what data we store, in which column, and what
counts as valid. Read it before building the review form, the Excel writer,
or any validation logic.

## 2. Scope

- In scope: the 20 Excel columns, the PostgreSQL tables, dedupe and validation rules.
- Out of scope: OCR regex details (see `04`) and mail templates (see `06`).

## 3. Excel tracker — `BG Tracker.xlsx`, Sheet1

This sheet is the format of record. Column order must not change.

| # | Column | Type | Required | How it is filled |
|---|--------|------|----------|------------------|
| 1 | Company Name | Dropdown | Yes | Reviewer picks from master list |
| 2 | Project Name | Dropdown | Yes | Reviewer picks from master list |
| 3 | Toll Plaza Name | Dropdown | Yes | Depends on Project |
| 4 | Types of BG | Dropdown | Yes | `PBG` or `ABG` (master `bg_type`) |
| 5 | Nature of Service | Dropdown | Yes | Reviewer picks from master list |
| 6 | Name of Contractor | Text | Yes | OCR-assisted (applicant block), editable |
| 7 | BG No | Text, **unique** | Yes | OCR; dedupe key |
| 8 | BG Amount | Number (INR) | Yes | OCR (`Rs`/`INR` + commas stripped), stored numeric |
| 9 | From | Date `DD-MM-YYYY` | Yes | OCR (issue or effective date) |
| 10 | To | Date `DD-MM-YYYY` | Yes | OCR (expiry or valid-till). **This is the T-7 anchor.** |
| 11 | Claim Period | Date or text | No | OCR; may be blank (ST008) |
| 12 | Extended | Yes/No | Yes, default No | Old row becomes Yes on extension |
| 13 | Bank Name | Text | Yes | OCR + normalised (SBI, HDFC, ICICI, Axis, PNB…) |
| 14 | Status | Dropdown | Yes | Draft, Active, Expiring, Expired, Extended, Closed |
| 15 | Remarks | Text | No | Free text |
| 16 | LOA Number | Text | No | OCR + regex; blank if unsure |
| 17 | PO Number | Text | No | OCR + regex or manual entry; blank if unsure |
| 18 | source_pdf | Text (system) | Yes | Original filename or stored path |
| 19 | created_by | Text (system) | Yes | Username of the uploader who saved |
| 20 | created_at | Date-time (system) | Yes | Save timestamp |
| 21 | reminder_sent_at | Date-time (system) | No | Set when the T-7 mail is sent; empty until then |

Rule: if OCR confidence is low, leave the field blank. Never guess (ST008).

## 4. PostgreSQL tables

PostgreSQL 16 (`postgres:16-alpine`, volume `bg_pgdata`) is the database of
record: users, sessions, the BG register, reminders and the audit trail all live
there. Excel stays the business record. Migrations `V1`–`V3` are the DDL source
of truth; they are applied in order by `python db/apply.py` or by the one-shot
`migrate` service (`db/bootstrap.py`), which also seeds the masters. V1 and V2
are the historical baseline; V3 is the parity layer the application code
actually reads.

Conventions: UUID primary keys for user-facing rows (`gen_random_uuid()`),
`BIGINT IDENTITY` for audit, `CITEXT` for usernames and email, `TIMESTAMPTZ`
(UTC) for instants, `DATE` for business dates, `JSONB` for OCR payloads and
audit snapshots. `set_updated_at()` triggers maintain `updated_at` on `users`,
`bg_records` and `loa_records`.

### 4.1 Masters and lookups

| Table | Columns | Seed |
|---|---|---|
| `company` | `id`, `name` (unique), `source`, `is_active` | 3 SPVs |
| `project` | `id`, `company_id`, `code` (unique), `name`, `state`, `round`, `project_manager`, `source`, `is_active` | 28 active corridors |
| `toll_plaza` | `id`, `project_id`, `name`, `chainage`, `toll_manager`, `maintenance_manager`, `corridor_manager`, `source`, `is_active`; unique `(project_id, name)` | 46 canonical plazas |
| `bank` | `id`, `name` (unique), `source` | none; first confirmed BG creates it |
| `contractor` | `id`, `name` (unique), `source` | none; first confirmed BG creates it |
| `bg_type` | `code` (PK), `sort_order` | `PBG` (1), `ABG` (2) |
| `nature_of_service` | `id`, `name` (unique) | 27 master values |
| `bg_status` | `code` (PK), `sort_order` | `Draft` 1, `Active` 2, `Expiring` 3, `Expired` 4, `Extended` 5, `Closed` 6 |
| `roles` | `id`, `name` (unique) | `admin`, `uploader`, `viewer` |

`source` records provenance: seeded rows are `NULL`, rows the master importer
adds are `'imported'`, values a reviewer picks by hand are `'manual'`, and the
legacy SQLite import tagged its additions `'migrated'`. `is_active` hides a
master value from the dropdowns without deleting history.

Master data is seeded from `docs/master-data.md` — 3 companies, 28 projects,
46 plazas, 27 natures of service — through `db/bootstrap.py` (the one-shot
`migrate` service) or `python -m bgauto.cli import-masters`, so the seed is
never duplicated. `V2__seed.sql` adds the lookup values (`bg_type`, `bg_status`,
`roles`, natures) and the corridor/plaza baseline. `bank` and `contractor`
start empty and gain a row only when a confirmed BG names one.

### 4.2 Users, roles and sessions

| Table | Columns and rules |
|---|---|
| `users` | `id` UUID PK, `username` CITEXT unique, `email` CITEXT unique, `full_name`, `password_hash`, `is_active` (default true), `last_login`, `created_at`, `updated_at`, `failed_attempts` (default 0), `locked_until` |
| `user_roles` | `user_id` → `users`, `role_id` → `roles`; PK `(user_id, role_id)`; cascade on user delete |
| `sessions` | `token_hash` PK, `user_id` → `users` (cascade), `created_at`, `expires_at`, `last_seen`, `ip` TEXT, `user_agent`; indexed on user and expiry |
| `lookup_cache` | `field`, `query`, `payload` JSONB, `created_at`; PK `(field, query)` |

Sessions persist across app container recreation because they live in the
database, not in process memory. Lockout is per user: five consecutive failures
(`MAX_FAILED_ATTEMPTS`) set `locked_until`; a success clears both counters.

### 4.3 LOA / PO parent

`loa_records`: `id` UUID PK, `loa_po_number` (unique), `description`,
`created_by` → `users` (`ON DELETE SET NULL`), `created_at`, `updated_at`.

One LOA has many BGs (1:M). An extension of the same LOA inserts a new
`bg_records` row and points it at the old one through `supersedes_bg_id`.

### 4.4 BG register (`bg_records`)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `bg_no` | TEXT NOT NULL UNIQUE | Dedupe key (UC003); Excel "Sr" is not stored |
| `company_id` | SMALLINT → `company` | |
| `project_id` | SMALLINT → `project` | |
| `toll_plaza_id` | INTEGER → `toll_plaza` | |
| `bg_type` | TEXT → `bg_type` | PBG / ABG |
| `nature_id` | SMALLINT → `nature_of_service` | |
| `contractor_id` | INTEGER → `contractor` | |
| `bank_id` | SMALLINT → `bank` | |
| `loa_id` | UUID → `loa_records` | |
| `po_number` | TEXT | Optional |
| `bg_amount` | NUMERIC(15,2) NOT NULL | `CHECK (bg_amount > 0)` |
| `from_date`, `to_date` | DATE NOT NULL | `CHECK (to_date >= from_date)` |
| `claim_period` | DATE | `CHECK (claim_period IS NULL OR claim_period >= to_date)` |
| `extended_date` | DATE | Excel "Extended": a date, or `Yes` migrated to `status='Extended'` with a NULL date |
| `status` | TEXT → `bg_status`, default `Draft` | Lifecycle, replaces Excel free text |
| `remarks` | TEXT | |
| `source_pdf` | TEXT NOT NULL | Original filename or stored path |
| `created_by` | UUID → `users` | `ON DELETE SET NULL` |
| `created_at`, `updated_at` | TIMESTAMPTZ | Trigger-maintained |
| `reminder_sent_at` | TIMESTAMPTZ | Set when the T-7 mail goes out |
| `supersedes_bg_id` | UUID → `bg_records` | Extension chain (ST007); `CHECK (id <> supersedes_bg_id)` |

Indexes: partial `idx_bg_to_date` on `to_date` where status is not
`Closed`/`Extended` (T-7 hot path), plus `status`, `contractor_id`,
`project_id`, `bank_id`, `loa_id` and `supersedes_bg_id`.

Dates and the status flag. A row is `from_date` + `to_date`, plus optional
`claim_period` and `extended_date`; `bg_status` carries the lifecycle. The six
statuses, in `sort_order` order: `Draft`, `Active`, `Expiring`, `Expired`,
`Extended`, `Closed`. `Draft` is the default until Confirm; moving `To`
re-derives `Active`/`Expired` (`to_date` before today is `Expired`); an
extension sets the new row's `supersedes_bg_id`, stamps `extended_date = today`
on the old row and closes it. `extended_date` is the date form of Excel
"Extended" — the register shows `Extended = Yes` whenever it is set, and a
legacy `Yes` with no date migrated to `status = 'Extended'` with a NULL date.

### 4.5 OCR attempts (`ocr_results`)

`id` UUID PK, `bg_id` → `bg_records` (nullable: failed extractions precede a
confirmed BG), `source_pdf`, `di_model` (Datalab mode, e.g. `fast`/`accurate`),
`raw_json` JSONB (full page tree for audit/retrain), `extracted_fields` JSONB
(default `{}`), `avg_confidence` NUMERIC(5,4) in 0–1, `status`
(`queued`/`success`/`needs_review`/`failed`), `error`, `created_by`,
`created_at`. GIN index on `extracted_fields`; indexes on `bg_id` and `status`.

Low-confidence rows stay `needs_review`/`failed` and are never auto-written to
the register (ST008). The current pipeline archives the raw JSON to
`out/web/<run>/` instead of inserting here; the table is kept for V1
compatibility and verification.

### 4.6 Reminders (`reminders`)

`id` UUID PK, `bg_id` → `bg_records` (cascade), `scheduled_for` DATE
(`to_date - 7 days`), `sent_at`, `recipients` TEXT[], `subject`, `body`,
`provider_msg_id`, `status` (`pending`/`sent`/`failed`/`skipped`), `error`,
`created_at`. `UNIQUE (bg_id, scheduled_for)` prevents a double send.

### 4.7 Audit (`audit_logs`)

`id` BIGINT IDENTITY PK, `ts` TIMESTAMPTZ, `user_id` → `users` (set null),
`action`, `bg_id` → `bg_records` (set null), `bg_no`, `old_data` JSONB,
`new_data` JSONB, `ip` TEXT (TEXT because tests and proxies send
`testclient`, not an address).

Allowed `action` values (V3 CHECK): `upload`, `extract`, `confirm`, `export`,
`remind`, `extend`, `close`, `login`, `login_failed`, `logout`, `locked`,
`submit`, `review_save`, `user_create`, `user_update`, `password_change`,
`role_change`, `reminder_sent`, `reminder_resend`, `workbook_reset`.

### 4.8 Register view (`v_bg_register`)

A read-only view over `bg_records` with its foreign keys resolved, shaped like
the Excel row: `bg_no`, `bg_amount`, `From`, `To`, `Claim Period`, `Extended`,
`Status`, `Remarks`, `source_pdf`, `created_at`, `reminder_sent_at`,
`Company Name`, `Project Name`, `Toll Plaza Name`, `Types of BG`,
`Nature of Service`, `Name of Contractor`, `Bank Name`, `LOA Number`,
`PO Number` and `created_by_name`. It is kept for V1 compatibility and
verification; the app reads `bg_records` with its joins directly and nothing
writes to the view.

```sql
-- shape only; the full definition is db/migrations/V1__schema.sql
CREATE VIEW v_bg_register AS
SELECT b.bg_no, b.bg_amount, b.from_date AS "From", b.to_date AS "To",
       c.name AS "Company Name", p.code AS "Project Name", ...
FROM bg_records b
LEFT JOIN company c ON c.id = b.company_id
LEFT JOIN project p ON p.id = b.project_id
/* ... one LEFT JOIN per master, plus users for created_by_name */;
```

## 5. Validation and business rules

1. **Dedupe.** `BG No` is unique after trimming spaces and ignoring case.
   Confirm returns HTTP 409 with a link to the existing row on duplicates.
2. **Mandatory for Confirm.** Company, Project, Toll Plaza, Types, Nature,
   Contractor, BG No, Amount, From, To, Bank, Status. Claim Period and LOA/PO
   may stay blank.
3. **Dates.** Business dates are `DATE` columns, exported as `DD-MM-YYYY`.
   Instants (`created_at`, `ts`, `sent_at`) are `TIMESTAMPTZ` in UTC and shown
   in IST.
4. **Extension.** A new BG under the same LOA creates a new `bg_records` row
   and sets its `supersedes_bg_id` to the old row. The old row gets
   `extended_date = today`, `status = 'Closed'`; the register shows
   `Extended = Yes` whenever `extended_date` is set.
5. **Single writer.** Only the API appends to Excel, one row at a time behind
   a lock. The Postgres rows are the source of truth until Confirm.
6. **Dedupe is enforced twice.** `bg_no` is `UNIQUE` in Postgres and the store
   catches `UniqueViolation`, so two concurrent writers cannot both insert.

## 6. Acceptance criteria

- [ ] Review form blocks Confirm when any mandatory field is empty.
- [ ] Duplicate BG No is rejected with a clear error and a link to the row.
- [ ] Column order and headers match this document exactly.

## References

- Tracker file: `../BG Tracker.xlsx`
- Schema: `../db/migrations/V1__schema.sql`, `../db/migrations/V2__seed.sql`, `../db/migrations/V3__schema.sql`
- Master data: `../docs/master-data.md`
- Migration record: `07-postgres-migration.md`
- OCR rules: `04-ocr-extraction.md`
- Flows: `05-flows.md`
