---
id: PLAN-LOG
title: Decision and Change Log
status: active
owner: Laxmi
last_updated: 2026-10-08
version: 3
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

### 2026-10-08 — Laxmi — Admin user management + delete access

- What: confirmed admin owns user management (create, role change,
  activate/deactivate, password reset, unlock) and delete. Delete is scoped:
  admin can delete any unconfirmed job; uploader can delete own unconfirmed
  jobs; confirmed Excel rows are never hard-deleted (void via Status Closed +
  audit); users deactivate by default, hard delete only with zero history.
- Why: admin needs full control, but history (tracker rows, audit, reminders)
  must survive deletes.
- Files: `plan/03-auth.md`, `plan/02-data-model.md`, `BG_Automation_Plan.md`
  §7, `BG_UseCase_Story_Tracking.xlsx` (ST014).

### 2026-10-08 — Laxmi + team — Documentation rewrite (v3)

- What: rewrote all `plan/` files and the master plan in a standard format
  (frontmatter, purpose, scope, acceptance criteria, references). Fixed grammar,
  headings, and inconsistent terms.
- Why: previous drafts were hard to follow. New format is readable by any
  engineer or AI agent without extra explanation.
- Files: `BG_Automation_Plan.md`, `plan/README.md`, `plan/01`–`06`, `plan/00-log.md` (this file).

### 2026-10-08 — Laxmi — Auth stories added (ST011–ST013)

- What: added three user stories under UC006, assigned to Laxmi, Sprint 1.
  ST011 = admin confirm-only. ST012 = uploader drafts, no confirm.
  ST013 = viewer read and export only.
- Why: the three-role maker-checker model needed tracking in the story sheet.
- Files: `BG_UseCase_Story_Tracking.xlsx` (User Stories sheet, rows 12–14).

### 2026-10-08 — Laxmi + team — Auth model locked (maker-checker)

- What: replaced "uploader can approve own work" with strict maker-checker.
  Admin confirms to Excel. Uploader edits own drafts and submits for approval.
  Viewer reads and exports only.
- Why: separation of duties. Prevents one person from uploading and approving
  their own entry.
- Files: `BG_Automation_Plan.md` §7, `plan/03-auth.md`.

### 2026-10-08 — Laxmi — Plan detail folder created

- What: created `plan/` with six detail files (architecture, data model, auth,
  OCR, flows, email/hosting) with Mermaid diagrams.
- Why: the master plan was too dense. Detail files let builders focus on one
  topic at a time.
- Files: `plan/01-architecture.md` … `plan/06-email-scheduler-hosting.md`.

### 2026-10-08 — Laxmi — Tracker format locked (20 columns)

- What: confirmed `BG Tracker.xlsx` (Sheet1) as the format of record.
  Added 5 columns: LOA/PO Number, source_pdf, created_by, created_at,
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
