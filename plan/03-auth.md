---
id: PLAN-03
title: Authentication and Authorization
status: active
owner: Laxmi
last_updated: 2026-10-08
version: 3
inputs: ["BG_UseCase_Story_Tracking.xlsx (UC006, ST009, ST011-ST013)"]
outputs: ["Login flow", "Role matrix", "Audit log"]
related: ["PLAN-01", "PLAN-02", "PLAN-05"]
---

# 03 — Authentication and Authorization

## 1. Purpose

This document defines who can log in and what each role is allowed to do.
It enforces maker-checker: the person who uploads a BG cannot approve it.
Read it before building login, any API endpoint, or any page.

Decision locked 2026-10-08: uploader proposes, admin approves.

## 2. Roles in plain language

- **Admin.** Full access. Reviews any item, approves (Confirms) rows into
  Excel, manages users, resends reminders.
- **Uploader.** Limited to own work. Uploads PDFs, fixes their own drafts,
  submits them for approval. Cannot write to Excel.
- **Viewer.** Read-only plus export. Searches and filters the register and
  downloads Excel. Cannot upload or edit.

Stories: ST009 (login + audit), ST011 (admin), ST012 (uploader), ST013
(viewer). All assigned to Laxmi except ST009 base.

## 3. Permission matrix

| Action | Admin | Uploader | Viewer |
|--------|-------|----------|--------|
| Upload a PDF | Yes | Yes (own files) | No |
| Edit own draft | Yes | Yes | No |
| Edit someone else's draft | Yes | No | No |
| Confirm a row into Excel | **Yes (only role)** | No — submits for approval instead | No |
| Edit a confirmed row (extend, close, correct) | Yes | No | No |
| Search and filter the register | Yes (all rows) | Own queue only | Yes (all rows) |
| Download or export Excel | Yes | No | Yes |
| Resend a T-7 reminder | Yes | No | No |
| Create users | Yes | No | No |
| Edit user (role, activate, deactivate, reset password) | Yes | No | No |
| Delete user | Yes — deactivate first; hard delete only if the user has no audit history | No | No |
| Delete a pending job (not yet confirmed) | Yes (any job) | Yes (own jobs only) | No |
| Delete a confirmed tracker row | No hard delete — admin voids via `Status = Closed` + audit entry | No | No |

## 4. How login works

1. Accounts live in the `users` table. Passwords use `bcrypt` (cost 12).
   Minimum 10 characters with upper, lower, digit, and symbol.
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

## 5. User management (admin only)

Admin owns the full user lifecycle:

1. **Create** — `POST /users` with username, role, temporary password.
   New account starts active; user must change password on first login.
2. **Edit** — `PATCH /users/{id}` changes role (`admin`, `uploader`,
   `viewer`) or details.
3. **Activate / deactivate** — deactivating blocks login immediately but
   keeps history. This is the default way to offboard someone.
4. **Reset password and unlock** — admin sets a temporary password and
   clears `failed_attempts` / `locked_until`.
5. **Delete** — allowed only when the user has no rows in `audit_log`,
   `jobs.owner`, or `reminder_log`. Otherwise deactivate instead of
   deleting, so history stays intact. Every user action is audited
   (`user_create`, `role_change`, `user_deactivate`, `user_delete`).

## 6. Delete policy (admin only, audited)

Delete is deliberately narrow. Nothing confirmed is ever hard-deleted.

| Target | Who | Rule |
|--------|-----|------|
| Pending job (`queued`, `extracting`, `pending_review`, `pending_admin`, `failed`) | Admin: any job. Uploader: own jobs only | `DELETE /jobs/{id}` removes the job, its PDFs, and DI JSON. Blocked once `status = confirmed`. Writes `job_delete` to audit. |
| Confirmed tracker row | Nobody hard-deletes | Admin corrects via void: set `Status = Closed`, add reason to `Remarks`, write audit entry. The row stays for history. |
| User account | Admin only | Deactivate by default. Hard delete only if the user never acted (no audit, jobs, or reminders). |
| Reminder log | Nobody | Append-only. Never deleted. |

Frontend rule: the Delete button appears only for admins (any job) and for
uploaders on their own unconfirmed jobs. Confirmed rows show Void/Close,
never Delete.

## 7. How authorization is enforced

Hiding buttons in the UI is not enough. Every API endpoint checks the role
and, where relevant, ownership (`job.owner == current user OR role == admin`).

| Endpoint | Admin | Uploader | Viewer |
|----------|-------|----------|--------|
| `POST /upload` | Yes | Yes, owner set to self | 403 |
| `POST /extract` | Yes | Yes | 403 |
| `GET /jobs` | All jobs | Own jobs only | 403 (use register) |
| `PATCH /jobs/{id}` | Any job | Own job in `pending_review` | 403 |
| `POST /jobs/{id}/submit` | Yes | Own job → `pending_admin` | 403 |
| `POST /jobs/{id}/confirm` | **Yes only** | 403 | 403 |
| `GET /register`, `GET /register/export` | Yes | Own queue | Yes |
| `GET /reminders`, `POST /reminders/resend` | Yes | No | Read-only |
| `POST /users`, `PATCH /users/{id}` | Yes | 403 | 403 |
| `DELETE /users/{id}` | Yes (only if user never acted, else deactivate) | 403 | 403 |
| `DELETE /jobs/{id}` | Yes (any unconfirmed job) | Yes (own unconfirmed jobs) | 403 |

Job lifecycle: `queued → extracting → pending_review (uploader) →
pending_admin → confirmed`, or `failed` (needs manual handling).

## 8. Audit

Every change writes one row to `audit_log` with timestamp, actor, action,
BG number, and before/after values. Tracked actions: login, login_failed,
locked, upload, extract, review_save, submit, confirm,
confirm_rejected_duplicate, register_export, reminder_sent, user_create,
role_change, user_deactivate, user_delete, job_delete.

Future: an `AuthProvider` interface lets Entra ID (OIDC) replace local login
without touching job or tracker code.

## 9. Acceptance criteria

- [ ] Uploader calling Confirm gets 403; nothing is written to Excel.
- [ ] Uploader cannot open or edit another uploader's job.
- [ ] Viewer can search and export but gets 403 on upload, edit, confirm.
- [ ] Every Confirm and export appears in `audit_log`.
- [ ] Uploader can delete own unconfirmed job; cannot delete confirmed rows or other users' jobs.
- [ ] Admin can create, deactivate, and reset users; hard delete works only when the user never acted.
- [ ] Confirmed Excel rows are never hard-deleted; void goes through `Status = Closed` + audit.

## References

- Data model: `02-data-model.md`
- Flows: `05-flows.md`
