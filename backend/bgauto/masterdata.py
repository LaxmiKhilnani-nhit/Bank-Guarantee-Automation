"""Project and toll-plaza masters: parse the operations master document, load it.

The source of truth is the operations team's master document (markdown, kept
outside this repo). It lists SPVs, the corridors under each, and the toll
plazas on every corridor.

The importer is deliberately boring:

* idempotent - running it twice changes nothing and creates no duplicates;
* additive - rows missing from the document are deactivated, never deleted, so a
  BG that already points at them still resolves;
* provenance-aware - imported rows are tagged ``source='imported'`` and rows a
  reviewer created by hand (``source='manual'``) are left alone.

Use it from the CLI::

    python -m bgauto.cli import-masters <file.md> [--db ...] [--dry-run]
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .schema import ensure_schema

_UNESCAPE = re.compile(r"\\([.\[\]()#*_`+>-])")
_COMPANY = re.compile(r"^###\s+(?!#)(.+?)\s*$")
_PROJECT = re.compile(r"^#{5}\s+(.+?)\s*$")
_PLAZA = re.compile(r"^[A-Za-z]\.\s+(.+?)\s*$")
_CHAINAGE = re.compile(r"\[\s*([0-9]+(?:\.[0-9]+)?)\s*\]\s*$")
_NUMBERED = re.compile(r"^\d+\\?\.\s*")
_CODE_SUFFIX = re.compile(r"^(?P<name>.+?)\s*\[(?P<code>[^\]]+)\]\s*$")
_STATE = re.compile(r"^State\s*:\s*(.*)$", re.IGNORECASE)
_ROUND = re.compile(r"^Round\s*:\s*(\d+)", re.IGNORECASE)
_PROJECT_MANAGER = re.compile(r"^Project\s+Manager\s*:\s*(.*)$", re.IGNORECASE)
_PLAZA_HEADING = re.compile(r"^Plaza\s+Names?\s*:?\s*$", re.IGNORECASE)
_MANAGERS = (
    ("toll_manager", re.compile(r"^Toll\s+Man[ae]nger\s*:\s*(.*)$", re.IGNORECASE)),
    ("maintenance_manager", re.compile(r"^Maintenance\s+Manager\s*:\s*(.*)$", re.IGNORECASE)),
    ("corridor_manager", re.compile(r"^Corridor\s+Manager\s*:\s*(.*)$", re.IGNORECASE)),
)


@dataclass
class Plaza:
    name: str
    chainage: str | None = None
    toll_manager: str | None = None
    maintenance_manager: str | None = None
    corridor_manager: str | None = None


@dataclass
class Project:
    name: str
    code: str | None = None
    state: str | None = None
    round: int | None = None
    project_manager: str | None = None
    plazas: list[Plaza] = field(default_factory=list)


@dataclass
class Company:
    name: str
    projects: list[Project] = field(default_factory=list)


@dataclass
class MasterData:
    companies: list[Company] = field(default_factory=list)

    def projects(self) -> list[tuple[Company, Project]]:
        return [(c, p) for c in self.companies for p in c.projects]

    def plaza_count(self) -> int:
        return sum(len(p.plazas) for _, p in self.projects())


def parse_master_markdown(text: str) -> MasterData:
    """Parse the master document. Tolerant of the source's typo and escaping."""
    parsed = MasterData()
    company: Company | None = None
    project: Project | None = None
    plaza: Plaza | None = None
    in_plazas = False

    for raw in text.splitlines():
        line = _UNESCAPE.sub(r"\1", raw).strip()
        if not line:
            continue
        if line.startswith("## "):
            break  # Head Office tables and anything after them
        if line.startswith("#####"):
            found = _PROJECT.match(line)
            if found and company is not None:
                project = _project(found.group(1))
                company.projects.append(project)
                plaza = None
                in_plazas = False
            continue
        if line.startswith("###"):
            found = _COMPANY.match(line)
            if found:
                company = Company(name=found.group(1).strip())
                parsed.companies.append(company)
                project = None
                plaza = None
                in_plazas = False
            continue
        if line.startswith("#"):
            continue
        if project is None:
            continue

        if _PLAZA_HEADING.match(line):
            in_plazas = True
            continue
        state = _STATE.match(line)
        if state:
            project.state = state.group(1).strip() or None
            continue
        round_no = _ROUND.match(line)
        if round_no:
            project.round = int(round_no.group(1))
            continue
        manager = _PROJECT_MANAGER.match(line)
        if manager:
            project.project_manager = manager.group(1).strip() or None
            continue
        if line.lower().startswith("no. of plaza"):
            in_plazas = True
            continue
        for attribute, pattern in _MANAGERS:
            found = pattern.match(line)
            if found and plaza is not None:
                setattr(plaza, attribute, found.group(1).strip() or None)
                break
        else:
            found = _PLAZA.match(line)
            if found and in_plazas:
                plaza = _plaza(found.group(1))
                project.plazas.append(plaza)
    return parsed


def _project(heading: str) -> Project:
    heading = _NUMBERED.sub("", heading).strip()
    labelled = _CODE_SUFFIX.match(heading)
    if labelled:
        return Project(name=labelled.group("name").strip(), code=labelled.group("code").strip())
    return Project(name=heading)


def _plaza(text: str) -> Plaza:
    chainage = _CHAINAGE.search(text)
    name = _CHAINAGE.sub("", text).strip()
    return Plaza(name=name, chainage=chainage.group(1) if chainage else None)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def import_master_data(
    source: Path | str,
    db_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Upsert ``source`` into the masters database and return a change report."""
    source = Path(source)
    parsed = parse_master_markdown(source.read_text(encoding="utf-8"))
    path = Path(db_path or config.DB_PATH)
    ensure_schema(path)

    report: dict = {
        "source": str(source),
        "dry_run": dry_run,
        "companies": {"created": [], "updated": []},
        "projects": {"created": [], "updated": [], "deactivated": []},
        "plazas": {"created": [], "updated": [], "deactivated": []},
    }

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        keep_projects: list[int] = []
        for company in parsed.companies:
            company_id = _upsert_company(conn, company, report)
            for project in company.projects:
                project_id = _upsert_project(conn, company_id, project, report)
                keep_projects.append(project_id)
                keep_plazas: list[int] = []
                for plaza in project.plazas:
                    keep_plazas.append(_upsert_plaza(conn, project_id, plaza, report))
                _deactivate_plazas(conn, project_id, keep_plazas, report)

        _deactivate_projects(conn, keep_projects, report)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return report


def _upsert_company(conn: sqlite3.Connection, company: Company, report: dict) -> int:
    row = conn.execute(
        "SELECT id, source FROM company WHERE lower(name) = lower(?)", (company.name,)
    ).fetchone()
    if row:
        company_id, source = row
        if source != "imported":
            conn.execute(
                "UPDATE company SET source = 'imported', is_active = 1 WHERE id = ?",
                (company_id,),
            )
            report["companies"]["updated"].append(company.name)
        return company_id
    cursor = conn.execute(
        "INSERT INTO company (name, source, is_active) VALUES (?, 'imported', 1)",
        (company.name,),
    )
    report["companies"]["created"].append(company.name)
    return int(cursor.lastrowid)


def _upsert_project(
    conn: sqlite3.Connection, company_id: int, project: Project, report: dict
) -> int:
    code = project.code or project.name
    row = conn.execute(
        "SELECT id, name, company_id, state, \"round\", project_manager FROM project "
        "WHERE lower(code) = lower(?)",
        (code,),
    ).fetchone()
    wanted = (
        project.name,
        company_id,
        project.state,
        project.round,
        project.project_manager,
    )
    if row:
        project_id = row[0]
        current = tuple(row[1:])
        if current != wanted:
            conn.execute(
                "UPDATE project SET name = ?, company_id = ?, state = ?, \"round\" = ?, "
                "project_manager = ?, source = 'imported', is_active = 1 WHERE id = ?",
                (*wanted, project_id),
            )
            report["projects"]["updated"].append(code)
        elif not _is_active(conn, "project", project_id):
            conn.execute("UPDATE project SET is_active = 1 WHERE id = ?", (project_id,))
            report["projects"]["updated"].append(code)
        return project_id
    cursor = conn.execute(
        "INSERT INTO project (code, name, company_id, state, \"round\", project_manager, "
        "source, is_active) VALUES (?, ?, ?, ?, ?, ?, 'imported', 1)",
        (code, *wanted),
    )
    report["projects"]["created"].append(code)
    return int(cursor.lastrowid)


def _upsert_plaza(
    conn: sqlite3.Connection, project_id: int, plaza: Plaza, report: dict
) -> int:
    row = conn.execute(
        "SELECT id, chainage, toll_manager, maintenance_manager, corridor_manager "
        "FROM toll_plaza WHERE project_id = ? AND lower(trim(name)) = lower(?)",
        (project_id, plaza.name),
    ).fetchone()
    wanted = (
        plaza.chainage,
        plaza.toll_manager,
        plaza.maintenance_manager,
        plaza.corridor_manager,
    )
    if row:
        plaza_id = row[0]
        if tuple(row[1:]) != wanted:
            conn.execute(
                "UPDATE toll_plaza SET chainage = ?, toll_manager = ?, "
                "maintenance_manager = ?, corridor_manager = ?, source = 'imported', "
                "is_active = 1 WHERE id = ?",
                (*wanted, plaza_id),
            )
            report["plazas"]["updated"].append(plaza.name)
        elif not _is_active(conn, "toll_plaza", plaza_id):
            conn.execute("UPDATE toll_plaza SET is_active = 1 WHERE id = ?", (plaza_id,))
            report["plazas"]["updated"].append(plaza.name)
        return plaza_id
    cursor = conn.execute(
        "INSERT INTO toll_plaza (project_id, name, chainage, toll_manager, "
        "maintenance_manager, corridor_manager, source, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, 'imported', 1)",
        (project_id, plaza.name, *wanted),
    )
    report["plazas"]["created"].append(plaza.name)
    return int(cursor.lastrowid)


def _is_active(conn: sqlite3.Connection, table: str, row_id: int) -> bool:
    row = conn.execute(f"SELECT is_active FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return bool(row and row[0])


def _deactivate_plazas(
    conn: sqlite3.Connection, project_id: int, keep: list[int], report: dict
) -> None:
    placeholders = ",".join("?" for _ in keep) or "NULL"
    stale = conn.execute(
        "SELECT id, name FROM toll_plaza WHERE project_id = ? AND is_active = 1 "
        f"AND id NOT IN ({placeholders})",
        (project_id, *keep),
    ).fetchall()
    if not stale:
        return
    conn.execute(
        "UPDATE toll_plaza SET is_active = 0 "
        f"WHERE project_id = ? AND id NOT IN ({placeholders})",
        (project_id, *keep),
    )
    report["plazas"]["deactivated"].extend(name for _, name in stale)


def _deactivate_projects(conn: sqlite3.Connection, keep: list[int], report: dict) -> None:
    placeholders = ",".join("?" for _ in keep) or "NULL"
    stale = [
        row[0]
        for row in conn.execute(
            f"SELECT code FROM project WHERE is_active = 1 AND id NOT IN ({placeholders}) "
            "AND (source IS NULL OR source != 'manual')",
            tuple(keep),
        )
    ]
    if not stale:
        return
    conn.execute(
        "UPDATE project SET is_active = 0 "
        f"WHERE id NOT IN ({placeholders}) AND (source IS NULL OR source != 'manual')",
        tuple(keep),
    )
    report["projects"]["deactivated"].extend(stale)
