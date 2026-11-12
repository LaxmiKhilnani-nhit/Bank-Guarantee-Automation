---
id: PLAN-MASTER
title: BG Automation — Build Plan
status: active
owner: Laxmi
last_updated: 2026-10-08
version: 3
inputs: ["BG Tracker.xlsx", "BG_UseCase_Story_Tracking.xlsx", "Sample_Bank_Guarantees/", "BG_OCR_Options_Comparison.md"]
outputs: ["plan/00-log.md", "plan/01-architecture.md", "plan/02-data-model.md", "plan/03-auth.md", "plan/04-ocr-extraction.md", "plan/05-flows.md", "plan/06-email-scheduler-hosting.md"]
---

# BG Automation — Build Plan

## Purpose

This is the master summary for BG Automation. It states what we are building,
why, and in what order. Detail lives in the `plan/` folder. This file should
stay short; if a section grows, move it to `plan/` and link it.

**Workflow:** upload a BG PDF on the dashboard → extract fields with
enterprise OCR → human review → append to the Excel tracker → send a T-7
expiry mail via SMTP.

**Constraints:** enterprise tools only (no open-source OCR or LLM), SMTP only
(no Graph yet), local login (no Entra SSO yet), India-only data, Windows
cloud VM available, Docker optional.

## 1. Inputs in this workspace

- `BG Tracker.xlsx` — Sheet1, extended to 21 columns with separate LOA and PO fields.
- `BG_UseCase_Story_Tracking.xlsx` — UC001–UC007, ST001–ST013.
- `Sample_Bank_Guarantees/` — two sample PDFs for training and testing.
- `BG_OCR_Options_Comparison.md` — recommends Azure DI in Central India.

## 2. Locked requirements

| # | Item | Decision |
|---|------|----------|
| R1 | Volume | 20–25 PDFs/month, 6–10 pages each, about 200 pages/month, scanned printed English |
| R2 | Fields | 21-column tracker (see §3). LOA Number and PO Number are separate fields |
| R3 | Excel | Append to local `BG Tracker.xlsx` (Sheet1), dedupe on BG No. SharePoint live sync waits for Graph approval |
| R4 | Email | SMTP via `smtp.office365.com:587`. T-7 calendar days on the `To` date to Finance + User Team. Extend-or-close loop per ST006–ST007 |
| R5 | Auth | Local users, 3 roles. Uploaders save their own reviewed rows; admins manage users; viewers read (see `plan/03-auth.md`) |
| R6 | OCR | Datalab API (`fast` by default). Every uploaded PDF is sent to Datalab for OCR; the register and archive stay local |
| R7 | Failures | Failed OCR or missing fields go to a manual queue and stay blank (ST008). Bad rows never reach Excel |
| R8 | Hosting | Existing Windows VM first. Environment-driven config for a later App Service move |

Still needed before build: SMTP mailbox, Finance and User Team DLs, `To` as
T-7 anchor (not Claim Period), sender name and template sign-off.

## 3. Data model (summary)

Tracker columns in order:

`Company Name | Project Name | Toll Plaza Name | Types of BG | Nature of Service | Name of Contractor | BG No (unique) | BG Amount | From | To (T-7 anchor) | Claim Period | Extended | Bank Name | Status | Remarks | LOA Number | PO Number | source_pdf | created_by | created_at | reminder_sent_at`

Extract first: BG No, Bank Name, Amount, From, To, Claim Period, LOA Number,
Contractor, Beneficiary. The rest come from review-form dropdowns.
Full spec: `plan/02-data-model.md`.

## 4. Architecture (summary)

- Frontend: React (Vite) dashboard.
- Backend: FastAPI on port 8000 → Datalab OCR, PostgreSQL 16
  (compose `db` service: runs, users, register, audit) + `BG Tracker.xlsx`,
  SMTP mailer + daily 09:00 job.
- VM: IIS reverse proxy + Windows Service (API) + Task Scheduler (T-7 check).

Pages (role-gated): Login; Upload and Review (uploader edits and saves own
rows); Register (admin/viewer); Workbook (all roles, read-only); Reminders;
Users/Admin (admin only).

Phase 1 has no Graph, SharePoint, or Entra. Interfaces (`MailSender`,
`TrackerStore`, `Extractor`, `AuthProvider`) keep those swaps easy.
Diagrams: `plan/01-architecture.md`. Flows: `plan/05-flows.md`.

## 5. Extraction strategy (summary)

1. Run each PDF through the pinned Datalab pipeline (`convert` then structured
   `extract`; `turbo` mode).
2. Keep uncertain values editable for review; blank beats wrong. Local code only
   normalises Datalab's values and resolves masters.
3. Store both raw pipeline results and the extraction report locally for audit.
Detail: `plan/04-ocr-extraction.md`.

## 6. Email and scheduler (summary)

- SMTP port 587, STARTTLS, service mailbox from env/Key Vault, sender `BG Automation`.
- Daily: rows where `To − 7 days = today`, `reminder_sent_at` empty, `Status ≠ Closed`.
- Subject: `BG {BG No} expiring {To} — {Project}/{Contractor} — extend or close?`
- On send: set `reminder_sent_at`, write `reminder_log`. Retry 3 times, then dead-letter.
- Extension: new BG under the same LOA is a new row; old row becomes `Extended = Yes`, `Status = Closed`.

Detail: `plan/06-email-scheduler-hosting.md`.

## 7. Auth and audit (summary)

| Action | Admin | Uploader | Viewer |
|--------|-------|----------|--------|
| Upload | Yes | Yes (own) | No |
| Edit own draft | Yes | Yes | No |
| Save reviewed rows | Yes | Yes (own runs) | No |
| View workbook in frontend | Yes | Yes | Yes |
| Register + export | Yes | Own queue | Yes (all + export) |
| Users + resend | Yes | No | No |
| Create, edit, activate/deactivate, reset users | Yes (admin only) | No | No |
| Delete user | Yes (deactivate by default; hard delete only if user never acted) | No | No |
| Delete pending job | Yes (any unconfirmed job) | Yes (own unconfirmed jobs) | No |
| Delete confirmed Excel row | Never hard-deleted (void via `Status = Closed` + audit) | No | No |

`bcrypt` passwords, 30-minute JWT in `httpOnly + Secure + SameSite=Lax`
cookie, 5-fail lockout, full `audit_log` on every change.
Detail: `plan/03-auth.md`. Stories: ST009 + ST011–ST013.

## 8. Phases

- **Sprint 1 (UC001–UC004, UC006):** Local OCR setup, upload + review + Excel
  save (21-column format), LOA/PO extraction, 3-role login.
- **Sprint 2 (UC005, UC007):** SMTP T-7 job, templates, extend/close handling,
  failed-OCR queue, register filters + export, VM scheduler.
- **Hardening:** daily backups (xlsx + DB + PDFs), log rotation, runbook,
  1-month parallel UAT with accuracy report.

## 9. Test plan

- 20+ samples including `Sample_Bank_Guarantees/`; per-field precision on
  BG No, Amount, To, Claim, LOA.
- Role checks: uploader saves own run, viewer upload → 403, uploader sees own queue only.
- Scheduler dry run (`To = today + 7`); SMTP sandbox before production DLs.

## 10. Risks

- Skewed scans and stamps → mandatory human review; never lower the confidence threshold.
- SMTP AUTH deprecation → request Entra app registration early; keep Graph path open.
- Excel locks and direct edits → single-writer API, service-account ownership.

## Approvals needed

1. SMTP mailbox + DLs + template sign-off.
2. Datalab plan/quota + cost-alert owner.
3. Confirm `To` = T-7 anchor.

## References

- Detail docs: `plan/README.md` (index), `plan/00-log.md` (history).
- Stories: `BG_UseCase_Story_Tracking.xlsx` (ST011–ST013 assigned to Laxmi).
