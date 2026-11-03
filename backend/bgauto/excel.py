"""Append confirmed-shape rows to a copy of ``BG Tracker.xlsx``.

The real tracker is never touched by this script: the app writes to a review
workbook after the uploader checks the extracted fields.
"""
from __future__ import annotations

import datetime as dt
import shutil
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from .extract import HEADERS
from .normalize import normalise_key

DATE_COLUMNS = {"From", "To", "Claim Period", "created_at", "reminder_sent_at"}
AMOUNT_COLUMNS = {"BG Amount"}
DATE_FORMAT = "DD-MM-YYYY"
DATETIME_FORMAT = "DD-MM-YYYY HH:MM"


def prepare_workbook(template: Path, out_path: Path) -> Path:
    """Copy the tracker template so the original stays untouched."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        shutil.copy2(template, out_path)
    return out_path


def open_tracker(path: Path) -> tuple:
    wb = load_workbook(path)
    ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.active
    header = [str(c.value).strip() if c.value else "" for c in ws[1]]
    # Older tracker copies used a combined LOA/PO cell. Preserve its existing
    # values as LOA and add a blank PO column without discarding workbook rows.
    if "LOA/PO Number" in header:
        col = header.index("LOA/PO Number") + 1
        ws.cell(1, col).value = "LOA Number"
        ws.insert_cols(col + 1)
        ws.cell(1, col + 1).value = "PO Number"
        target_letter = ws.cell(1, col + 1).column_letter
        ws.column_dimensions[target_letter].width = 30
        for row_number in range(1, ws.max_row + 1):
            source = ws.cell(row_number, col)
            target = ws.cell(row_number, col + 1)
            if source.has_style:
                target._style = copy(source._style)
            if source.number_format:
                target.number_format = source.number_format
            if source.alignment:
                target.alignment = copy(source.alignment)
        header = [str(c.value).strip() if c.value else "" for c in ws[1]]
    if header[: len(HEADERS)] != HEADERS:
        raise ValueError(
            "Tracker header does not match the current format. "
            f"Found: {header[:len(HEADERS)]}"
        )
    return wb, ws


def existing_keys(ws: Worksheet) -> set[str]:
    bg_col = HEADERS.index("BG No") + 1
    keys: set[str] = set()
    for row in ws.iter_rows(min_row=2, min_col=bg_col, max_col=bg_col, values_only=True):
        value = row[0]
        if value:
            keys.add(normalise_key(str(value)))
    return keys


def row_key(row: dict) -> str:
    """A BG can legitimately appear once per toll plaza (multi-plaza guarantee)."""
    bg_key = normalise_key(str(row.get("BG No") or ""))
    plaza_key = normalise_key(str(row.get("Toll Plaza Name") or ""))
    return f"{bg_key}|{plaza_key}"


def existing_row_keys(ws: Worksheet) -> set[str]:
    bg_col = HEADERS.index("BG No") + 1
    plaza_col = HEADERS.index("Toll Plaza Name") + 1
    keys: set[str] = set()
    for row in ws.iter_rows(min_row=2, max_col=len(HEADERS), values_only=True):
        bg_no = row[bg_col - 1]
        if bg_no:
            keys.add(row_key({"BG No": bg_no, "Toll Plaza Name": row[plaza_col - 1]}))
    return keys


def _next_row(ws: Worksheet) -> int:
    for row in range(2, ws.max_row + 2):
        if all(ws.cell(row=row, column=col).value in (None, "") for col in range(1, len(HEADERS) + 1)):
            return row
    return ws.max_row + 1


def append_row(ws: Worksheet, row: dict) -> int:
    target = _next_row(ws)
    for col, name in enumerate(HEADERS, start=1):
        value = row.get(name)
        cell = ws.cell(row=target, column=col)
        if isinstance(value, dt.datetime):
            cell.value = value
            cell.number_format = DATETIME_FORMAT
        elif isinstance(value, dt.date):
            cell.value = value
            cell.number_format = DATE_FORMAT
        elif name in AMOUNT_COLUMNS and isinstance(value, (int, float)):
            cell.value = value
            cell.number_format = "#,##0.00"
        else:
            cell.value = value
        cell.alignment = Alignment(vertical="top", wrap_text=name in {"Remarks", "LOA Number", "PO Number"})
    return target


def find_row_by_bg_no(ws: Worksheet, bg_no) -> int | None:
    """The workbook row for a BG number, or None. Same normalisation as dedupe."""
    column = HEADERS.index("BG No") + 1
    key = normalise_key(str(bg_no))
    if not key:
        return None
    for row in range(2, ws.max_row + 1):
        value = ws.cell(row=row, column=column).value
        if value and normalise_key(str(value)) == key:
            return row
    return None


def update_row_by_bg_no(ws: Worksheet, bg_no, changes: dict) -> int | None:
    """Correct an existing row in place (extend/close/relabel). Never deletes."""
    target = find_row_by_bg_no(ws, bg_no)
    if target is None:
        return None
    for name, value in changes.items():
        if name not in HEADERS:
            continue
        cell = ws.cell(row=target, column=HEADERS.index(name) + 1)
        if isinstance(value, dt.datetime):
            cell.value = value
            cell.number_format = DATETIME_FORMAT
        elif isinstance(value, dt.date):
            cell.value = value
            cell.number_format = DATE_FORMAT
        elif name in AMOUNT_COLUMNS and isinstance(value, (int, float)):
            cell.value = value
            cell.number_format = "#,##0.00"
        else:
            cell.value = value
    return target


def finalise(ws: Worksheet) -> None:
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=len(HEADERS)).coordinate}"
    widths = {
        "Company Name": 14, "Project Name": 12, "Toll Plaza Name": 18,
        "Types of BG": 11, "Nature of Service": 22, "Name of Contractor": 30,
        "BG No": 22, "BG Amount": 14, "From": 12, "To": 12, "Claim Period": 12,
        "Extended": 12, "Bank Name": 22, "Status": 10, "Remarks": 24,
        "LOA Number": 34, "PO Number": 30, "source_pdf": 34, "created_by": 14,
        "created_at": 18, "reminder_sent_at": 18,
    }
    for col, name in enumerate(HEADERS, start=1):
        letter = ws.cell(row=1, column=col).column_letter
        ws.column_dimensions[letter].width = widths.get(name, 16)


def save(wb, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
