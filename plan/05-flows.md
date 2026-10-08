---
id: PLAN-05
title: Process Flows
status: active
owner: Laxmi
last_updated: 2026-10-08
version: 3
inputs: ["PLAN-02", "PLAN-03", "PLAN-04"]
outputs: ["Upload flow", "Extend/close flow", "Reminder flow", "Failure flow"]
related: ["PLAN-01", "PLAN-06"]
---

# 05 — Process Flows

## 1. Purpose

This document shows step by step what happens in each scenario. If you are
unsure how roles, OCR, Excel, and mail fit together, start here.

## 2. Flow A — Upload to Confirm (maker-checker)

The uploader proposes. The admin approves. Nothing reaches Excel without an
admin Confirm.

```mermaid
sequenceDiagram
  participant U as Uploader
  participant A as Admin
  participant API as FastAPI
  participant DI as Azure DI
  participant X as BG Tracker.xlsx
  U->>API: Upload PDF and request extraction
  API->>DI: Analyse with prebuilt-layout
  DI-->>API: Text, tables, confidence
  API->>API: Apply field rules, save job as pending_review (owner = U)
  U->>API: Fix fields, pick dropdowns, Submit for approval
  API->>API: Job becomes pending_admin
  A->>API: Open review queue, check fields, Confirm
  API->>API: Validate mandatory fields, dedupe BG No
  alt BG No already exists
    API-->>A: Error 409 with link to existing row
  else valid
    API->>X: Append row (created_by = A)
    API->>API: Write audit entry
  end
```

Accept: uploader cannot Confirm (403); duplicate BG No never creates a second row.

## 3. Flow B — Extend or Close (same LOA)

```mermaid
flowchart TD
  R["T-7 mail asks: extend or close?"] --> D{"User Team decision"}
  D -->|"Extend"| N["New BG arrives under same LOA - upload and Confirm as a new row"]
  N --> O["Old row set to Extended = Yes, Status = Closed"]
  D -->|"Close"| C["Admin sets Status = Closed when the BG expires"]
```

Accept: the old row is never deleted; history is preserved.

## 4. Flow C — Daily T-7 reminder (09:00 IST)

```mermaid
flowchart TD
  T["Task Scheduler runs at 09:00"] --> Q["Find rows where To minus 7 days is today, reminder not sent, Status is not Closed"]
  Q -->|"rows found"| S["Send one mail per BG via SMTP, retry up to 3 times"]
  S -->|"sent"| U["Set reminder_sent_at, add reminder_log entry"]
  S -->|"all retries fail"| DL["Send to manual review queue"]
  Q -->|"no rows"| E["Log a no-op run"]
```

Accept: no BG gets two T-7 mails; every send is logged with recipients and subject.

## 5. Flow D — Failure and manual queue (ST008)

```mermaid
flowchart TD
  X["DI error, unreadable scan, or missing mandatory fields"] --> F["Job marked failed, error logged"]
  F --> M["Job appears in manual review queue"]
  M -->|"fixable"| R["Uploader or admin types the fields by hand, then submits"]
  M -->|"not fixable"| B["Leave blank - never write a bad row to Excel"]
```

Accept: failed jobs never reach Excel; blanks are explicit, not guessed.

## References

- Architecture: `01-architecture.md`
- Auth: `03-auth.md`
- Email detail: `06-email-scheduler-hosting.md`
