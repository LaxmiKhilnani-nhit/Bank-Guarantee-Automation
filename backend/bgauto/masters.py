"""Dropdown masters, read from the SQLite database when available."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import config

FALLBACK = {
    "banks": [],
    "companies": [
        "NHIT Eastern Projects Private Limited",
        "NHIT Southern Projects Private Limited",
        "NHIT Western Projects Private Limited",
    ],
    "projects": ["APA", "APTNK", "BS", "CM", "GDK", "GMP", "MH", "NR", "RH", "RS", "SB"],
    "natures": [
        "ATMS", "ATMS Building", "BESS_Bahadrabad Toll Plaza", "Incident Management",
        "Initial Improvement", "Manpower", "Operation", "Rehabilitation of bridge",
        "Repair & rehabilititation", "Road Marking", "Signboard Installation", "TMS",
        "Routine Maintenance", "Supply & Installation of Overhead Gantry",
    ],
    "contractors": [],
    "statuses": ["Draft", "Active", "Expiring", "Expired", "Extended", "Closed"],
    "toll_plazas": [],
    "project_options": [],
}


# Short forms seen in BG beneficiaries. The company master stores the full legal
# SPV name, so OCR text is only an alias source: ``NSPPL`` is never a company of
# its own.
COMPANY_ALIASES = {
    "NSPPL": "NHIT Southern Projects Private Limited",
    "NEPPL": "NHIT Eastern Projects Private Limited",
    "NWPPL": "NHIT Western Projects Private Limited",
    "NHIT SOUTHERN": "NHIT Southern Projects Private Limited",
    "NHIT EASTERN": "NHIT Eastern Projects Private Limited",
    "NHIT WESTERN": "NHIT Western Projects Private Limited",
}


def company_from_text(text: str | None) -> str | None:
    """Canonical company named in OCR text, following the alias table.

    More than one distinct company in the text is ambiguous, so it returns
    ``None`` instead of guessing the first alias.
    """
    upper = " ".join(str(text or "").upper().split())
    matches = {
        company
        for alias, company in COMPANY_ALIASES.items()
        if alias in upper
    }
    return matches.pop() if len(matches) == 1 else None


@dataclass
class Masters:
    banks: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    natures: list[str] = field(default_factory=list)
    contractors: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    # [{"project": "BS", "name": "Faridpur", "chainage": "157.59"}]
    toll_plazas: list[dict] = field(default_factory=list)
    # [{"code": "BS", "name": "Bareilly-Sitapur", "company": "...", "state": "..."}]
    project_options: list[dict] = field(default_factory=list)

    def project_name(self, code: str | None) -> str | None:
        for option in self.project_options:
            if code and option.get("code", "").lower() == str(code).lower():
                return option.get("name")
        return None

    def company_for_project(self, code: str | None) -> str | None:
        """Full legal company that owns ``code``, from the project master."""
        for option in self.project_options:
            if code and option.get("code", "").lower() == str(code).lower():
                return option.get("company") or None
        return None

    @classmethod
    def load(cls, db_path: Path | None = None) -> "Masters":
        db_path = db_path or config.DB_PATH
        if not db_path.exists():
            return cls(**FALLBACK)

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            def rows(sql: str) -> list[tuple]:
                try:
                    return list(conn.execute(sql))
                except sqlite3.Error:
                    return []

            def column(sql: str) -> list[str]:
                return [r[0] for r in rows(sql) if r[0]]

            project_options = [
                {"code": code, "name": name or code, "company": company or "", "state": state or ""}
                for code, name, company, state in rows(
                    "SELECT p.code, p.name, c.name, p.state FROM project p "
                    "LEFT JOIN company c ON c.id = p.company_id "
                    "WHERE COALESCE(p.is_active, 1) = 1 ORDER BY p.code"
                )
                if code
            ]
            loaded = cls(
                banks=column("SELECT name FROM bank ORDER BY name"),
                companies=column(
                    "SELECT name FROM company WHERE COALESCE(is_active, 1) = 1 ORDER BY name"
                ),
                projects=column(
                    "SELECT code FROM project WHERE COALESCE(is_active, 1) = 1 ORDER BY code"
                ),
                natures=column("SELECT name FROM nature_of_service ORDER BY name"),
                contractors=column("SELECT name FROM contractor ORDER BY name"),
                # bg_status has no id column; rowid keeps the seeded order.
                statuses=column("SELECT code FROM bg_status ORDER BY rowid"),
                toll_plazas=[
                    {"project": project, "name": name, "chainage": chainage or ""}
                    for project, name, chainage in rows(
                        "SELECT p.code, t.name, t.chainage FROM toll_plaza t "
                        "JOIN project p ON p.id = t.project_id "
                        "WHERE COALESCE(t.is_active, 1) = 1 AND COALESCE(p.is_active, 1) = 1 "
                        "ORDER BY p.code, t.name"
                    )
                    if name
                ],
                project_options=project_options,
            )
        finally:
            # The context manager only commits; the handle must be closed or
            # Windows keeps the file locked (and the API leaks one per reload).
            conn.close()

        # An empty table should not blank out a working dropdown.
        for name, value in vars(loaded).items():
            if not value and name in FALLBACK:
                setattr(loaded, name, FALLBACK[name])
        if not loaded.project_options:
            loaded.project_options = [
                {"code": code, "name": code, "company": "", "state": ""}
                for code in loaded.projects
            ]
        return loaded
