"""Local web API for BG ingestion and review.

Start it with::

    $env:PYTHONPATH = "backend"
    .venv\\Scripts\\python.exe -m bgauto.web

Then open http://127.0.0.1:8000 . The server binds to loopback only. The page
and every asset are served from this process: no CDN. The register stays local,
but every uploaded PDF is sent to the Datalab API for OCR. The optional
Firecrawl name lookup sends just the OCR'd bank/contractor names and IFSC codes.

Roles (``plan/03``): an uploader owns and saves their runs, an admin administers,
and a viewer reads the register and workbook. Every route states its role in
its signature via :mod:`bgauto.web.security`; the UI only mirrors that.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import auth, config, lookup, ocr, register, schema, scheduler, store
from ..auth import AuthError, User
from ..extract import HEADERS
from ..mailer import MailSender
from . import runs, security

WEB_DIR = Path(__file__).resolve().parent
INDEX_HTML = WEB_DIR / "index.html"
LOGIN_HTML = WEB_DIR / "login.html"

log = logging.getLogger("bgauto.web")

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


# --- lifecycle -----------------------------------------------------------


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        schema.ensure_schema()
    except (OSError, sqlite3.Error):
        log.warning("schema top-up skipped (database unavailable)")
    try:
        with store.connect() as conn:
            created = auth.ensure_bootstrap_admin(conn)
            if created and created.get("password"):
                log.warning(
                    "No accounts existed. Created bootstrap admin %r with password: %s "
                    "(change it after the first sign-in)",
                    created["user"],
                    created["password"],
                )
            elif created:
                log.info("Created bootstrap admin %r from BG_BOOTSTRAP_ADMIN_PASS", created["user"])
    except (OSError, sqlite3.Error, AuthError) as error:
        log.error("could not prepare accounts: %s", error)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(
    title="BG Ingestion (local review)",
    version="0.3.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# --- request hardening ---------------------------------------------------


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    """Same-origin check for state changes plus a no-CDN content policy."""
    if request.method in MUTATING:
        origin = request.headers.get("origin")
        if origin:
            if urlsplit(origin).netloc != request.headers.get("host", ""):
                return JSONResponse(status_code=403, content={"detail": "Cross-origin request refused."})
        elif request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request refused."})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'",
    )
    return response


# --- models --------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class RowUpdate(BaseModel):
    source_pdf: str
    values: dict[str, Any] = Field(default_factory=dict)


class ConfirmRequest(BaseModel):
    include_flagged: bool = False
    rows: list[RowUpdate] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    email: str | None = None
    full_name: str | None = None


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    email: str | None = None
    full_name: str | None = None
    password: str | None = None


class RegisterPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class CloseRequest(BaseModel):
    remarks: str | None = None


# --- errors --------------------------------------------------------------


def _auth_http(error: AuthError) -> HTTPException:
    status = {
        "duplicate": 409,
        "weak_password": 400,
        "bad_request": 400,
        "not_found": 404,
    }.get(error.code, 401)
    return HTTPException(status_code=status, detail=str(error))


@app.exception_handler(runs.ConfirmError)
def _confirm_error(_: Request, exc: runs.ConfirmError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"detail": str(exc), "data": exc.payload},
    )


@app.exception_handler(PermissionError)
def _permission_error(_: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


# --- pages ---------------------------------------------------------------


def _page(path: Path) -> FileResponse:
    if not path.is_file():
        raise HTTPException(status_code=500, detail=f"Missing page {path.name}")
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page(INDEX_HTML)


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return _page(LOGIN_HTML)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


# --- health --------------------------------------------------------------

@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    # A local configuration check only: health must not spend a Datalab call.
    ocr_ok = ocr.datalab_available()
    return {
        "status": "ok" if ocr_ok else "degraded",
        "ocr_ok": ocr_ok,
        "ocr_engine": config.OCR_ENGINE,
        "ocr_mode": config.DATALAB_MODE,
        "datalab": {
            "configured": ocr_ok,
            "base_url": config.DATALAB_BASE_URL,
            "mode": config.DATALAB_MODE,
        },
        "database": str(config.DB_PATH) if config.DB_PATH.exists() else None,
        "template": str(config.TRACKER_PATH) if config.TRACKER_PATH.exists() else None,
        "workbook": str(config.WORKBOOK_PATH),
        "scheduler": config.SCHEDULER_ENABLED,
        "lookup": lookup.enabled(),
        "mail": "dry-run" if config.MAIL_DRY_RUN or not config.SMTP_HOST else config.SMTP_HOST,
        "time": dt.datetime.now().isoformat(timespec="seconds"),
    }


# --- authentication ------------------------------------------------------


def _session_response(user: User, token: str, status: int = 200) -> JSONResponse:
    response = JSONResponse(
        status_code=status,
        content={"user": user.as_dict(), "expires_in": config.SESSION_MINUTES * 60},
    )
    security.set_session_cookie(response, token)
    return response


@app.post("/api/login")
def login(request: Request, payload: LoginRequest) -> JSONResponse:
    with store.connect() as conn:
        try:
            user = auth.authenticate(
                conn, payload.username, payload.password, ip=security.client_ip(request)
            )
        except AuthError as error:
            # authenticate() already wrote the failed-attempt counter and the
            # login_failed/locked audit row. Commit before raising, otherwise
            # store.connect() rolls the lockout back with the exception.
            conn.commit()
            raise _auth_http(error) from error
        token = auth.create_session(
            conn,
            user.id,
            ip=security.client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    return _session_response(user, token)


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    token = security.token_from(request)
    user = security.optional_user(request)
    with store.connect() as conn:
        if user:
            store.audit(conn, "logout", user_id=user.id, ip=security.client_ip(request))
        auth.destroy_session(conn, token)
    security.clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(security.current_user)) -> dict:
    return {"user": user.as_dict()}


@app.get("/api/session")
def session_state(request: Request) -> dict:
    """Public probe for the sign-in page; 200 even when signed out."""
    user = security.optional_user(request)
    return {
        "authenticated": user is not None,
        "user": user.as_dict() if user else None,
    }


@app.post("/api/change-password")
def change_password(
    request: Request,
    payload: PasswordChange,
    user: User = Depends(security.current_user),
) -> JSONResponse:
    with store.connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
        if row is None or not auth.verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="The current password is incorrect.")
        try:
            auth.set_password(conn, user.id, payload.new_password)
        except AuthError as error:
            raise _auth_http(error) from error
        store.audit(conn, "password_change", user_id=user.id, ip=security.client_ip(request))
        token = auth.create_session(conn, user.id, ip=security.client_ip(request))
    return _session_response(user, token)


# --- masters -------------------------------------------------------------


@app.get("/api/masters")
def masters_endpoint(_: User = Depends(security.current_user)) -> dict:
    loaded = runs.masters()
    return {
        "companies": loaded.companies,
        "projects": loaded.projects,
        "project_options": loaded.project_options,
        "toll_plazas": loaded.toll_plazas,
        "natures": loaded.natures,
        "contractors": loaded.contractors,
        "banks": loaded.banks,
        "statuses": loaded.statuses,
        "bg_types": ["PBG", "ABG"],
        "extended": ["Yes", "No"],
        "columns": HEADERS,
    }


# --- runs (upload and review) -------------------------------------------


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@app.get("/api/runs")
def list_runs(user: User = Depends(security.require("uploader"))) -> dict:
    return {"runs": runs.list_runs(user)}


@app.post("/api/runs")
def create_run(user: User = Depends(security.require("uploader"))) -> dict:
    return runs.create_run(user)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, user: User = Depends(security.current_user)) -> dict:
    try:
        manifest = runs.require_run(run_id, user)
    except (FileNotFoundError, ValueError) as exc:
        raise _not_found(exc) from exc
    return {
        "run_id": manifest["run_id"],
        "created_at": manifest["created_at"],
        "owner": manifest.get("owner", ""),
        "status": manifest.get("status", "draft"),
        "progress": manifest.get("progress") or {},
        "files": [runs.public_entry(entry) for entry in manifest["files"]],
    }


@app.post("/api/runs/{run_id}/upload")
def upload_pdf(
    run_id: str,
    file: UploadFile = File(...),
    user: User = Depends(security.require("uploader")),
) -> dict:
    data = file.file.read()
    try:
        return runs.add_pdf(run_id, file.filename or "upload.pdf", data, user)
    except FileNotFoundError as exc:
        raise _not_found(exc) from exc
    except (ValueError, PermissionError) as exc:
        raise _bad_request(exc) from exc


@app.post("/api/runs/{run_id}/confirm")
def confirm_rows(
    run_id: str,
    request: ConfirmRequest,
    user: User = Depends(security.require("uploader")),
) -> dict:
    try:
        return runs.confirm(
            run_id,
            user,
            [row.model_dump() for row in request.rows],
            include_flagged=request.include_flagged,
        )
    except FileNotFoundError as exc:
        raise _not_found(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc


@app.get("/api/runs/{run_id}/workbook")
def download_workbook(
    run_id: str,
    user: User = Depends(security.require("viewer")),
) -> FileResponse:
    try:
        runs.load_manifest(run_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _not_found(exc) from exc
    if not config.WORKBOOK_PATH.exists():
        raise HTTPException(status_code=404, detail="No review workbook has been written yet.")
    return FileResponse(
        config.WORKBOOK_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=config.WORKBOOK_PATH.name,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/workbook/reset")
def reset_workbook(
    user: User = Depends(security.require("admin")),
) -> dict:
    runs.reset_workbook()
    with store.connect() as conn:
        store.audit(conn, "workbook_reset", user_id=user.id)
    return {"workbook": str(config.WORKBOOK_PATH)}


@app.get("/api/workbook/view")
def view_workbook(
    sheet: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=250),
    _: User = Depends(security.current_user),
) -> dict:
    """Return a read-only workbook page for the signed-in frontend."""
    from openpyxl import load_workbook

    if not config.WORKBOOK_PATH.exists():
        return {"sheets": [], "sheet": None, "headers": [], "rows": [], "total": 0}
    workbook = load_workbook(config.WORKBOOK_PATH, read_only=True, data_only=True)
    try:
        names = workbook.sheetnames
        selected = sheet if sheet in names else (names[0] if names else None)
        if not selected:
            return {"sheets": names, "sheet": None, "headers": [], "rows": [], "total": 0}
        worksheet = workbook[selected]
        iterator = worksheet.iter_rows(values_only=True)
        headers = [str(value or "") for value in next(iterator, ())]
        legacy_combined = "LOA/PO Number" in headers
        if legacy_combined:
            index = headers.index("LOA/PO Number")
            headers[index] = "LOA Number"
            headers.insert(index + 1, "PO Number")
        rows = []
        total = 0
        for values in iterator:
            if not any(value not in (None, "") for value in values):
                continue
            if total >= offset and len(rows) < limit:
                row = [value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value for value in values]
                if legacy_combined:
                    row.insert(index + 1, "")
                rows.append(row)
            total += 1
        return {"sheets": names, "sheet": selected, "headers": headers, "rows": rows,
                "offset": offset, "limit": limit, "total": total}
    finally:
        workbook.close()


@app.get("/api/workbook/download")
def download_review_workbook(_: User = Depends(security.current_user)) -> FileResponse:
    if not config.WORKBOOK_PATH.exists():
        raise HTTPException(status_code=404, detail="No review workbook has been written yet.")
    return FileResponse(
        config.WORKBOOK_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=config.WORKBOOK_PATH.name,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/runs/{run_id}/audit")
def run_audit(run_id: str, user: User = Depends(security.current_user)) -> dict:
    try:
        directory = runs.require_run(run_id, user)
    except (FileNotFoundError, ValueError) as exc:
        raise _not_found(exc) from exc
    files = sorted(directory.glob("confirm-*.json"))
    return {
        "run_id": run_id,
        "confirmations": [
            {"file": path.name, "content": json.loads(path.read_text(encoding="utf-8"))}
            for path in files
        ],
    }


# --- register ------------------------------------------------------------


def _scope(user: User) -> str | None:
    """Uploaders see their own queue; admin and viewer see everything."""
    return user.id if user.role == "uploader" else None


@app.get("/api/register")
def get_register(
    q: str = "",
    status: str = "",
    project: str = "",
    overdue: bool = False,
    window: int | None = None,
    reminded: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(security.current_user),
) -> dict:
    filters = {
        "q": q,
        "status": status,
        "project": project,
        "overdue": overdue,
        "window": window,
        "reminded": reminded,
        "limit": limit,
        "offset": offset,
        "created_by": _scope(user),
    }
    with store.connect() as conn:
        page = register.list_rows(conn, filters)
        return {**page, "stats": register.stats(conn, _scope(user))}


@app.get("/api/register/export")
def export_register(
    q: str = "",
    status: str = "",
    project: str = "",
    overdue: bool = False,
    window: int | None = None,
    user: User = Depends(security.require("viewer")),
) -> Response:
    filters = {
        "q": q,
        "status": status,
        "project": project,
        "overdue": overdue,
        "window": window,
        "limit": 500,
        "offset": 0,
    }
    with store.connect() as conn:
        page = register.list_rows(conn, filters)
        payload = register.export_bytes(page["rows"])
        store.audit(
            conn,
            "export",
            user_id=user.id,
            new={"rows": len(page["rows"]), "filters": filters},
        )
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="BG_register_{stamp}.xlsx"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/register/{bg_id}")
def get_register_row(bg_id: str, user: User = Depends(security.current_user)) -> dict:
    with store.connect() as conn:
        row = register.find_existing_by_id(conn, bg_id)
    if row is None:
        raise HTTPException(status_code=404, detail="BG not found.")
    if user.role == "uploader" and row.get("created_by") != user.username:
        raise HTTPException(status_code=403, detail="This row belongs to another uploader.")
    return row


@app.patch("/api/register/{bg_id}")
def patch_register_row(
    bg_id: str,
    payload: RegisterPatch,
    request: Request,
    user: User = Depends(security.require("admin")),
) -> dict:
    with store.connect() as conn:
        before = register.find_existing_by_id(conn, bg_id)
        if before is None:
            raise HTTPException(status_code=404, detail="BG not found.")
        try:
            row = register.update_row(conn, bg_id, payload.values, user.id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        changed = {
            key: {"from": before.get(key), "to": row.get(key)}
            for key in payload.values
            if before.get(key) != row.get(key)
        }
        action = "review_save"
        if str(row.get("Status")) == "Closed" and before.get("Status") != "Closed":
            action = "close"
        if str(row.get("Extended")) == "Yes" and before.get("Extended") != "Yes":
            action = "extend"
        store.audit(
            conn,
            action,
            user_id=user.id,
            bg_no=row["BG No"],
            old=changed or None,
            new={"requested": payload.values},
            ip=security.client_ip(request),
        )
    runs.update_workbook_row(row["BG No"], _workbook_values(row))
    return {"row": row, "workbook_updated": bool(config.WORKBOOK_PATH.exists())}


def _workbook_values(row: dict) -> dict:
    values: dict[str, Any] = {}
    for name in HEADERS:
        if name not in row:
            continue
        value = row[name]
        if name in {"From", "To", "Claim Period"} and value:
            try:
                value = dt.date.fromisoformat(str(value)[:10])
            except ValueError:
                pass
        values[name] = value
    return values


@app.post("/api/register/{bg_id}/close")
def close_register_row(
    bg_id: str,
    payload: CloseRequest,
    request: Request,
    user: User = Depends(security.require("admin")),
) -> dict:
    changes: dict[str, Any] = {"Status": "Closed"}
    if payload.remarks is not None:
        changes["Remarks"] = payload.remarks
    with store.connect() as conn:
        before = register.find_existing_by_id(conn, bg_id)
        if before is None:
            raise HTTPException(status_code=404, detail="BG not found.")
        row = register.update_row(conn, bg_id, changes, user.id)
        store.audit(
            conn,
            "close",
            user_id=user.id,
            bg_no=row["BG No"],
            old={"Status": before["Status"]},
            new={"Status": row["Status"], "Remarks": row.get("Remarks")},
            ip=security.client_ip(request),
        )
    runs.update_workbook_row(row["BG No"], _workbook_values(row))
    return {"row": row}


@app.get("/api/stats")
def get_stats(user: User = Depends(security.current_user)) -> dict:
    with store.connect() as conn:
        return register.stats(conn, _scope(user))


# --- reminders -----------------------------------------------------------


@app.get("/api/reminders")
def get_reminders(
    status: str = "",
    limit: int = 200,
    _: User = Depends(security.require("viewer")),
) -> dict:
    with store.connect() as conn:
        return {
            "reminders": scheduler.list_reminders(conn, status=status or None, limit=limit),
            "days": config.REMINDER_DAYS,
            "dry_run": config.MAIL_DRY_RUN or not config.SMTP_HOST,
            "recipients": scheduler_recipients(),
        }


def scheduler_recipients() -> list[str]:
    from ..mailer import recipients

    return recipients()


@app.post("/api/reminders/run")
def run_reminders(
    days: int | None = None,
    user: User = Depends(security.require("admin")),
) -> dict:
    report = scheduler.run_daily(days=days, sender=MailSender(), user_id=user.id)
    return report


@app.post("/api/reminders/{bg_id}/resend")
def resend_reminder(
    bg_id: str,
    user: User = Depends(security.require("admin")),
) -> dict:
    try:
        return scheduler.resend(bg_id, sender=MailSender(), user_id=user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc


@app.post("/internal/daily-reminder")
def internal_daily_reminder(
    _: None = Depends(security.require_internal),
    days: int | None = None,
) -> dict:
    return scheduler.run_daily(days=days, sender=MailSender())


# --- users ---------------------------------------------------------------


@app.get("/api/users")
def get_users(_: User = Depends(security.require("admin"))) -> dict:
    with store.connect() as conn:
        return {"users": [user.as_dict() for user in auth.list_users(conn)]}


@app.post("/api/users")
def create_user(
    payload: UserCreate,
    request: Request,
    actor: User = Depends(security.require("admin")),
) -> dict:
    with store.connect() as conn:
        try:
            user = auth.create_user(
                conn,
                payload.username,
                payload.password,
                payload.role,
                email=payload.email,
                full_name=payload.full_name,
            )
        except AuthError as error:
            raise _auth_http(error) from error
        store.audit(
            conn,
            "user_create",
            user_id=actor.id,
            new={**user.as_dict(), "created_by": actor.username},
            ip=security.client_ip(request),
        )
    return {"user": user.as_dict()}


def _active_admins(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users u JOIN user_roles ur ON ur.user_id = u.id "
        "JOIN roles r ON r.id = ur.role_id WHERE r.name = 'admin' AND u.is_active = 1"
    ).fetchone()[0]


@app.patch("/api/users/{user_id}")
def patch_user(
    user_id: str,
    payload: UserPatch,
    request: Request,
    actor: User = Depends(security.require("admin")),
) -> dict:
    with store.connect() as conn:
        try:
            target = auth.get_user(conn, user_id)
        except AuthError as error:
            raise _auth_http(error) from error
        losing_admin = target.role == "admin" and (
            (payload.role is not None and payload.role != "admin")
            or payload.is_active is False
        )
        if losing_admin and _active_admins(conn) <= 1:
            raise HTTPException(
                status_code=400,
                detail="This is the last active admin. Promote another admin first.",
            )
        try:
            if payload.password:
                auth.set_password(conn, user_id, payload.password)
                store.audit(
                    conn, "password_change", user_id=actor.id,
                    new={"target": target.username}, ip=security.client_ip(request),
                )
            user = auth.update_user(
                conn,
                user_id,
                role=payload.role,
                is_active=payload.is_active,
                full_name=payload.full_name,
                email=payload.email,
            )
        except AuthError as error:
            raise _auth_http(error) from error
        if payload.role and payload.role != target.role:
            store.audit(
                conn,
                "role_change",
                user_id=actor.id,
                new={"target": target.username, "from": target.role, "to": payload.role},
                ip=security.client_ip(request),
            )
        store.audit(
            conn,
            "user_update",
            user_id=actor.id,
            new={
                "target": user.username,
                "role": user.role,
                "is_active": user.is_active,
                "actor": actor.username,
            },
            ip=security.client_ip(request),
        )
    return {"user": user.as_dict()}


# --- audit ---------------------------------------------------------------


@app.get("/api/audit")
def get_audit(
    action: str = "",
    limit: int = Query(200, ge=1, le=1000),
    _: User = Depends(security.require("admin")),
) -> dict:
    where = " WHERE a.action = ?" if action else ""
    params: tuple = (action, limit) if action else (limit,)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT a.id, a.ts, a.action, a.bg_no, a.old_data, a.new_data, a.ip, "
            "       u.username FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id"
            f"{where} ORDER BY a.id DESC LIMIT ?",
            params,
        ).fetchall()
    return {
        "entries": [
            {
                "id": row["id"],
                "ts": row["ts"],
                "action": row["action"],
                "bg_no": row["bg_no"],
                "user": row["username"] or "",
                "ip": row["ip"],
                "old": json.loads(row["old_data"]) if row["old_data"] else None,
                "new": json.loads(row["new_data"]) if row["new_data"] else None,
            }
            for row in rows
        ]
    }
