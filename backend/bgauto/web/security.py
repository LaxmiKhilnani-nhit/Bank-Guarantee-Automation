"""Request identity and role checks for the FastAPI app.

One dependency reads the opaque session cookie, looks it up in SQLite and
returns a :class:`~bgauto.auth.User`. Role checks are dependencies too, so an
endpoint cannot forget one: the check is part of its signature.

Hiding a button in the page is never the control (``plan/03`` section 5).
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response

from .. import config, store
from ..auth import User, session_user

COOKIE = "bg_session"


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def token_from(request: Request) -> str | None:
    return request.cookies.get(COOKIE)


def optional_user(request: Request) -> User | None:
    token = token_from(request)
    if not token:
        return None
    with store.connect() as conn:
        return session_user(conn, token)


def current_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


def require(*roles: str):
    """Dependency factory: at least one of ``roles`` (admins always pass)."""

    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles and user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({user.role}) cannot do this. Required: {', '.join(roles)}.",
            )
        return user

    return dependency


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=config.SESSION_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def require_internal(request: Request) -> None:
    """Guard ``/internal/daily-reminder`` when a shared secret is configured."""
    if not config.INTERNAL_TOKEN:
        return
    supplied = request.headers.get("x-internal-token", "")
    import hmac

    if not hmac.compare_digest(supplied, config.INTERNAL_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid internal token.")
