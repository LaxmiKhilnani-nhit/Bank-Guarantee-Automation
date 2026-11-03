"""The SQLite register: confirmed BGs, master resolution, extensions, export.

Excel stays the business record; this mirror is what the Register screen, the
T-7 reminder query and the audit trail read. One row per BG number, the same
key the workbook uses.

Master values are resolved by name. A reviewer typing a value that is not in a
master creates it with ``source = 'manual'`` - the catalogue grows from
confirmed work, which is the agreed Phase B rule.
"""
from __future__ import annotations

import datetime as dt
import io
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from . import store
from .extract import HEADERS
from .normalize import normalise_key

STATUS_FALLBACK = "Active"
# Columns the Register screen shows, in tracker order plus the keys it needs.
DISPLAY_COLUMNS = [
    "BG No", "Company Name", "Project Name", "Toll Plaza Name", "Types of BG",
    "Nature of Service", "Name of Contractor", "BG Amount", "From", "To",
    "Claim Period", "Extended", "Bank Name", "Status", "Remarks",
    "LOA Number", "PO Number", "source_pdf", "created_by", "created_at",
    "reminder_sent_at",
]

_SELECT = """
SELECT r.id, r.bg_no, r.bg_amount, r.from_date, r.to_date, r.claim_period,
       r.extended_date, r.status, r.remarks, r.source_pdf, r.created_at,
       r.reminder_sent_at, r.loa_id,
       c.name AS company, p.code AS project, p.name AS project_name,
       t.name AS plaza, r.bg_type, n.name AS nature, ct.name AS contractor,
       b.name AS bank, l.loa_po_number AS loa, r.po_number AS po,
       u.username AS created_by
FROM bg_records r
LEFT JOIN company c        ON c.id = r.company_id
LEFT JOIN project p        ON p.id = r.project_id
LEFT JOIN toll_plaza t     ON t.id = r.toll_plaza_id
LEFT JOIN nature_of_service n ON n.id = r.nature_id
LEFT JOIN contractor ct     ON ct.id = r.contractor_id
LEFT JOIN bank b            ON b.id = r.bank_id
LEFT JOIN loa_records l     ON l.id = r.loa_id
LEFT JOIN users u           ON u.id = r.created_by
"""


def _iso(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _master_id(conn, sql: str, name: str | None) -> int | None:
    if not name or not str(name).strip():
        return None
    return conn.execute(sql, (str(name).strip(),)).fetchone()


def _named(
    conn, table: str, name: str | None, source: bool = False, origin: str = "manual"
) -> int | None:
    """Find or create a simple name master (company, bank, nature, contractor)."""
    clean = (name or "").strip()
    if not clean:
        return None
    row = conn.execute(
        f"SELECT id FROM {table} WHERE lower(trim(name)) = lower(?)", (clean,)
    ).fetchone()
    if row:
        return int(row[0])
    if source:
        cursor = conn.execute(
            f"INSERT INTO {table} (name, source) VALUES (?, ?)", (clean, origin)
        )
    else:
        cursor = conn.execute(f"INSERT INTO {table} (name) VALUES (?)", (clean,))
    return int(cursor.lastrowid)


def ensure_company(conn, name: str | None, origin: str = "manual") -> int | None:
    return _named(conn, "company", name, source=True, origin=origin)


def ensure_bank(conn, name: str | None, origin: str = "manual") -> int | None:
    return _named(conn, "bank", name, source=True, origin=origin)


def ensure_nature(conn, name: str | None) -> int | None:
    return _named(conn, "nature_of_service", name)


def ensure_contractor(conn, name: str | None, origin: str = "manual") -> int | None:
    return _named(conn, "contractor", name, source=True, origin=origin)


def ensure_status(conn, code: str | None) -> str:
    clean = (code or "").strip() or STATUS_FALLBACK
    conn.execute("INSERT OR IGNORE INTO bg_status (code) VALUES (?)", (clean,))
    return clean


def ensure_bg_type(conn, code: str | None) -> str | None:
    clean = (code or "").strip().upper()
    if not clean:
        return None
    conn.execute("INSERT OR IGNORE INTO bg_type (code) VALUES (?)", (clean,))
    return clean


def ensure_project(conn, code: str | None) -> int | None:
    clean = (code or "").strip()
    if not clean:
        return None
    row = conn.execute(
        "SELECT id FROM project WHERE lower(trim(code)) = lower(?)", (clean,)
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO project (code, name, source, is_active) VALUES (?, ?, 'manual', 1)",
        (clean, clean),
    )
    return int(cursor.lastrowid)


def ensure_plaza(conn, project_id: int | None, name: str | None) -> int | None:
    clean = (name or "").strip()
    if not clean or project_id is None:
        return None
    row = conn.execute(
        "SELECT id FROM toll_plaza WHERE project_id = ? AND lower(trim(name)) = lower(?)",
        (project_id, clean),
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO toll_plaza (project_id, name, source, is_active) VALUES (?, ?, 'manual', 1)",
        (project_id, clean),
    )
    return int(cursor.lastrowid)


def ensure_loa(conn, number: str | None, user_id: str | None, description: str | None = None) -> str | None:
    clean = " ".join((number or "").split())
    if not clean:
        return None
    row = conn.execute(
        "SELECT id FROM loa_records WHERE loa_po_number = ?", (clean,)
    ).fetchone()
    if row:
        return str(row[0])
    cursor = conn.execute(
        "INSERT INTO loa_records (loa_po_number, description, created_by) VALUES (?, ?, ?)",
        (clean, description, user_id),
    )
    # The primary key is TEXT; lastrowid is SQLite's internal integer rowid.
    return str(
        conn.execute(
            "SELECT id FROM loa_records WHERE rowid = ?", (cursor.lastrowid,)
        ).fetchone()[0]
    )


# --- write path ----------------------------------------------------------


def find_existing(conn, bg_no: str) -> dict | None:
    row = conn.execute(
        f"{_SELECT} WHERE upper(trim(r.bg_no)) = upper(trim(?))", (str(bg_no),)
    ).fetchone()
    return row_to_dict(row) if row else None


def find_existing_by_id(conn, bg_id: str) -> dict | None:
    row = conn.execute(f"{_SELECT} WHERE r.id = ?", (str(bg_id),)).fetchone()
    return row_to_dict(row) if row else None


def store_row(
    conn, row: dict, user_id: str | None, sources: dict[str, str] | None = None
) -> tuple[str, dict]:
    """Insert one confirmed row. Returns ("written"|"duplicate", detail).

    ``sources`` maps a tracker column ("Name of Contractor", "Bank Name") to a
    master origin such as ``web`` when the reviewer picked a Firecrawl match.
    """
    sources = sources or {}
    bg_no = " ".join(str(row.get("BG No") or "").split())
    if not bg_no:
        return "duplicate", {"error": "BG No is empty"}
    existing = find_existing(conn, bg_no)
    if existing:
        return "duplicate", existing

    company_id = ensure_company(
        conn, row.get("Company Name"), sources.get("Company Name", "manual")
    )
    project_id = ensure_project(conn, row.get("Project Name"))
    plaza_id = ensure_plaza(conn, project_id, row.get("Toll Plaza Name"))
    nature_id = ensure_nature(conn, row.get("Nature of Service"))
    contractor_id = ensure_contractor(
        conn, row.get("Name of Contractor"), sources.get("Name of Contractor", "manual")
    )
    bank_id = ensure_bank(
        conn, row.get("Bank Name"), sources.get("Bank Name", "manual")
    )
    bg_type = ensure_bg_type(conn, row.get("Types of BG"))
    status = ensure_status(conn, row.get("Status"))
    loa_id = ensure_loa(conn, row.get("LOA Number"), user_id, row.get("Remarks"))

    extended = str(row.get("Extended") or "").strip().lower() in {"yes", "y", "true"}
    cursor = conn.execute(
        "INSERT INTO bg_records (bg_no, company_id, project_id, toll_plaza_id, bg_type, "
        "nature_id, contractor_id, bank_id, loa_id, po_number, bg_amount, from_date, to_date, "
        "claim_period, extended_date, status, remarks, source_pdf, created_by, "
        "reminder_sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            bg_no,
            company_id,
            project_id,
            plaza_id,
            bg_type,
            nature_id,
            contractor_id,
            bank_id,
            loa_id,
            row.get("PO Number") or None,
            float(row.get("BG Amount") or 0),
            _iso(row.get("From")),
            _iso(row.get("To")),
            _iso(row.get("Claim Period")),
            dt.date.today().isoformat() if extended else None,
            status,
            row.get("Remarks"),
            row.get("source_pdf"),
            user_id,
            row.get("reminder_sent_at"),
        ),
    )
    bg_id = conn.execute(
        "SELECT id FROM bg_records WHERE rowid = ?", (cursor.lastrowid,)
    ).fetchone()[0]
    return "written", {"id": str(bg_id), "bg_no": bg_no, "loa_id": loa_id}


def apply_extension(conn, loa_id: str | None, new_bg_id: str) -> list[dict]:
    """Close the earlier guarantee this BG extends, never delete it.

    One LOA can cover several guarantees (different corridors, plazas or BG
    types), so LOA alone is not enough. The earlier row must also share the
    contractor, project, plaza and BG type, and it must start before the new
    guarantee.
    """
    if not loa_id:
        return []
    new = conn.execute(
        "SELECT contractor_id, project_id, toll_plaza_id, bg_type, from_date "
        "FROM bg_records WHERE id = ?",
        (new_bg_id,),
    ).fetchone()
    if new is None or not new["from_date"]:
        return []
    rows = conn.execute(
        "SELECT id, bg_no, status, contractor_id, project_id, toll_plaza_id, "
        "bg_type, from_date FROM bg_records "
        "WHERE loa_id = ? AND id <> ? AND status <> 'Closed'",
        (loa_id, new_bg_id),
    ).fetchall()
    scope = (
        new["contractor_id"],
        new["project_id"],
        new["toll_plaza_id"],
        new["bg_type"],
    )
    closing = [
        row
        for row in rows
        if (
            row["contractor_id"],
            row["project_id"],
            row["toll_plaza_id"],
            row["bg_type"],
        )
        == scope
        and row["from_date"]
        and row["from_date"] < new["from_date"]
    ]
    if not closing:
        return []
    today = dt.date.today().isoformat()
    # The status lookup may not seed Closed in a trimmed/test database.
    ensure_status(conn, "Closed")
    for row in closing:
        conn.execute(
            "UPDATE bg_records SET status = 'Closed', extended_date = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (today, row["id"]),
        )
        conn.execute(
            "UPDATE bg_records SET supersedes_bg_id = ? WHERE id = ?", (new_bg_id, row["id"])
        )
    return [{"id": str(row["id"]), "BG No": row["bg_no"]} for row in closing]


# --- corrections after confirmation -------------------------------------

# Tracker column -> how the register stores it. Only these can be corrected;
# BG No is deliberately excluded (it is the identity, and the workbook key).
_UPDATE_TEXT = {
    "Remarks": "remarks",
    "Status": "status",
}
_UPDATE_DATE = {
    "From": "from_date",
    "To": "to_date",
    "Claim Period": "claim_period",
}
_UPDATE_NAME = {
    "Company Name": ("company", "company_id", ensure_company),
    "Bank Name": ("bank", "bank_id", ensure_bank),
    "Nature of Service": ("nature", "nature_id", ensure_nature),
    "Name of Contractor": ("contractor", "contractor_id", ensure_contractor),
}


def update_row(conn, bg_id: str, changes: dict, user_id: str | None = None) -> dict:
    """Correct, extend or close one confirmed row (admin only at the API)."""
    if not changes:
        raise ValueError("Nothing to change.")
    sets: list[str] = []
    params: list = []
    if "Status" in changes:
        status = ensure_status(conn, changes["Status"])
        sets.append("status = ?")
        params.append(status)
    if "Extended" in changes:
        extended = str(changes["Extended"] or "").strip().lower() in {"yes", "y", "true"}
        sets.append("extended_date = ?")
        params.append(dt.date.today().isoformat() if extended else None)
    if "BG Amount" in changes:
        try:
            amount = float(str(changes["BG Amount"]).replace(",", "").strip())
        except (TypeError, ValueError) as error:
            raise ValueError("BG Amount must be a number.") from error
        if amount <= 0:
            raise ValueError("BG Amount must be greater than zero.")
        sets.append("bg_amount = ?")
        params.append(amount)
    for column, target in _UPDATE_TEXT.items():
        if column in changes and column != "Status":
            sets.append(f"{target} = ?")
            params.append(changes[column] or None)
    for column, target in _UPDATE_DATE.items():
        if column in changes:
            sets.append(f"{target} = ?")
            params.append(_iso(changes[column]))
    for column, (_, target, ensure) in _UPDATE_NAME.items():
        if column in changes:
            sets.append(f"{target} = ?")
            params.append(ensure(conn, changes[column]))
    if "Project Name" in changes:
        project_id = ensure_project(conn, changes["Project Name"])
        sets.append("project_id = ?")
        params.append(project_id)
        if "Toll Plaza Name" not in changes:
            # A new corridor invalidates the old plaza; blank rather than guess.
            sets.append("toll_plaza_id = ?")
            params.append(None)
    if "Toll Plaza Name" in changes:
        project_id = ensure_project(conn, changes.get("Project Name"))
        if project_id is None:
            row = conn.execute("SELECT project_id FROM bg_records WHERE id = ?", (bg_id,)).fetchone()
            project_id = row["project_id"] if row else None
        sets.append("toll_plaza_id = ?")
        params.append(ensure_plaza(conn, project_id, changes["Toll Plaza Name"]))
    if "Types of BG" in changes:
        sets.append("bg_type = ?")
        params.append(ensure_bg_type(conn, changes["Types of BG"]))
    if "LOA Number" in changes:
        sets.append("loa_id = ?")
        params.append(ensure_loa(conn, changes["LOA Number"], user_id))
    if "PO Number" in changes:
        sets.append("po_number = ?")
        params.append(str(changes["PO Number"] or "").strip() or None)
    if "To" in changes and "Status" not in changes:
        # Keep the derived status honest when the admin moves the expiry date.
        # Closed/Extended rows keep their explicit status.
        current = conn.execute(
            "SELECT status FROM bg_records WHERE id = ?", (str(bg_id),)
        ).fetchone()
        current_status = str(current["status"] or "") if current else ""
        new_to = _iso(changes["To"])
        if new_to and current_status not in {"Closed", "Extended"}:
            derived = "Expired" if new_to < dt.date.today().isoformat() else "Active"
            sets.append("status = ?")
            params.append(ensure_status(conn, derived))

    if not sets:
        raise ValueError("No supported fields in the request.")
    params.append(str(bg_id))
    try:
        changed = conn.execute(
            f"UPDATE bg_records SET {', '.join(sets)}, updated_at = datetime('now') WHERE id = ?",
            params,
        ).rowcount
    except Exception as error:  # sqlite CHECK (to >= from, claim >= to) and friends
        raise ValueError(f"Could not update the register row: {error}") from error
    if not changed:
        raise LookupError("BG not found")
    return find_existing_by_id(conn, bg_id)


# --- read path -----------------------------------------------------------


def row_to_dict(row) -> dict:
    extended = "Yes" if row["extended_date"] else "No"
    return {
        "id": str(row["id"]),
        "BG No": row["bg_no"],
        "Company Name": row["company"] or "",
        "Project Name": row["project"] or "",
        "Project Label": row["project_name"] or row["project"] or "",
        "Toll Plaza Name": row["plaza"] or "",
        "Types of BG": row["bg_type"] or "",
        "Nature of Service": row["nature"] or "",
        "Name of Contractor": row["contractor"] or "",
        "BG Amount": float(row["bg_amount"] or 0),
        "From": row["from_date"],
        "To": row["to_date"],
        "Claim Period": row["claim_period"],
        "Extended": extended,
        "Bank Name": row["bank"] or "",
        "Status": row["status"],
        "Remarks": row["remarks"] or "",
        "LOA Number": row["loa"] or "",
        "PO Number": row["po"] or "",
        "source_pdf": row["source_pdf"],
        "created_by": row["created_by"] or "",
        "created_at": row["created_at"],
        "reminder_sent_at": row["reminder_sent_at"],
    }


def _where(filters: dict) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    q = (filters.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        clauses.append(
            "(" + " OR ".join(
                f"{column} LIKE ?"
                for column in (
                    "r.bg_no", "ct.name", "p.code", "p.name", "t.name", "c.name",
                    "l.loa_po_number", "r.po_number", "b.name",
                )
            ) + ")"
        )
        params.extend([like] * 9)
    status = (filters.get("status") or "").strip()
    if status:
        clauses.append("r.status = ?")
        params.append(status)
    project = (filters.get("project") or "").strip()
    if project:
        clauses.append("p.code = ?")
        params.append(project)
    created_by = filters.get("created_by")
    if created_by:
        clauses.append("r.created_by = ?")
        params.append(created_by)
    if filters.get("overdue"):
        clauses.append("date(r.to_date) < date('now', 'localtime') AND r.status <> 'Closed'")
    window = filters.get("window")
    if window:
        clauses.append(
            "date(r.to_date) BETWEEN date('now', 'localtime') "
            "AND date('now', 'localtime', ?)"
        )
        params.append(f"+{int(window)} days")
    if filters.get("reminded") in (0, False):
        clauses.append("r.reminder_sent_at IS NULL")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_rows(conn, filters: dict | None = None) -> dict:
    filters = filters or {}
    where, params = _where(filters)
    total = conn.execute(
        "SELECT COUNT(*) FROM bg_records r "
        "LEFT JOIN company c ON c.id = r.company_id "
        "LEFT JOIN project p ON p.id = r.project_id "
        "LEFT JOIN toll_plaza t ON t.id = r.toll_plaza_id "
        "LEFT JOIN contractor ct ON ct.id = r.contractor_id "
        "LEFT JOIN bank b ON b.id = r.bank_id "
        "LEFT JOIN loa_records l ON l.id = r.loa_id" + where,
        params,
    ).fetchone()[0]
    limit = max(1, min(int(filters.get("limit") or 100), 500))
    offset = max(0, int(filters.get("offset") or 0))
    rows = conn.execute(
        f"{_SELECT}{where} ORDER BY date(r.to_date) ASC, r.bg_no ASC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {
        "rows": [row_to_dict(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


def stats(conn, created_by: str | None = None) -> dict:
    """Headline counts for the Register screen."""
    where = " WHERE created_by = ?" if created_by else ""
    params = (created_by,) if created_by else ()
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM bg_records{where} GROUP BY status", params
    ).fetchall()
    by_status = {row["status"]: int(row["n"]) for row in rows}
    total = sum(by_status.values())
    due = conn.execute(
        f"SELECT COUNT(*) FROM bg_records{where} {'AND' if where else 'WHERE'} "
        "date(to_date) BETWEEN date('now', 'localtime') "
        "AND date('now', 'localtime', '+7 days') AND status <> 'Closed'",
        params,
    ).fetchone()[0]
    overdue = conn.execute(
        f"SELECT COUNT(*) FROM bg_records{where} {'AND' if where else 'WHERE'} "
        "date(to_date) < date('now', 'localtime') AND status NOT IN ('Closed','Expired')",
        params,
    ).fetchone()[0]
    return {
        "total": total,
        "by_status": by_status,
        "due_in_7_days": int(due),
        "overdue": int(overdue),
    }


def due_rows(conn, days: int, today: dt.date | None = None) -> list[dict]:
    """BGs whose ``To`` is exactly ``days`` from today and still open."""
    target = (today or dt.date.today()) + dt.timedelta(days=days)
    rows = conn.execute(
        f"{_SELECT} WHERE date(r.to_date) = date(?) AND r.reminder_sent_at IS NULL "
        "AND r.status <> 'Closed' ORDER BY r.bg_no",
        (target.isoformat(),),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def mark_reminder_sent(conn, bg_id: str, when: dt.datetime | None = None) -> None:
    conn.execute(
        "UPDATE bg_records SET reminder_sent_at = ?, updated_at = datetime('now') WHERE id = ?",
        (store.iso(when), bg_id),
    )


def export_bytes(rows: Iterable[dict]) -> bytes:
    """A workbook of the register, independent of the tracker copy."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    headers = list(HEADERS)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    widths = {
        "Company Name": 44, "Project Name": 10, "Toll Plaza Name": 18,
        "Types of BG": 11, "Nature of Service": 22, "Name of Contractor": 30,
        "BG No": 22, "BG Amount": 14, "From": 12, "To": 12, "Claim Period": 12,
        "Extended": 10, "Bank Name": 22, "Status": 10, "Remarks": 24,
        "LOA Number": 34, "PO Number": 30, "source_pdf": 34, "created_by": 14,
        "created_at": 18, "reminder_sent_at": 18,
    }
    for index, name in enumerate(headers, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = (
            widths.get(name, 16)
        )
    date_columns = {"From", "To", "Claim Period"}
    for row in rows:
        line = []
        for name in headers:
            value = row.get(name)
            if name in date_columns and value:
                try:
                    value = dt.date.fromisoformat(str(value)[:10]).strftime("%d-%m-%Y")
                except ValueError:
                    pass
            line.append(value)
        sheet.append(line)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(headers)).column_letter}"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def open_flags_for(bg_no: str) -> dict:
    """Placeholder for the UI's flag column; flags live in the run, not the row."""
    return {}


def normalise_bg_no(bg_no: str) -> str:
    return normalise_key(bg_no)
