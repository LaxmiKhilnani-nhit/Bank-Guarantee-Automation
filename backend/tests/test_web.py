"""HTTP surface: sessions, the role matrix, confirm, register and audit."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from bgauto import auth, config, excel, pipeline, register, store
from bgauto.extract import HEADERS
from bgauto.ocr import DocumentOCR, PageOCR
from bgauto.web import api, runs
from support import TempDatabase

ADMIN = ("admin", "Admin#12345")
UPLOADER = ("asha", "Uploader#123")
VIEWER = ("vik", "Viewer#12345")


def entry_for(bg_no: str = "WEB001", **overrides) -> dict:
    values = {
        "Company Name": "Example Eastern Projects Private Limited",
        "Project Name": "AB",
        "Toll Plaza Name": "First Plaza",
        "Name of Contractor": "Example Infra Limited",
        "Types of BG": "PBG",
        "Nature of Service": "Repair & Rehabilitation",
        "BG No": bg_no,
        "BG Amount": 4078080.0,
        "Bank Name": "HDFC Bank",
        "From": "2025-01-01",
        "To": "2025-12-31",
        "Claim Period": "2026-03-31",
        "Status": "Active",
        "Extended": "No",
        "Remarks": "",
        "LOA Number": "LOA/WEB/001",
        "PO Number": "",
        "source_pdf": f"{bg_no}.pdf",
        "created_by": "",
        "created_at": "",
        "reminder_sent_at": None,
    }
    values.update(overrides)
    return {
        "source_pdf": values["source_pdf"],
        "stored_pdf": f"uploads/{values['source_pdf']}",
        "sha256": bg_no,
        "pages": 1,
        "ocr_confidence": 0.97,
        "extracted_at": "2026-01-01T10:00:00",
        "fields": {name: {"flags": [], "notes": [], "confidence": 0.97} for name in HEADERS},
        "values": values,
        "extracted_values": dict(values),
    }


class WebTests(TempDatabase):
    def setUp(self):
        super().setUp()
        self.bootstrap_admin()
        with store.connect() as conn:
            auth.create_user(conn, *UPLOADER[:1], UPLOADER[1], "uploader")
            auth.create_user(conn, *VIEWER[:1], VIEWER[1], "viewer")
            self.admin = auth.get_user(
                conn,
                conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"],
            )
            self.uploader = auth.get_user(
                conn,
                conn.execute("SELECT id FROM users WHERE username='asha'").fetchone()["id"],
            )
            self.viewer = auth.get_user(
                conn,
                conn.execute("SELECT id FROM users WHERE username='vik'").fetchone()["id"],
            )

    def client(self, credentials=ADMIN) -> TestClient:
        client = TestClient(api.app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        response = client.post(
            "/api/login", json={"username": credentials[0], "password": credentials[1]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("bg_session", response.cookies)
        return client

    def run_with(self, owner, *entries) -> str:
        manifest = runs.create_run(owner)
        manifest["files"] = list(entries)
        runs.save_manifest(manifest["run_id"], manifest)
        return manifest["run_id"]

    def confirm(self, client, run_id, entries, include_flagged=False):
        return client.post(
            f"/api/runs/{run_id}/confirm",
            json={
                "include_flagged": include_flagged,
                "rows": [
                    {"source_pdf": entry["source_pdf"], "values": entry["values"]}
                    for entry in entries
                ],
            },
        )

    def test_unauthenticated_requests_are_401(self):
        client = TestClient(api.app)
        self.assertEqual(client.get("/api/me").status_code, 401)
        self.assertEqual(client.get("/api/register").status_code, 401)
        self.assertEqual(client.post("/api/runs").status_code, 401)
        probe = client.get("/api/session")
        self.assertEqual(probe.status_code, 200)
        self.assertFalse(probe.json()["authenticated"])
        self.assertIsNone(probe.json()["user"])

    def test_health_reports_datalab_configuration(self):
        client = TestClient(api.app)
        with patch.object(api.ocr, "datalab_available", return_value=True):
            payload = client.get("/api/health").json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["datalab"]["configured"])
        self.assertEqual(payload["ocr_engine"], "datalab")
        self.assertEqual(payload["ocr_mode"], config.DATALAB_MODE)
        self.assertEqual(payload["pipeline"]["id"], config.DATALAB_PIPELINE_ID)
        self.assertEqual(
            payload["pipeline"]["version"], config.DATALAB_PIPELINE_VERSION
        )
        self.assertEqual(
            payload["pipeline"]["extraction_mode"],
            config.DATALAB_EXTRACTION_MODE,
        )

    def _pipeline_result(self) -> pipeline.PipelineResult:
        document = DocumentOCR(
            source=Path("upload.pdf"),
            pages=[PageOCR(number=1, width=0, height=0, text="BANK GUARANTEE")],
        )
        return pipeline.PipelineResult(
            document=document,
            data={
                "company_name": "NHIT Southern Projects Private Limited",
                "bg_no": "1234AB12345678901",
                "bg_amount": 1000,
                "from_date": "01-04-2025",
                "to_date": "31-03-2026",
                "authorized_signatories": [
                    {"name": "A. Signatory", "sol_id": "880919"}
                ],
            },
            execution_id="pex_TEST",
            pipeline_id="pl_TEST",
            pipeline_version=3,
            extraction_mode="turbo",
            page_count=3,
            raw_convert={"page_count": 3},
            raw_extract={"success": True},
        )

    def test_upload_runs_the_pipeline_and_archives_both_steps(self):
        uploader = self.client(UPLOADER)
        manifest = runs.create_run(self.uploader)
        run_id = manifest["run_id"]
        result = self._pipeline_result()
        with (
            patch.object(runs.pipeline, "run", return_value=result),
            patch.object(runs.lookup, "lookup_entry", return_value={}),
        ):
            response = uploader.post(
                f"/api/runs/{run_id}/upload",
                files={
                    "file": (
                        "upload.pdf",
                        b"%PDF-1.4\n% test pdf\n",
                        "application/pdf",
                    )
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["duplicate"])
        self.assertEqual(payload["pages"], 3)
        self.assertEqual(payload["fields"]["BG No"]["value"], "1234AB12345678901")
        self.assertEqual(
            payload["extras"]["authorized_signatories"][0]["name"], "A. Signatory"
        )
        self.assertEqual(payload["pipeline"]["version"], 3)
        self.assertEqual(payload["pipeline"]["execution_id"], "pex_TEST")

        run_path = config.RUNS_DIR / run_id
        self.assertTrue((run_path / "ocr" / "upload.json").is_file())
        raw = run_path / "pipeline" / "upload.json"
        self.assertTrue(raw.is_file())
        self.assertIn("convert", raw.read_text(encoding="utf-8"))

    def test_failed_pipeline_upload_is_rejected_and_removes_the_pdf(self):
        uploader = self.client(UPLOADER)
        manifest = runs.create_run(self.uploader)
        run_id = manifest["run_id"]
        with patch.object(
            runs.pipeline, "run", side_effect=RuntimeError("pipeline down")
        ):
            response = uploader.post(
                f"/api/runs/{run_id}/upload",
                files={
                    "file": (
                        "broken.pdf",
                        b"%PDF-1.4\n% test pdf\n",
                        "application/pdf",
                    )
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("pipeline down", response.json()["detail"])
        self.assertFalse((config.RUNS_DIR / run_id / "uploads" / "broken.pdf").exists())

    def test_wrong_password_is_refused(self):
        client = TestClient(api.app)
        response = client.post(
            "/api/login", json={"username": "admin", "password": "NotThePassword#1"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("bg_session", response.cookies)

    def test_failed_login_is_audited_and_lockout_persists(self):
        client = TestClient(api.app)
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            response = client.post(
                "/api/login", json={"username": "admin", "password": "NotThePassword#1"}
            )
            self.assertEqual(response.status_code, 401)
        # The counter is committed, so even the right password is refused now.
        locked = client.post(
            "/api/login", json={"username": "admin", "password": ADMIN[1]}
        )
        self.assertEqual(locked.status_code, 401)
        self.assertIn("15 minutes", locked.json()["detail"])
        with store.connect() as conn:
            row = conn.execute(
                "SELECT failed_attempts, locked_until FROM users WHERE username='admin'"
            ).fetchone()
            actions = [
                item["action"]
                for item in conn.execute("SELECT action FROM audit_logs ORDER BY id")
            ]
        self.assertEqual(row["failed_attempts"], auth.MAX_FAILED_ATTEMPTS)
        self.assertIsNotNone(row["locked_until"])
        self.assertIn("login_failed", actions)
        self.assertIn("locked", actions)

    def test_me_returns_the_effective_role(self):
        client = self.client(UPLOADER)
        payload = client.get("/api/me").json()
        self.assertEqual(payload["user"]["username"], "asha")
        self.assertEqual(payload["user"]["role"], "uploader")
        probe = client.get("/api/session").json()
        self.assertTrue(probe["authenticated"])
        self.assertEqual(probe["user"]["username"], "asha")

    def test_uploader_can_upload_and_save_without_admin(self):
        uploader = self.client(UPLOADER)
        entry = entry_for()
        run_id = self.run_with(self.uploader, entry)
        response = self.confirm(uploader, run_id, [entry])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(config.WORKBOOK_PATH.exists())
        with store.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM bg_records").fetchone()["n"], 1
            )

    def test_viewer_is_read_only(self):
        viewer = self.client(VIEWER)
        self.assertEqual(viewer.post("/api/runs").status_code, 403)
        self.assertEqual(viewer.get("/api/reminders").status_code, 200)
        self.assertEqual(viewer.post("/api/reminders/run").status_code, 403)
        self.assertEqual(viewer.get("/api/users").status_code, 403)
        self.assertEqual(viewer.get("/api/register").status_code, 200)

    def test_uploader_cannot_open_or_edit_another_run(self):
        admin = self.client(ADMIN)
        uploader = self.client(UPLOADER)
        run_id = self.run_with(self.admin, entry_for())
        self.assertEqual(uploader.get(f"/api/runs/{run_id}").status_code, 403)
        self.assertEqual(
            uploader.post(f"/api/runs/{run_id}/submit").status_code, 404
        )
        self.assertEqual(admin.get(f"/api/runs/{run_id}").status_code, 200)

    def test_uploader_lists_and_saves_only_their_own_runs(self):
        uploader = self.client(UPLOADER)
        mine = self.run_with(self.uploader, entry_for("MINE001"))
        self.run_with(self.admin, entry_for("THEIRS01"))
        listed = uploader.get("/api/runs").json()["runs"]
        self.assertEqual([item["run_id"] for item in listed], [mine])
        response = self.confirm(uploader, mine, [entry_for("MINE001")])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["written"], ["MINE001.pdf"])

    def test_admin_confirm_writes_workbook_and_register(self):
        admin = self.client(ADMIN)
        entry = entry_for()
        run_id = self.run_with(self.uploader, entry)
        response = self.confirm(admin, run_id, [entry])
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["written"], [entry["source_pdf"]])
        self.assertFalse(payload["not_written"])

        with store.connect() as conn:
            row = register.find_existing(conn, "WEB001")
            self.assertIsNotNone(row)
            self.assertEqual(row["created_by"], "admin")
            actions = [
                item["action"]
                for item in conn.execute("SELECT action FROM audit_logs ORDER BY id")
            ]
            self.assertIn("confirm", actions)
        self.assertTrue(config.WORKBOOK_PATH.exists())
        sheet = load_workbook(config.WORKBOOK_PATH).active
        target = excel.find_row_by_bg_no(sheet, "WEB001")
        self.assertIsNotNone(target)
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(header, list(HEADERS))

        # A second run with the same BG No is reported honestly, never doubled.
        second = entry_for(source_pdf="again.pdf")
        run_two = self.run_with(self.uploader, second)
        again = self.confirm(admin, run_two, [second])
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["written"], [])
        self.assertEqual(again.json()["not_written"][0]["reason"], "duplicate")
        self.assertEqual(again.json()["not_written"][0]["existing"]["BG No"], "WEB001")
        with store.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM bg_records").fetchone()["n"], 1
            )

    def test_multi_plaza_bg_adds_one_workbook_row_per_plaza(self):
        admin = self.client(ADMIN)
        entry = entry_for("WEB002")
        entry["plazas"] = ["First Plaza", "Second Plaza"]
        run_id = self.run_with(self.uploader, entry)
        response = self.confirm(admin, run_id, [entry])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["written"], [entry["source_pdf"]])

        sheet = load_workbook(config.WORKBOOK_PATH).active
        rows = [
            row
            for row in sheet.iter_rows(min_row=2, values_only=True)
            if row[HEADERS.index("BG No")] == "WEB002"
        ]
        self.assertEqual(
            [row[HEADERS.index("Toll Plaza Name")] for row in rows],
            ["First Plaza", "Second Plaza"],
        )
        # Reminders stay per guarantee, so the register keeps one row.
        with store.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM bg_records WHERE bg_no = 'WEB002'"
                ).fetchone()["n"],
                1,
            )

    def test_uploader_saves_reviewed_values_directly(self):
        uploader = self.client(UPLOADER)
        entry = entry_for()
        run_id = self.run_with(self.uploader, entry)
        reviewed = dict(entry["values"])
        reviewed["Remarks"] = "Checked against the paper guarantee"
        reviewed["created_by"] = "not-the-server"
        response = self.confirm(uploader, run_id, [{"source_pdf": entry["source_pdf"], "values": reviewed}])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["written"], [entry["source_pdf"]])
        with store.connect() as conn:
            stored = register.find_existing(conn, "WEB001")
        self.assertEqual(stored["Remarks"], "Checked against the paper guarantee")
        self.assertEqual(stored["created_by"], "asha")

    def test_confirmed_run_is_frozen(self):
        admin = self.client(ADMIN)
        entry = entry_for()
        run_id = self.run_with(self.uploader, entry)
        self.assertEqual(self.confirm(admin, run_id, [entry]).status_code, 200)
        again = self.confirm(admin, run_id, [entry])
        self.assertEqual(again.status_code, 403)
        self.assertEqual(again.json()["detail"], "This run is already confirmed and cannot be edited.")

    def test_missing_required_is_never_bypassed_by_include_flagged(self):
        admin = self.client(ADMIN)
        entry = entry_for(**{"Company Name": ""})
        run_id = self.run_with(self.uploader, entry)
        response = self.confirm(admin, run_id, [entry], include_flagged=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["written"], [])
        self.assertEqual(
            response.json()["not_written"][0]["missing"], ["Company Name"]
        )
        self.assertFalse(config.WORKBOOK_PATH.exists())
        self.assertNotEqual(runs.load_manifest(run_id).get("status"), "confirmed")

    def test_open_flags_can_be_accepted_but_are_reported(self):
        admin = self.client(ADMIN)
        entry = entry_for()
        entry["fields"]["BG No"] = {"flags": ["low_confidence"], "notes": []}
        run_id = self.run_with(self.uploader, entry)
        blocked = self.confirm(admin, run_id, [entry])
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["data"]["rows"][entry["source_pdf"]]["flags"], {"BG No": ["low_confidence"]})
        accepted = self.confirm(admin, run_id, [entry], include_flagged=True)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["written"], [entry["source_pdf"]])
        self.assertEqual(accepted.json()["open_flags"], {entry["source_pdf"]: {"BG No": ["low_confidence"]}})

    def test_extension_closes_the_previous_bg_in_register_and_workbook(self):
        admin = self.client(ADMIN)
        first = entry_for("EXT001", To="2027-12-31", **{"Claim Period": "2028-03-31"})
        second = entry_for(
            "EXT002",
            Extended="Yes",
            source_pdf="EXT002.pdf",
            From="2026-01-01",
            To="2027-12-31",
            **{"Claim Period": "2028-03-31"},
        )
        run_id = self.run_with(self.uploader, first, second)
        response = self.confirm(admin, run_id, [first, second])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sorted(response.json()["written"]), ["EXT001.pdf", "EXT002.pdf"])
        self.assertEqual([item["BG No"] for item in response.json()["closed_by_extension"]], ["EXT001"])
        with store.connect() as conn:
            old = register.find_existing(conn, "EXT001")
            new = register.find_existing(conn, "EXT002")
        self.assertEqual(old["Status"], "Closed")
        self.assertEqual(new["Status"], "Active")
        sheet = load_workbook(config.WORKBOOK_PATH).active
        statuses = {}
        for bg_no in ("EXT001", "EXT002"):
            target = excel.find_row_by_bg_no(sheet, bg_no)
            self.assertIsNotNone(target, bg_no)
            statuses[bg_no] = sheet.cell(
                row=target, column=HEADERS.index("Status") + 1
            ).value
        self.assertEqual(statuses, {"EXT001": "Closed", "EXT002": "Active"})

    def test_register_export_and_admin_correction_are_audited(self):
        admin = self.client(ADMIN)
        viewer = self.client(VIEWER)
        entry = entry_for()
        run_id = self.run_with(self.uploader, entry)
        self.confirm(admin, run_id, [entry])

        listing = viewer.get("/api/register", params={"q": "WEB001"}).json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["stats"]["total"], 1)
        export = viewer.get("/api/register/export")
        self.assertEqual(export.status_code, 200)
        self.assertIn("spreadsheetml", export.headers["content-type"])
        self.assertTrue(export.content.startswith(b"PK"))

        row = listing["rows"][0]
        close = admin.post(f"/api/register/{row['id']}/close", json={"remarks": "Closed after expiry"})
        self.assertEqual(close.status_code, 200, close.text)
        self.assertEqual(close.json()["row"]["Status"], "Closed")
        with store.connect() as conn:
            actions = [item["action"] for item in conn.execute("SELECT action FROM audit_logs ORDER BY id")]
        self.assertIn("export", actions)
        self.assertIn("close", actions)
        sheet = load_workbook(config.WORKBOOK_PATH).active
        target = excel.find_row_by_bg_no(sheet, "WEB001")
        self.assertIsNotNone(target)
        self.assertEqual(
            sheet.cell(row=target, column=HEADERS.index("Status") + 1).value, "Closed"
        )

    def test_viewer_cannot_export_the_review_workbook(self):
        admin = self.client(ADMIN)
        uploader = self.client(UPLOADER)
        entry = entry_for()
        run_id = self.run_with(self.uploader, entry)
        self.confirm(admin, run_id, [entry])
        self.assertEqual(uploader.get(f"/api/runs/{run_id}/workbook").status_code, 403)
        self.assertEqual(self.client(VIEWER).get(f"/api/runs/{run_id}/workbook").status_code, 200)

    def test_cross_origin_write_is_refused(self):
        admin = self.client(ADMIN)
        response = admin.post(
            "/api/workbook/reset", headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_user_management_and_last_admin_guard(self):
        admin = self.client(ADMIN)
        created = admin.post(
            "/api/users",
            json={"username": "newbie", "password": "Newbie#12345", "role": "viewer"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["user"]["id"]
        promoted = admin.patch(f"/api/users/{user_id}", json={"role": "uploader"})
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.json()["user"]["role"], "uploader")

        with store.connect() as conn:
            admin_id = conn.execute(
                "SELECT id FROM users WHERE username='admin'"
            ).fetchone()["id"]
        guard = admin.patch(f"/api/users/{admin_id}", json={"is_active": False})
        self.assertEqual(guard.status_code, 400)
        self.assertIn("last active admin", guard.json()["detail"])


if __name__ == "__main__":
    unittest.main()
