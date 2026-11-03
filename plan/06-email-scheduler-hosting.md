---
id: PLAN-06
title: Email, Scheduler, Hosting and Operations
status: active
last_updated: 2026-10-08
version: 3
inputs: ["BG_Automation_Plan.md (R4, R8)"]
outputs: ["T-7 mails", "Daily scheduler", "VM deployment", "Backups"]
related: ["PLAN-01", "PLAN-02", "PLAN-05"]
---

# 06 — Email, Scheduler, Hosting and Operations

## 1. Purpose

This document covers everything after a row is confirmed: how expiry
reminders are sent, where the app runs, and how data is protected. Read it
before configuring mail, the scheduler, or the VM.

## 2. Email (SMTP only in Phase 1)

We use SMTP, not Microsoft Graph, for the pilot. A Graph migration path stays
open behind the `MailSender` interface.

- Host: `smtp.office365.com`, port `587`, STARTTLS.
- Sender: service mailbox from environment variables; display name `BG Automation`.
- Recipients: `FINANCE_DL` and `USER_TEAM_DL` from environment variables.
  Use a sandbox distribution list for testing before production.
- Reliability: retry up to 3 times with backoff. After 3 failures, send the
  job to the manual review queue and log `status = failed`.

## 3. Message template (needs sign-off)

- Subject: `BG {BG No} expiring {To} — {Project}/{Contractor} — extend or close?`
- Body includes: Company, Project, Toll Plaza, BG No, Amount, Bank, From, To,
  Claim Period, LOA Number, PO Number, Remarks, a link to the Register row, and the
  instruction to reply `EXTEND` or `CLOSE`.
- One mail per BG (no digest in the pilot) so each decision stays traceable
  (ST006–ST007).

## 4. Daily scheduler (09:00 IST)

Once a day the scheduler finds BGs expiring in exactly 7 calendar days that
have not been reminded and are not closed.

```sql
SELECT *
FROM tracker_mirror
WHERE date("To", '-7 days') = date('now', 'localtime')
  AND reminder_sent_at IS NULL
  AND Status <> 'Closed';
```

On success: set `reminder_sent_at` and insert a `reminder_log` row (BG No,
recipients, subject, timestamp). On empty result: log a no-op run.

Trigger: Windows Task Scheduler at 09:00 IST calls
`POST /internal/daily-reminder` with a shared-secret header. An in-process
scheduler is an acceptable alternative for the pilot.

## 5. Hosting on the Windows VM

- IIS ends HTTPS and serves the static React build. It forwards `/api/*` to
  `127.0.0.1:8000`, where the FastAPI app runs as a Windows Service
  (uvicorn).
- Data folder: `C:/bg-auto/data/` holds `BG Tracker.xlsx`, `app.db`, `pdfs/`,
  `di-json/`, and `backups/`. The service account owns it.
- Configuration is 100% environment-driven, so a later move to App Service
  needs no code change.

## 6. Backups and runbook

- Daily backup of the Excel file, the database, and PDFs with 7-day rotation.
- Log rotation for API and scheduler logs.
- Runbook covers: restore from backup, re-run the scheduler for a date,
  resend a reminder, unlock a user, and rotate the SMTP password.
- UAT: one month of parallel manual entry with an accuracy report, a
  scheduler dry run (`To = today + 7`), and a sandbox mail test.

## 7. Risks

- SMTP AUTH may be deprecated → request an Entra app registration early.
- Excel file locks → single-writer API plus service-account ownership; users
  never open the file.

## 8. Acceptance criteria

- [ ] Dry run with `To = today + 7` sends exactly one mail per BG.
- [ ] No duplicate T-7 mail for the same BG.
- [ ] Restore from backup reproduces the tracker and the database.
- [ ] Sandbox mail test passes before production DLs are used.

## References

- Architecture: `01-architecture.md`
- Flows: `05-flows.md`
