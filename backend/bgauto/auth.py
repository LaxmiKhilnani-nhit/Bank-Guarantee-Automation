"""Local accounts, password hashing, roles and server-side sessions.

Design notes (deviations from ``plan/03`` are deliberate and logged):

* ``hashlib.scrypt`` instead of bcrypt - no native build, no extra wheel.
* opaque session tokens in SQLite instead of a JWT - logout is immediate and
  nothing is stored in browser storage.
* the cookie is ``httpOnly`` + ``SameSite=Lax``; ``Secure`` is off by default
  because the pilot runs on loopback over plain HTTP (set ``BG_COOKIE_SECURE=1``
  once TLS terminates in front of the app).

Roles are rows (``roles``/``user_roles``), so a user can hold more than one. The
effective role is the strongest one: admin > uploader > viewer.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass, field

from . import config
from .store import audit, iso, utcnow

ROLE_ORDER = ("admin", "uploader", "viewer")
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


class AuthError(Exception):
    """A login or account action the caller must report to the user."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


@dataclass
class User:
    id: str
    username: str
    role: str
    roles: list[str] = field(default_factory=list)
    full_name: str = ""
    email: str = ""
    is_active: bool = True

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "roles": self.roles,
            "full_name": self.full_name,
            "email": self.email,
            "is_active": self.is_active,
        }

    def has_role(self, *names: str) -> bool:
        return self.role == "admin" or self.role in names


# --- passwords -----------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$", 5)
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def password_problems(password: str) -> list[str]:
    """Pilot rules: >= 8 chars, upper, lower, digit, symbol.

    ``plan/03`` said 10; the local pilot uses 8 so the role-based demo
    passwords work. Tighten this again when SSO replaces local sign-in.
    """
    problems = []
    if len(password or "") < 8:
        problems.append("at least 8 characters")
    if not any(c.isupper() for c in password or ""):
        problems.append("an uppercase letter")
    if not any(c.islower() for c in password or ""):
        problems.append("a lowercase letter")
    if not any(c.isdigit() for c in password or ""):
        problems.append("a digit")
    if not any(not c.isalnum() for c in password or ""):
        problems.append("a symbol")
    return problems


def _strong_password() -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyz"
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    symbols = "!@#$%^&*-_=+?"
    pools = (alphabet, upper, digits, symbols)
    chars = [secrets.choice(pool) for pool in pools]
    chars += [secrets.choice(alphabet + upper + digits + symbols) for _ in range(10)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# --- users ---------------------------------------------------------------


def _roles(conn: sqlite3.Connection, user_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
        "WHERE ur.user_id = ? ORDER BY r.id",
        (user_id,),
    ).fetchall()
    return [row[0] for row in rows]


def _effective(roles: list[str]) -> str:
    for name in ROLE_ORDER:
        if name in roles:
            return name
    return "viewer"


def _to_user(row: sqlite3.Row, roles: list[str]) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        role=_effective(roles),
        roles=roles,
        full_name=row["full_name"] or "",
        email=row["email"] or "",
        is_active=bool(row["is_active"]),
    )


def ensure_roles(conn: sqlite3.Connection) -> None:
    for name in ROLE_ORDER:
        conn.execute("INSERT OR IGNORE INTO roles (name) VALUES (?)", (name,))


def ensure_bootstrap_admin(conn: sqlite3.Connection) -> dict | None:
    """Create the first admin when the users table is empty.

    The password comes from ``BG_BOOTSTRAP_ADMIN_PASS``; when that is unset a
    random one is generated and printed once to the log (local pilot only).
    """
    ensure_roles(conn)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count:
        return None
    password = config.BOOTSTRAP_ADMIN_PASS or _strong_password()
    problems = password_problems(password)
    if problems:
        raise AuthError(
            "BG_BOOTSTRAP_ADMIN_PASS must contain " + ", ".join(problems), "weak_password"
        )
    user = create_user(
        conn,
        config.BOOTSTRAP_ADMIN_USER,
        password,
        "admin",
        email=config.BOOTSTRAP_ADMIN_EMAIL or None,
        full_name="Bootstrap admin",
    )
    return {"user": user.username, "password": password if not config.BOOTSTRAP_ADMIN_PASS else None}


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    role: str,
    *,
    email: str | None = None,
    full_name: str | None = None,
) -> User:
    username = (username or "").strip()
    if not username:
        raise AuthError("Username is required.", "bad_request")
    if role not in ROLE_ORDER:
        raise AuthError(f"Unknown role: {role}", "bad_request")
    problems = password_problems(password)
    if problems:
        raise AuthError("Password must contain " + ", ".join(problems), "weak_password")
    try:
        user_id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO users (id, username, email, full_name, password_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, (email or None), (full_name or None), hash_password(password)),
        )
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT ?, id FROM roles WHERE name = ?",
            (user_id, role),
        )
    except sqlite3.IntegrityError as error:
        raise AuthError("That username or email is already taken.", "duplicate") from error
    return get_user(conn, user_id)


def get_user(conn: sqlite3.Connection, user_id: str) -> User:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise AuthError("User not found.", "not_found")
    return _to_user(row, _roles(conn, user_id))


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [_to_user(row, _roles(conn, row["id"])) for row in rows]


def update_user(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    role: str | None = None,
    is_active: bool | None = None,
    full_name: str | None = None,
    email: str | None = None,
) -> User:
    if role is not None:
        if role not in ROLE_ORDER:
            raise AuthError(f"Unknown role: {role}", "bad_request")
    fields: dict[str, object] = {}
    if is_active is not None:
        fields["is_active"] = 1 if is_active else 0
    if full_name is not None:
        fields["full_name"] = full_name.strip() or None
    if email is not None:
        fields["email"] = email.strip() or None
    if fields:
        columns = ", ".join(f"{name} = ?" for name in fields)
        conn.execute(
            f"UPDATE users SET {columns}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), user_id),
        )
    if role is not None:
        conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) SELECT ?, id FROM roles WHERE name = ?",
            (user_id, role),
        )
    user = get_user(conn, user_id)
    if is_active is False:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return user


def set_password(conn: sqlite3.Connection, user_id: str, password: str) -> None:
    problems = password_problems(password)
    if problems:
        raise AuthError("Password must contain " + ", ".join(problems), "weak_password")
    conn.execute(
        "UPDATE users SET password_hash = ?, failed_attempts = 0, locked_until = NULL, "
        "updated_at = datetime('now') WHERE id = ?",
        (hash_password(password), user_id),
    )
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def authenticate(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    ip: str | None = None,
) -> User:
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", ((username or "").strip(),)
    ).fetchone()
    if row is None:
        audit(conn, "login_failed", ip=ip, new={"username": username})
        raise AuthError("Incorrect username or password.", "invalid")
    if not row["is_active"]:
        audit(conn, "login_failed", user_id=row["id"], ip=ip, new={"reason": "inactive"})
        raise AuthError("This account is disabled.", "inactive")

    now = utcnow()
    locked_until = row["locked_until"]
    if locked_until:
        try:
            if utcnow() < dt.datetime.fromisoformat(str(locked_until)):
                audit(conn, "login_failed", user_id=row["id"], ip=ip, new={"reason": "locked"})
                raise AuthError("Too many failed attempts. Try again in 15 minutes.", "locked")
        except ValueError:
            pass

    if not verify_password(password, row["password_hash"]):
        failed = (row["failed_attempts"] or 0) + 1
        locked = failed >= MAX_FAILED_ATTEMPTS
        conn.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
            (
                failed,
                iso(now + dt.timedelta(minutes=LOCK_MINUTES)) if locked else None,
                row["id"],
            ),
        )
        if locked:
            audit(conn, "locked", user_id=row["id"], ip=ip, new={"attempts": failed})
        audit(conn, "login_failed", user_id=row["id"], ip=ip, new={"attempts": failed})
        raise AuthError("Incorrect username or password.", "invalid")

    conn.execute(
        "UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (iso(now), row["id"]),
    )
    audit(conn, "login", user_id=row["id"], ip=ip)
    return _to_user(row, _roles(conn, row["id"]))




# --- sessions ------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    minutes: int | None = None,
) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + dt.timedelta(minutes=minutes or config.SESSION_MINUTES)
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso(now),))
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_token_hash(token), user_id, iso(now), iso(expires), iso(now), ip, (user_agent or "")[:200]),
    )
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> User | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT s.token_hash, s.expires_at, s.last_seen, u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (_token_hash(token),),
    ).fetchone()
    if row is None:
        return None
    now = utcnow()
    try:
        expired = dt.datetime.fromisoformat(row["expires_at"]) <= now
    except ValueError:
        expired = True
    if expired or not row["is_active"]:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return None
    # Sliding expiry, refreshed at most once a minute so every request is not a write.
    try:
        stale = (now - dt.datetime.fromisoformat(row["last_seen"] or row["expires_at"])).total_seconds() > 60
    except ValueError:
        stale = True
    if stale:
        conn.execute(
            "UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
            (
                iso(now),
                iso(now + dt.timedelta(minutes=config.SESSION_MINUTES)),
                row["token_hash"],
            ),
        )
    return _to_user(row, _roles(conn, row["id"]))


def destroy_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def audit_login(conn: sqlite3.Connection, user_id: str, action: str, ip: str | None = None) -> None:
    audit(conn, action, user_id=user_id, ip=ip)
