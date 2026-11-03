---
id: PLAN-INDEX
title: BG Automation — Plan Index
status: active
last_updated: 2026-10-08
version: 3
inputs: ["BG_Automation_Plan.md", "BG Tracker.xlsx", "BG_UseCase_Story_Tracking.xlsx"]
outputs: ["plan/00-log.md", "plan/01-architecture.md", "plan/02-data-model.md", "plan/03-auth.md", "plan/04-ocr-extraction.md", "plan/05-flows.md", "plan/06-email-scheduler-hosting.md"]
---

# BG Automation — Plan Index

## 1. Purpose

This folder holds the detailed design for BG Automation. The master summary is
`BG_Automation_Plan.md` at the repo root. Each file here expands one topic so
that any engineer — or any AI agent — can implement it without asking
follow-up questions.

Who should read what:

- New to the project: read this file, then `01`, `05`, `02` in that order.
- Building auth: read `03`.
- Building OCR: read `04`.
- Setting up mail or the VM: read `06`.
- Reviewing history: read `00-log.md`.

## 2. Document standard

Every file in this folder follows the same structure so humans and agents can
parse it the same way:

1. YAML frontmatter (id, title, status, owner, date, version, inputs, outputs).
2. `1. Purpose` — why the document exists and who should read it.
3. `2. Context / Scope` — what is covered and what is out of scope.
4. Body sections — decisions, specifications, diagrams.
5. `Acceptance criteria` — how to verify the work is done.
6. `References` — links to related files, sheets, or stories.

Writing rules used: short sentences, active voice, one idea per bullet,
tables for anything comparable, Mermaid for every flow.

## 3. Documents

| ID | File | Question it answers |
|----|------|---------------------|
| PLAN-LOG | `00-log.md` | What changed, when, and why? |
| PLAN-01 | `01-architecture.md` | What are the components and where do they run? |
| PLAN-02 | `02-data-model.md` | What are the exact columns, types, and validation rules? |
| PLAN-03 | `03-auth.md` | Who is allowed to do what? |
| PLAN-04 | `04-ocr-extraction.md` | How do we turn a PDF into structured fields? |
| PLAN-05 | `05-flows.md` | What happens step by step in each scenario? |
| PLAN-06 | `06-email-scheduler-hosting.md` | How do reminders send and where is the app hosted? |

## 4. Global conventions

- Dates shown as `DD-MM-YYYY` in the UI and Excel. Stored as ISO (`YYYY-MM-DD`) in the database.
- Money in INR, stored as a number (no commas, no symbols).
- `To` is the expiry date and the T-7 reminder anchor. `Claim Period` is not the anchor.
- `BG No` is unique. Matching ignores case and extra spaces.
- Only the API writes to `BG Tracker.xlsx`. Users never open the file directly.
- Status values: `Draft` (internal) / `Active` / `Expired` / `Closed` / `Extended`.

## References

- Master plan: `../BG_Automation_Plan.md`
- Tracker: `../BG Tracker.xlsx` (Sheet1, 21 columns; LOA and PO are separate)
- Stories: `../BG_UseCase_Story_Tracking.xlsx`
- Change history: `00-log.md`
