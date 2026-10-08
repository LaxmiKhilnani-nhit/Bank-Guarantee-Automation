---
id: PLAN-01
title: System Architecture
status: active
owner: Laxmi
last_updated: 2026-10-08
version: 3
inputs: ["BG_Automation_Plan.md", "BG Tracker.xlsx"]
outputs: ["React dashboard", "FastAPI service", "SQLite DB", "BG Tracker.xlsx updates"]
related: ["PLAN-02", "PLAN-03", "PLAN-06"]
---

# 01 — System Architecture

## 1. Purpose

This document describes the building blocks of BG Automation and where they
run. Read it if you are setting up the project, deploying it, or adding a
new integration.

## 2. Context and scope

- In scope: frontend, backend API, database, Excel tracker, OCR service,
  mail service, VM deployment.
- Out of scope (Phase 1): Microsoft Graph, SharePoint live sync, Entra SSO.
  The code must keep clean seams so these can be added later without rewrites.

## 3. System context

A user uploads BG PDFs on a web dashboard. The backend extracts fields with
Azure Document Intelligence, a human reviews the result, and confirmed rows
are appended to the Excel tracker. A daily job emails Finance and the User
Team 7 days before expiry.

```mermaid
flowchart LR
  Uploader["Uploader or Admin\n(React dashboard)"] -->|HTTPS| API["FastAPI on port 8000\n(upload, review, register, auth)"]
  Viewer["Viewer - Finance / User Team"] -->|HTTPS read and export| API
  API -->|SDK or REST| DI["Azure Document Intelligence\n(Central India)"]
  API -->|single writer| XLSX["BG Tracker.xlsx\n(Sheet1, 20 columns)"]
  API -->|SQL| DB[("SQLite\nusers, jobs, audit, reminders")]
  API -->|STARTTLS port 587| SMTP["smtp.office365.com\n(service mailbox)"]
  SMTP -->|T-7 reminder| DL["Finance DL + User Team DL"]
  API -->|raw JSON archive| STORE["DI JSON store\n(folder or Blob)"]
```

Phase 1 has no Graph, SharePoint, or Entra. Each external dependency sits
behind an interface: `Extractor`, `TrackerStore`, `MailSender`,
`AuthProvider`.

## 4. Components

```mermaid
flowchart TB
  subgraph UI["Frontend - React (Vite)"]
    Login["Login"]
    Upload["Upload - drag and drop, multiple PDFs"]
    Review["Review - OCR text plus editable form"]
    Register["Register - table, Due-7d and Overdue filters, search, export"]
    RemLog["Reminders log"]
    Users["Users and Admin"]
  end
  subgraph SVC["Backend - FastAPI"]
    AuthN["Auth - JWT cookie, role checks"]
    Jobs["Jobs - upload, fetch, edit, confirm"]
    Extract["Extractor - DI layout plus regex, custom model later"]
    Track["TrackerStore - append row, dedupe on BG No"]
    Mail["MailSender - SMTP"]
    Sched["Scheduler - daily 09:00 T-7 check"]
  end
  Login --> AuthN
  Upload --> Jobs
  Review --> Jobs
  Register --> Track
  Jobs --> Extract
  Jobs --> Track
  Sched --> Mail
  Jobs --> AuthN
```

| Component | Responsibility | Key rule |
|-----------|---------------|----------|
| Upload | Accept PDFs, create a job per file | Max file size and type validated before queuing |
| Extractor | Call DI, apply regex, return fields + confidence | Never invent a value; leave low-confidence fields blank |
| Review | Let humans fix fields and pick dropdowns | Uploader edits own drafts; admin edits any |
| TrackerStore | Append confirmed rows to Excel | One writer at a time; reject duplicate BG No with 409 |
| MailSender | Send T-7 mails, log results | Retry 3 times, then dead-letter |
| Auth | Login, roles, audit | Every mutation is logged |

## 5. Deployment on the Windows VM

```mermaid
flowchart LR
  Internet --> IIS["IIS reverse proxy (HTTPS)"]
  IIS --> FE["Static React build"]
  IIS -->|"/api/*"| SVC["Windows Service\n(uvicorn FastAPI on 127.0.0.1:8000)"]
  SVC --> DATA["C:/bg-auto/data/\nBG Tracker.xlsx, app.db, pdfs/, di-json/, backups/"]
  SVC -->|outbound| DIC["Azure DI - Central India"]
  SVC -->|outbound port 587| O365["smtp.office365.com"]
  TS["Task Scheduler - daily 09:00 IST"] -->|calls API| SVC
```

- IIS ends TLS and forwards `/api/*` to the backend.
- The service account owns the data folder. End users never open the Excel file directly.
- All secrets and endpoints come from environment variables (`DI_ENDPOINT`,
  `DI_KEY`, `SMTP_*`, `JWT_SECRET`, paths), so a later move to App Service
  needs config changes only.
- Docker is optional for the pilot.

## 6. Acceptance criteria

- [ ] A new developer can draw the data path (browser → API → DI → Excel → mail) from this file alone.
- [ ] Every external system maps to one interface (`Extractor`, `TrackerStore`, `MailSender`, `AuthProvider`).
- [ ] Deployment steps work on a fresh Windows VM without code edits.

## References

- Master plan §4: `../BG_Automation_Plan.md`
- Data model: `02-data-model.md`
- Hosting detail: `06-email-scheduler-hosting.md`
