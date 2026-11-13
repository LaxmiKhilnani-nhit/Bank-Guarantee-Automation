---
id: PLAN-06
title: Email, Scheduler, Hosting and Operations
status: active
last_updated: 2026-11-13
version: 4
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
have not been reminded and are still open.

- The scheduler is an in-process daemon thread inside the `app` container
  (`ReminderScheduler`), started with the API's lifespan. It wakes at 09:00
  IST, runs once per day, and writes `data/scheduler-last-run.txt` so a restart
  can tell whether today's run already happened.
- If the app starts after 09:00 and today's marker is missing, it catches up
  immediately; otherwise it waits for the next 09:00 IST.
- The time is configurable with `BG_SCHEDULER_TIME` (default `09:00`); the
  thread runs in the app process, so there is no separate job to schedule.
- Selection semantics (`to_date` is the T-7 anchor):
  `bg_records.to_date = today + 7`, `reminder_sent_at IS NULL`, and
  `status <> 'Closed'`.
- On send: write a `reminders` row (`bg_id`, `scheduled_for`, `recipients`,
  `status`, and `sent_at` once the mail goes out) and set
  `bg_records.reminder_sent_at`. `UNIQUE (bg_id, scheduled_for)` on the table
  is what stops a BG from getting the same T-7 mail twice, so a re-run cannot
  double-send.
- On empty result: log a no-op run.
- Manual re-run for ops (missed day, changed DLs, dry run):
  `POST /internal/daily-reminder` with `X-Internal-Token: $BG_INTERNAL_TOKEN`.
  Admins can also resend a single BG from the register. Neither is the default
  scheduler — the in-process thread is.

## 5. Hosting on the Windows VM

Production is Docker Compose on the Windows VM. Requirements:

- Docker Desktop with the WSL 2 backend (or Docker Engine plus Compose v2)
  installed on the VM, with the project checked out in a fixed folder.
- `docker compose build` once, then `docker compose up -d` starts all three
  services: `db` (PostgreSQL 16 with the `bg_pgdata` named volume), the
  one-shot `migrate` (applies the schema migrations, then seeds the masters),
  and `app` (FastAPI serving the API and the static HTML pages).
- The app publishes only on the host loopback:
  `127.0.0.1:${BG_WEB_PORT:-8000}:8000` (override with `BG_WEB_PORT`). No
  database port is published; `app` reaches `db` over the compose network.
- TLS is terminated in front of that published port by the environment's
  reverse proxy / load balancer. This stack does not terminate TLS itself and
  does not name a proxy product — whatever the environment already fronts the
  VM with is expected to forward to `http://127.0.0.1:8000`.
- IIS is not used, and neither is a Windows Service wrapper: the app runs as a
  container, so all lifecycle commands are compose commands.
- Inspect the stack with `docker compose ps`, and follow the app log with
  `docker compose logs -f app`.
- Changing `.env` needs a recreate, not a restart: run `docker compose up -d
  app`, because `docker compose restart app` keeps the old environment.
- Data folders: `out/` holds the review workbook, the run archive and the mail
  dry-run `.eml` files; backup archives go to `data/backups/`, next to the OCR
  and PDF archive under `data/pdfs/`. The register itself lives in the
  `bg_pgdata` volume, not in a file.
- Configuration is 100% environment-driven, so a later move to another host
  needs no code change.

## 6. Backups and runbook

- Take a backup with `python -m bgauto.cli backup`. It writes one zip
  (`bg-auto-backup-<timestamp>.zip`) to `data/backups/` containing a `pg_dump`
  custom-format dump of the register and audit trail, the review workbook, the
  tracker template and the archived PDFs, and keeps the newest 7 archives.
- Restore is manual: stop the app, unzip the archive, then restore the register
  with `pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL"
  database.dump` and copy `out/BG Tracker_ingested.xlsx` and `data/pdfs/` back
  into place. Start the app and check `/api/health`, the Register screen and one
  reminder dry run.
- `bg_pgdata` is a named volume, so it also survives `docker compose down`;
  a volume deletion, however, is unrecoverable without a dump.
- Log rotation for the container logs is an operational task on the VM.
- Runbook covers: restore from backup, re-run the scheduler
  (`POST /internal/daily-reminder`), resend a reminder, unlock a user, and
  rotate the SMTP password.
- UAT: one month of parallel manual entry with an accuracy report, a
  scheduler dry run (`To = today + 7`), and a sandbox mail test.
- Off-host copies of the backups are an operational task for the VM owner —
  nothing in this stack uploads them anywhere yet.

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
