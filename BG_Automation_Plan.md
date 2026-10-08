# BG Automation — Build Plan

**Date:** 2026-10-07
**Workflow:** Upload BG PDF on dashboard → Enterprise OCR + field extraction → Human review → Append to Excel tracker → T-7 expiry email via SMTP
**Constraints:** Enterprise-only (no OSS OCR/LLM), SMTP-only (no Graph for now), local login (no Entra SSO for now), India-only data, Windows cloud VM available, Docker possible

## 1. Source Inputs Found in Workspace

- `BG Details-Updated.xlsx` — sheet `BG from Vendor`, tracker format of record
- `BG_UseCase_Story_Tracking(User Stories).csv` — UC001-UC005, ST001-ST008
- `Sample_Bank_Guarantees/` — `BG 264GT02252580004.pdf`, `BG Document.pdf` for training/test
- `BG_OCR_Options_Comparison.md` — Azure DI Central India recommended

## 2. Requirements Locked

| # | Item | Decision |
|---|------|----------|
| R1 | Volume | 20-25 PDFs/mo, 6-10 pages each, ~200 pages/mo, scanned printed English |
| R2 | Fields | See §3. Includes LOA/PO Number per ST004 |
| R3 | Excel | Local `BG Details-Updated.xlsx` format append, dedupe on BG No. Live SharePoint deferred until Graph approved |
| R4 | Email | SMTP-only via `smtp.office365.com:587` service mailbox. T-7 calendar days on `To` date to Finance + User Team. Extend/close decision loop per ST006-ST007 |
| R5 | Auth | Local DB users + roles (admin/uploader/viewer). Uploader can approve own for now, audit log for future maker-checker |
| R6 | OCR | Azure Document Intelligence in Central India. Layout baseline → Custom extraction if needed. No Tesseract/Paddle/Ollama |
| R7 | Failure handling | Failed OCR / missing mandatory fields → manual review queue, leave blank per ST008, never auto-write bad rows |
| R8 | Hosting | Existing Windows cloud VM first. Code env-driven for later App Service move |

Open to confirm before build: SMTP mailbox + Finance DL + User Team DL, `To` as T-7 anchor (not Claim Period), sender name/template.

## 3. Data Model — Tracker Columns

From `BG Details-Updated.xlsx`:

`Sr (auto) | Project Name | Types of BG | Nature of Service | Name of Contractor | BG No (unique) | BG Amount | From (issue/effective) | To (expiry/valid-till, T-7 anchor) | Claim Period | Extended | Bank Name | Status | Remarks | LOA/PO Number | source_pdf | created_by | created_at | reminder_sent_at`

Extraction priority: `BG No, Bank Name, Amount, From, To, Claim Period, LOA Number, Applicant/Contractor, Beneficiary` → rest via review form defaults + dropdowns (Project, Types, Nature, Status, Extended).

## 4. Architecture

```
React (Vite) dashboard ──HTTPS──> FastAPI :8000 ──> Azure DI Central India (SDK/REST)
                          │                ├─> SQLite (jobs/users/audit) + BG_Master.xlsx (same columns as tracker)
                          │                └─> SMTP mailer + daily scheduler (09:00)
VM: IIS reverse proxy + Windows Service (API) + Task Scheduler (daily T-7 check)
```

Pages: Login, Upload (drag-drop multi-PDF, queue), Review (OCR text + editable form + Confirm), Register (table, filters Due-7d/Overdue, search BG No/Contractor, Excel download), Reminders log, Users/Admin (MVP minimal).

No Graph, no SharePoint, no Entra in Phase 1. Interfaces abstracted (`MailSender`, `TrackerStore`, `Extractor`) for later Graph swap.

## 5. Enterprise OCR Strategy

1. Provision DI S0 in Central India + budget alert. F0 500 free pages/mo covers pilot.
2. Baseline with `prebuilt-layout` on samples: extract text/tables, regex for dates `DD-MM-YYYY`, amounts `INR/Rs`, BG No patterns `XXXX...ILG/GT...`.
3. If project/location/LOA accuracy <95%, train `custom extraction` on 15-25 labelled BGs across banks.
4. Store raw DI JSON per PDF in Blob/folder for audit + retrain.

Cost at 200 pgs/mo: Layout $2/mo, Custom $6/mo. See comparison doc.

## 6. Email + Scheduler (SMTP-only)

- Config: host, port 587, STARTTLS, service user + app password in env/Key Vault, from display `BG Automation`.
- Daily job: `SELECT * WHERE To - 7 days == today AND reminder_sent_at IS NULL AND Status != Closed`.
- Template: `Subject: BG {BG No} expiring {To} — {Project}/{Contractor} — extend or close?` Body: amount, bank, claim period, LOA, link to register.
- On send: set `reminder_sent_at`, append to `reminder_log`. Retry 3x, dead-letter to review queue.
- Extension flow: new BG with same LOA → new row, old row `Extended=Yes, Status=Closed/Extended`.

## 7. Auth + Audit

- `bcrypt` passwords, session JWT httpOnly, roles: admin manages users, uploader uploads+confirms, viewer read+export.
- `audit_log (ts, user, action, bg_no, old_json, new_json)`.
- Later: swap to Entra ID without changing business logic.

## 8. Phases (maps to UC/ST)

- **Sprint 1 (UC001-UC004):** VM + DI provision, `POST /extract`, upload + review + Excel append + LOA extraction, local auth.
- **Sprint 2 (UC005):** SMTP T-7 job, Finance + User Team templates, extend/close update, failed-OCR queue, register filters + export.
- **Hardening:** backups (xlsx + db + PDFs daily), logs rotation, runbook, UAT 1 month parallel with manual entry, accuracy report.

## 9. Test Plan

- 20+ samples incl. `Sample_Bank_Guarantees/`, measure per-field precision on BG No/Amount/To/Claim/LOA.
- Scheduler dry-run with fake `To = today+7`.
- SMTP sandbox to test DL before prod.

## 10. Risks

- Scan skew/stamps → DI confidence low → human review mandatory, don't lower threshold.
- SMTP AUTH deprecation → keep Graph migration path open, request app-reg early.
- Local xlsx locks → single-writer API + SQLite truth, Excel as export.

## Next Approvals Needed

1. SMTP mailbox + DLs + template sign-off
2. Azure DI S0 Central India RG + cost alert owner
3. Confirm `To` = T-7 anchor
