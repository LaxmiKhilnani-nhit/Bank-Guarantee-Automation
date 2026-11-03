---
id: PLAN-03
title: Authentication and Authorization
status: active
last_updated: 2026-10-08
version: 3
inputs: ["BG_UseCase_Story_Tracking.xlsx (UC006, ST009, ST011-ST013)"]
outputs: ["Login flow", "Role matrix", "Audit log"]
related: ["PLAN-01", "PLAN-02", "PLAN-05"]
---

# 03 — Authentication and Authorization

## 1. Purpose

This document defines who can log in and what each role is allowed to do.
It allows uploaders to review and save their own BG runs directly.
Read it before building login, any API endpoint, or any page.

Current workflow: uploader reviews and saves their own run directly.

## 2. Roles in plain language

- **Admin.** Full access. Manages users, can save runs, and resends reminders.
- **Uploader.** Limited to own work. Uploads PDFs, fixes their own drafts,
  saves rows directly to the register and workbook.
- **Viewer.** Read-only plus export. Searches and filters the register, views
  the review workbook, and downloads Excel. Cannot upload or edit.

Stories: ST009 (login + audit), ST011 (admin), ST012 (uploader), ST013
(viewer). All assigned to Laxmi except ST009 base.

## 3. Permission matrix

| Action | Admin | Uploader | Viewer |
|--------|-------|----------|--------|
| Upload a PDF | Yes | Yes (own files) | No |
| Edit own draft | Yes | Yes | No |
| Edit someone else's draft | Yes | No | No |
| Save own reviewed rows to register/workbook | Yes | Yes (own runs) | No |
| Edit a confirmed row (extend, close, correct) | Yes | No | No |
| Search and filter the register | Yes (all rows) | Own queue only | Yes (all rows) |
| View workbook in frontend | Yes | Yes | Yes |
| Download or export Excel | Yes | Yes | Yes |
| Resend a T-7 reminder | Yes | No | No |
| Create or manage users | Yes | No | No |

## 4. How login works

1. Accounts live in the `users` table. Passwords use `bcrypt` (cost 12) in the
   plan; the local build uses `hashlib.scrypt` (see `plan/00-log.md`).
   Minimum 8 characters with upper, lower, digit, and symbol for the pilot
   (10 until 2026-10-30; raise again when SSO replaces local sign-in).
2. The first admin comes from environment variables
   (`BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASS`), then bootstrap is disabled.
3. `POST /login` checks the password and sets a 30-minute JWT in an
   `httpOnly + Secure + SameSite=Lax` cookie. The token is never stored in
   browser local storage.
4. `GET /me` returns the current user and role. `POST /logout` clears the cookie.
5. Five wrong passwords lock the account for 15 minutes
   (`failed_attempts`, `locked_until`).
6. Users change their own password via `POST /change-password`. Admins manage
   accounts via `POST /users` and `PATCH /users/{id}`.

## 5. How authorization is enforced

Hiding buttons in the UI is not enough. Every API endpoint checks the role
and, where relevant, ownership (`job.owner == current user OR role == admin`).

| Endpoint | Admin | Uploader | Viewer |
|----------|-------|----------|--------|
| `POST /upload` | Yes | Yes, owner set to self | 403 |
| `POST /extract` | Yes | Yes | 403 |
| `GET /jobs` | All jobs | Own jobs only | 403 (use register) |
| `PATCH /jobs/{id}` | Any job | Own job in `pending_review` | 403 |
| `POST /api/runs/{id}/confirm` | Yes | Own run | 403 |
| `GET /register`, `GET /register/export` | Yes | Own queue | Yes |
| `GET /reminders`, `POST /reminders/resend` | Yes | No | Read-only |
| `POST /users`, `PATCH /users/{id}` | Yes | 403 | 403 |

Run lifecycle: `draft → confirmed`, or `failed` (needs manual handling).

## 6. Audit

Every change writes one row to `audit_log` with timestamp, actor, action,
BG number, and before/after values. Tracked actions: login, login_failed,
locked, upload, extract, review_save, confirm,
confirm_rejected_duplicate, register_export, reminder_sent, user_create,
role_change.

Future: an `AuthProvider` interface lets Entra ID (OIDC) replace local login
without touching job or tracker code.

## 7. Acceptance criteria

- [ ] Uploader saves their own run; ownership prevents access to others' runs.
- [ ] Uploader cannot open or edit another uploader's job.
- [ ] Viewer can search, view workbook and export but gets 403 on upload, edit, confirm.
- [ ] Every Confirm and export appears in `audit_log`.

## References

- Data model: `02-data-model.md`
- Flows: `05-flows.md`
