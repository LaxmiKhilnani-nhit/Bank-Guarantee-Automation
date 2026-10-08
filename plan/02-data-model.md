---
id: PLAN-02
title: Data Model
status: active
owner: Laxmi
last_updated: 2026-10-08
version: 3
inputs: ["BG Tracker.xlsx (Sheet1)"]
outputs: ["Tracker validation rules", "SQLite schema"]
related: ["PLAN-01", "PLAN-04", "PLAN-05"]
---

# 02 — Data Model

## 1. Purpose

This document defines exactly what data we store, in which column, and what
counts as valid. Read it before building the review form, the Excel writer,
or any validation logic.

## 2. Scope

- In scope: the 20 Excel columns, the SQLite tables, dedupe and validation rules.
- Out of scope: OCR regex details (see `04`) and mail templates (see `06`).

## 3. Excel tracker — `BG Tracker.xlsx`, Sheet1

This sheet is the format of record. Column order must not change.

| # | Column | Type | Required | How it is filled |
|---|--------|------|----------|------------------|
| 1 | Company Name | Dropdown | Yes | Reviewer picks from master list |
| 2 | Project Name | Dropdown | Yes | Reviewer picks from master list |
| 3 | Toll Plaza Name | Dropdown | Yes | Depends on Project |
| 4 | Types of BG | Dropdown | Yes | For example Performance, Advance, Retention |
| 5 | Nature of Service | Dropdown | Yes | Reviewer picks from master list |
| 6 | Name of Contractor | Text | Yes | OCR-assisted (applicant block), editable |
| 7 | BG No | Text, **unique** | Yes | OCR; dedupe key |
| 8 | BG Amount | Number (INR) | Yes | OCR (`Rs`/`INR` + commas stripped), stored numeric |
| 9 | From | Date `DD-MM-YYYY` | Yes | OCR (issue or effective date) |
| 10 | To | Date `DD-MM-YYYY` | Yes | OCR (expiry or valid-till). **This is the T-7 anchor.** |
| 11 | Claim Period | Date or text | No | OCR; may be blank (ST008) |
| 12 | Extended | Yes/No | Yes, default No | Old row becomes Yes on extension |
| 13 | Bank Name | Text | Yes | OCR + normalised (SBI, HDFC, ICICI, Axis, PNB…) |
| 14 | Status | Dropdown | Yes | Active, Expired, Closed, Extended |
| 15 | Remarks | Text | No | Free text |
| 16 | LOA/PO Number | Text | No | OCR + regex (ST004); blank if unsure |
| 17 | source_pdf | Text (system) | Yes | Original filename or stored path |
| 18 | created_by | Text (system) | Yes | Username of the confirming admin |
| 19 | created_at | Date-time (system) | Yes | Confirm timestamp |
| 20 | reminder_sent_at | Date-time (system) | No | Set when the T-7 mail is sent; empty until then |

Rule: if OCR confidence is low, leave the field blank. Never guess (ST008).

## 4. SQLite tables

SQLite tracks jobs, users, and history. Excel is the business record; SQLite
is the queue and audit trail.

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT CHECK (role IN ('admin','uploader','viewer')) NOT NULL,
  is_active INTEGER DEFAULT 1,
  failed_attempts INTEGER DEFAULT 0,
  locked_until DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  owner TEXT NOT NULL,
  source_pdf TEXT NOT NULL,
  status TEXT CHECK (status IN
    ('queued','extracting','pending_review','pending_admin','confirmed','failed')) NOT NULL,
  extract_json TEXT,
  fields_json TEXT,
  error TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  ts DATETIME DEFAULT CURRENT_TIMESTAMP,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  bg_no TEXT,
  old_json TEXT,
  new_json TEXT
);

CREATE TABLE reminder_log (
  id INTEGER PRIMARY KEY,
  bg_no TEXT NOT NULL,
  sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  recipients TEXT NOT NULL,
  subject TEXT NOT NULL,
  status TEXT NOT NULL
);
```

## 5. Validation and business rules

1. **Dedupe.** `BG No` is unique after trimming spaces and ignoring case.
   Confirm returns HTTP 409 with a link to the existing row on duplicates.
2. **Mandatory for Confirm.** Company, Project, Toll Plaza, Types, Nature,
   Contractor, BG No, Amount, From, To, Bank, Status. Claim Period and LOA/PO
   may stay blank.
3. **Dates.** Stored as ISO in the DB, displayed as `DD-MM-YYYY`.
4. **Extension.** A new BG under the same LOA creates a new row. The old row
   is set to `Extended = Yes`, `Status = Closed`.
5. **Single writer.** Only the API appends to Excel, one row at a time behind
   a lock. The queue in SQLite is the source of truth until Confirm.
6. **Delete safety.** Pending jobs may be deleted (admin: any; uploader: own
   only). Confirmed Excel rows are never hard-deleted — admin voids with
   `Status = Closed` plus a reason in `Remarks` and an audit entry. Users are
   deactivated by default; hard delete only when the user has no history.
   `reminder_log` is append-only.

## 6. Acceptance criteria

- [ ] Review form blocks Confirm when any mandatory field is empty.
- [ ] Duplicate BG No is rejected with a clear error and a link to the row.
- [ ] Column order and headers match this document exactly.

## References

- Tracker file: `../BG Tracker.xlsx`
- OCR rules: `04-ocr-extraction.md`
- Flows: `05-flows.md`
