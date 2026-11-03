"""CLI entry point for local BG ingestion.

Usage (from the repo root, with the project venv):

    .venv\\Scripts\\python.exe -m bgauto.cli ingest "Sample_Bank_Guarantees/*.pdf" ^
        --out "out/BG Tracker_ingested.xlsx" --report out/review

The command is deliberately non-destructive: it appends to a copy of the tracker
so an admin can review and confirm. Nothing is written to the real tracker.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sqlite3
import sys
from pathlib import Path

from . import config, excel, ocr
from .extract import HEADERS, Extraction, extract
from .masters import Masters
from .normalize import normalise_key
from .schema import ensure_schema

CSV_COLUMNS = [
    "source_pdf", "BG No", "BG Amount", "From", "To", "Claim Period",
    "Name of Contractor", "Bank Name", "Types of BG", "Nature of Service",
    "Status", "LOA Number", "PO Number", "ocr_mean_confidence", "flags", "notes",
]


def _expand(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        matches = [Path(p) for p in glob.glob(pattern)]
        if not matches and Path(pattern).is_file():
            matches = [Path(pattern)]
        files.extend(sorted(matches))
    return [p for p in dict.fromkeys(files) if p.suffix.lower() == ".pdf"]


def _archive(extraction: Extraction, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{extraction.source.stem}.json"
    payload = {
        "source_pdf": extraction.source.name,
        "engine": "tesseract-local",
        "ocr": {
            "mean_confidence": extraction.ocr.mean_conf,
            "pages": [
                {"page": p.number, "mean_confidence": p.mean_conf, "text": p.text}
                for p in extraction.ocr.pages
            ],
        },
        "extraction": extraction.report(),
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def _flags(extraction: Extraction) -> str:
    parts = []
    for name, f in extraction.fields.items():
        if f.flags:
            parts.append(f"{name}: {'|'.join(f.flags)}")
    return "; ".join(parts)


def _notes(extraction: Extraction) -> str:
    parts = []
    for name, f in extraction.fields.items():
        if f.notes:
            parts.append(f"{name}: {'|'.join(f.notes)}")
    return "; ".join(parts)


def _print_summary(extractions: list[Extraction]) -> None:
    for ex in extractions:
        print("")
        print(f"PDF   : {ex.source.name}  ({len(ex.ocr.pages)} pages, OCR conf {ex.ocr.mean_conf:.2%})")
        for name in (
            "Company Name", "Name of Contractor", "BG No", "BG Amount", "From", "To",
            "Claim Period", "Bank Name", "Types of BG", "Nature of Service",
            "LOA Number", "PO Number", "Status",
        ):
            f = ex.fields.get(name)
            if not f:
                continue
            value = f.value.isoformat() if hasattr(f.value, "isoformat") else f.value
            marker = "ok " if f.value not in (None, "") and not f.flags else "!! "
            note = f"  [{', '.join(f.flags)}]" if f.flags else ""
            hints = f"  ({', '.join(f.notes)})" if f.notes else ""
            print(f"  {marker}{name:<20} {value}{note}{hints}")


def cmd_ingest(args: argparse.Namespace) -> int:
    files = _expand(args.pdfs)
    if not files:
        print("No PDF files matched.", file=sys.stderr)
        return 2

    ocr.configure()
    db_path = Path(args.db) if args.db else None
    try:
        ensure_schema(db_path)
    except sqlite3.Error:
        pass  # keep going with whatever the database already has
    masters = Masters.load(db_path)

    out_path = Path(args.out)
    template = Path(args.tracker)
    excel.prepare_workbook(template, out_path)
    wb, ws = excel.open_tracker(out_path)
    known = excel.existing_keys(ws)

    extractions: list[Extraction] = []
    for pdf_path in files:
        print(f"OCR   : {pdf_path.name} ...", flush=True)
        document = ocr.ocr_pdf(pdf_path, dpi=args.dpi)
        result = extract(document, masters)
        extractions.append(result)
        archive = _archive(result, Path(args.archive_dir))
        print(f"        archived OCR -> {archive}")

    rows = []
    reported = []
    for result in extractions:
        key = result.value("BG No")
        report = result.report()
        report["flags_summary"] = _flags(result)
        report["notes_summary"] = _notes(result)
        if key and normalise_key(str(key)) in known:
            report["warnings"].append("duplicate_bg_no_skipped")
            print(f"SKIP  : {result.source.name} -> duplicate BG No {key}")
        else:
            if key:
                known.add(normalise_key(str(key)))
            rows.append(result.row())
        reported.append(report)

    for row in rows:
        excel.append_row(ws, row)
    excel.finalise(ws)
    excel.save(wb, out_path)

    report_dir = Path(args.report)
    report_dir.mkdir(parents=True, exist_ok=True)
    for report, result in zip(reported, extractions):
        (report_dir / f"{result.source.stem}.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    with (report_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for result in extractions:
            writer.writerow({
                "source_pdf": result.source.name,
                "BG No": result.value("BG No"),
                "BG Amount": result.value("BG Amount"),
                "From": result.value("From"),
                "To": result.value("To"),
                "Claim Period": result.value("Claim Period"),
                "Name of Contractor": result.value("Name of Contractor"),
                "Bank Name": result.value("Bank Name"),
                "Types of BG": result.value("Types of BG"),
                "Nature of Service": result.value("Nature of Service"),
                "Status": result.value("Status"),
                "LOA Number": result.value("LOA Number"),
                "PO Number": result.value("PO Number"),
                "ocr_mean_confidence": result.ocr.mean_conf,
                "flags": _flags(result),
                "notes": _notes(result),
            })

    _print_summary(extractions)
    print("")
    print(f"Rows written : {len(rows)} -> {out_path}")
    print(f"Review files : {report_dir}")
    print(f"OCR archive  : {Path(args.archive_dir)}")
    print("Reminder     : confirm these rows before they go into the real tracker.")
    return 0


def cmd_import_masters(args) -> int:
    from .masterdata import import_master_data
    from .schema import ensure_schema

    source = Path(args.file)
    if not source.is_file():
        print(f"ERROR: {source} not found")
        return 2
    db = Path(args.db) if args.db else None
    added = ensure_schema(db)
    if added:
        print(f"schema+: {', '.join(added)}")
    report = import_master_data(source, db, dry_run=args.dry_run)
    for group in ("companies", "projects", "plazas"):
        counts = {
            key: len(value)
            for key, value in report[group].items()
            if isinstance(value, list)
        }
        print(f"{group:9}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for key in ("projects", "plazas"):
        gone = report[key]["deactivated"]
        if gone:
            print(f"deactivated {key}: {', '.join(gone)}")
    if args.dry_run:
        print("dry run: nothing committed")
    else:
        print(f"masters now: {db or config.DB_PATH}")
    return 0


def cmd_backup(args) -> int:
    from .backup import create_backup, restore_hint

    try:
        report = create_backup(backup_dir=Path(args.dir) if args.dir else None, keep=args.keep)
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"backup : {report['archive']}")
    print(f"files  : {report['files']} ({report['bytes'] / 1024:.1f} KB)")
    print(f"kept   : {report['kept']} archive(s)")
    if report["removed"]:
        print(f"pruned : {', '.join(report['removed'])}")
    print("")
    print(restore_hint(Path(report["archive"])))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bgauto", description="Local BG PDF ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="OCR PDFs and append rows to a review workbook")
    ingest.add_argument("pdfs", nargs="+", help="PDF paths or glob patterns")
    ingest.add_argument("--out", default="out/BG Tracker_ingested.xlsx", help="review workbook path")
    ingest.add_argument("--report", default="out/review", help="directory for JSON/CSV review files")
    ingest.add_argument("--tracker", default=str(config.TRACKER_PATH), help="tracker template")
    ingest.add_argument("--archive-dir", default=str(config.OCR_ARCHIVE_DIR), help="OCR JSON archive")
    ingest.add_argument("--db", default=None, help="SQLite masters database")
    ingest.add_argument("--dpi", type=int, default=None, help="render DPI (default 300)")
    ingest.set_defaults(func=cmd_ingest)

    masters = sub.add_parser(
        "import-masters", help="Load project and toll-plaza masters from the markdown doc"
    )
    masters.add_argument("file", help="master document (markdown)")
    masters.add_argument("--db", default=None, help="SQLite masters database")
    masters.add_argument("--dry-run", action="store_true", help="report changes without writing")
    masters.set_defaults(func=cmd_import_masters)

    backup = sub.add_parser("backup", help="Zip the register, workbook, template and PDFs")
    backup.add_argument("--dir", default=None, help="destination (default data/backups)")
    backup.add_argument("--keep", type=int, default=7, help="archives to keep (default 7)")
    backup.set_defaults(func=cmd_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
