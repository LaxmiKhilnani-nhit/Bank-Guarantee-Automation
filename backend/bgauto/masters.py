"""Dropdown masters, read from PostgreSQL when available."""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from . import store

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
    def load(cls) -> "Masters":
        try:
            with store.connect() as conn:
                def rows(sql: str, params: tuple = ()) -> list[dict]:
                    return conn.execute(sql, params).fetchall()

                def column(sql: str) -> list[str]:
                    return [r["value"] for r in rows(sql) if r["value"]]

                project_options = [
                    {
                        "code": row["code"],
                        "name": row["name"] or row["code"],
                        "company": row["company"] or "",
                        "state": row["state"] or "",
                    }
                    for row in rows(
                        "SELECT p.code, p.name, c.name AS company, p.state "
                        "FROM project p LEFT JOIN company c ON c.id = p.company_id "
                        "WHERE p.is_active IS NOT FALSE ORDER BY p.code"
                    )
                    if row["code"]
                ]
                loaded = cls(
                    banks=column("SELECT name AS value FROM bank ORDER BY name"),
                    companies=column(
                        "SELECT name AS value FROM company "
                        "WHERE is_active IS NOT FALSE ORDER BY name"
                    ),
                    projects=column(
                        "SELECT code AS value FROM project "
                        "WHERE is_active IS NOT FALSE ORDER BY code"
                    ),
                    natures=column(
                        "SELECT name AS value FROM nature_of_service ORDER BY name"
                    ),
                    contractors=column(
                        "SELECT name AS value FROM contractor ORDER BY name"
                    ),
                    statuses=column(
                        "SELECT code AS value FROM bg_status "
                        "ORDER BY sort_order, code"
                    ),
                    toll_plazas=[
                        {
                            "project": row["project"],
                            "name": row["name"],
                            "chainage": row["chainage"] or "",
                        }
                        for row in rows(
                            "SELECT p.code AS project, t.name, t.chainage "
                            "FROM toll_plaza t JOIN project p ON p.id = t.project_id "
                            "WHERE t.is_active IS NOT FALSE AND p.is_active IS NOT FALSE "
                            "ORDER BY p.code, t.name"
                        )
                        if row["name"]
                    ],
                    project_options=project_options,
                )
        except (psycopg.Error, RuntimeError, OSError):
            return cls(**FALLBACK)

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
