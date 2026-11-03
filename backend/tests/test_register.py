"""The SQLite register: confirmed rows, masters, extensions, filters, export."""
from __future__ import annotations

import datetime as dt
import io
import unittest

from openpyxl import load_workbook

from bgauto import register, store
from bgauto.extract import HEADERS
from support import TempDatabase


def sample(**overrides) -> dict:
    row = {
        "Company Name": "Example Eastern Projects Private Limited",
        "Project Name": "AB",
        "Toll Plaza Name": "First Plaza",
        "Name of Contractor": "Example Infra Limited",
        "Types of BG": "PBG",
        "Nature of Service": "Repair & Rehabilitation",
        "BG No": "0001TESTBG",
        "BG Amount": 4078080.0,
        "Bank Name": "HDFC Bank",
        "From": "2025-01-01",
        "To": "2025-12-31",
        "Claim Period": "2026-03-31",
        "Status": "Active",
        "Extended": "No",
        "Remarks": "",
        "LOA Number": "LOA/TEST/001",
        "PO Number": "",
        "source_pdf": "sample.pdf",
        "created_by": "admin",
        "created_at": dt.datetime(2026, 1, 1, 10, 0),
        "reminder_sent_at": None,
    }
    row.update(overrides)
    return row


class RegisterTests(TempDatabase):
    def setUp(self):
        super().setUp()
        self.bootstrap_admin()
        with store.connect() as conn:
            self.admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]

    def _store(self, row):
        with store.connect() as conn:
            return register.store_row(conn, row, self.admin)

    def test_store_row_resolves_and_creates_masters(self):
        outcome, detail = self._store(sample())
        self.assertEqual(outcome, "written")
        with store.connect() as conn:
            row = register.find_existing(conn, "0001TESTBG")
        self.assertIsNotNone(row)
        self.assertEqual(row["Project Name"], "AB")
        self.assertEqual(row["Toll Plaza Name"], "First Plaza")
        self.assertEqual(row["BG Amount"], 4078080.0)
        self.assertEqual(row["Extended"], "No")
        with store.connect() as conn:
            source = conn.execute(
                "SELECT source FROM company WHERE name = ?",
                ("Example Eastern Projects Private Limited",),
            ).fetchone()[0]
            joined = conn.execute(
                "SELECT l.loa_po_number FROM bg_records r JOIN loa_records l ON l.id = r.loa_id "
                "WHERE r.bg_no = '0001TESTBG'"
            ).fetchone()[0]
        self.assertEqual(source, "manual")
        self.assertEqual(joined, "LOA/TEST/001")
        self.assertEqual(row["LOA Number"], "LOA/TEST/001")

    def test_duplicate_bg_no_is_never_inserted_twice(self):
        self.assertEqual(self._store(sample())[0], "written")
        outcome, existing = self._store(sample(**{"source_pdf": "second.pdf"}))
        self.assertEqual(outcome, "duplicate")
        self.assertEqual(existing["BG No"], "0001TESTBG")
        with store.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM bg_records").fetchone()[0], 1
            )

    def test_extension_closes_the_previous_bg_it_does_not_delete(self):
        old = sample()
        first = self._store(old)[1]
        second = self._store(
            sample(
                **{
                    "BG No": "0002TESTBG",
                    "From": "2026-01-01",
                    "To": "2026-12-31",
                    "Claim Period": "2027-03-31",
                    "Extended": "Yes",
                    "source_pdf": "extension.pdf",
                }
            )
        )[1]
        with store.connect() as conn:
            closed = register.apply_extension(conn, second["loa_id"], second["id"])
            old_row = register.find_existing_by_id(conn, first["id"])
            new_row = register.find_existing_by_id(conn, second["id"])
        self.assertEqual([item["BG No"] for item in closed], ["0001TESTBG"])
        self.assertEqual(old_row["Status"], "Closed")
        self.assertEqual(old_row["Extended"], "Yes")
        self.assertEqual(new_row["Status"], "Active")
        self.assertEqual(new_row["Extended"], "Yes")

    def test_extension_does_not_close_a_different_plaza(self):
        first = self._store(sample())[1]
        second = self._store(
            sample(
                **{
                    "BG No": "0002TESTBG",
                    "Toll Plaza Name": "Second Plaza",
                    "From": "2026-01-01",
                    "To": "2026-12-31",
                    "Claim Period": "2027-03-31",
                }
            )
        )[1]
        with store.connect() as conn:
            closed = register.apply_extension(conn, second["loa_id"], second["id"])
            old_row = register.find_existing_by_id(conn, first["id"])
        self.assertEqual(closed, [])
        self.assertEqual(old_row["Status"], "Active")

    def test_extension_does_not_close_a_different_contractor(self):
        first = self._store(sample())[1]
        second = self._store(
            sample(
                **{
                    "BG No": "0002TESTBG",
                    "Name of Contractor": "Another Infra Limited",
                    "From": "2026-01-01",
                    "To": "2026-12-31",
                    "Claim Period": "2027-03-31",
                }
            )
        )[1]
        with store.connect() as conn:
            closed = register.apply_extension(conn, second["loa_id"], second["id"])
            old_row = register.find_existing_by_id(conn, first["id"])
        self.assertEqual(closed, [])
        self.assertEqual(old_row["Status"], "Active")

    def test_extension_does_not_close_a_different_bg_type(self):
        first = self._store(sample())[1]
        second = self._store(
            sample(
                **{
                    "BG No": "0002TESTBG",
                    "Types of BG": "ABG",
                    "From": "2026-01-01",
                    "To": "2026-12-31",
                    "Claim Period": "2027-03-31",
                }
            )
        )[1]
        with store.connect() as conn:
            closed = register.apply_extension(conn, second["loa_id"], second["id"])
            old_row = register.find_existing_by_id(conn, first["id"])
        self.assertEqual(closed, [])
        self.assertEqual(old_row["Status"], "Active")

    def test_extension_needs_a_later_from_date(self):
        first = self._store(sample())[1]
        second = self._store(
            sample(
                **{
                    "BG No": "0002TESTBG",
                    "From": "2024-12-01",
                }
            )
        )[1]
        with store.connect() as conn:
            closed = register.apply_extension(conn, second["loa_id"], second["id"])
            old_row = register.find_existing_by_id(conn, first["id"])
        self.assertEqual(closed, [])
        self.assertEqual(old_row["Status"], "Active")

    def test_update_row_closes_and_corrects(self):
        bg_id = self._store(sample())[1]["id"]
        with store.connect() as conn:
            row = register.update_row(conn, bg_id, {"Status": "Closed", "Remarks": "Closed by admin"})
        self.assertEqual(row["Status"], "Closed")
        self.assertEqual(row["Remarks"], "Closed by admin")
        with store.connect() as conn:
            extended = register.update_row(conn, bg_id, {"Extended": "Yes"})
        self.assertEqual(extended["Extended"], "Yes")

    def test_changing_to_recomputes_status_unless_closed(self):
        today = dt.date.today()
        bg_id = self._store(
            sample(
                From="2019-01-01",
                To="2020-12-31",
                Status="Expired",
                **{"Claim Period": "2021-03-31"},
            )
        )[1]["id"]
        with store.connect() as conn:
            row = register.update_row(
                conn,
                bg_id,
                {
                    "To": (today + dt.timedelta(days=30)).isoformat(),
                    "Claim Period": (today + dt.timedelta(days=60)).isoformat(),
                },
            )
        self.assertEqual(row["Status"], "Active")
        with store.connect() as conn:
            register.update_row(conn, bg_id, {"Status": "Closed"})
            closed = register.update_row(
                conn, bg_id, {"To": (today + dt.timedelta(days=45)).isoformat()}
            )
        self.assertEqual(closed["Status"], "Closed")

    def test_update_row_refuses_bad_dates_and_amounts(self):
        bg_id = self._store(sample())[1]["id"]
        with store.connect() as conn:
            with self.assertRaises(ValueError):
                register.update_row(conn, bg_id, {"BG Amount": "-5"})
            with self.assertRaises(ValueError):
                register.update_row(conn, bg_id, {"To": "2024-01-01"})
        with store.connect() as conn:
            with self.assertRaises(LookupError):
                register.update_row(conn, "missing", {"Status": "Closed"})

    def test_filters_scope_and_paging(self):
        self._store(sample())
        self._store(
            sample(
                **{
                    "BG No": "0003TESTBG",
                    "Status": "Closed",
                    "Project Name": "GD",
                    "Toll Plaza Name": "",
                    "To": "2024-01-01",
                    "Claim Period": "2024-06-30",
                    "From": "2023-01-01",
                }
            )
        )
        with store.connect() as conn:
            everything = register.list_rows(conn)
            self.assertEqual(everything["total"], 2)
            self.assertEqual(len(everything["rows"]), 2)
            # Sorted by expiry, earliest first.
            self.assertEqual(everything["rows"][0]["BG No"], "0003TESTBG")

            closed = register.list_rows(conn, {"status": "Closed"})
            self.assertEqual([r["BG No"] for r in closed["rows"]], ["0003TESTBG"])

            by_project = register.list_rows(conn, {"project": "AB"})
            self.assertEqual([r["BG No"] for r in by_project["rows"]], ["0001TESTBG"])

            by_text = register.list_rows(conn, {"q": "LOA/TEST"})
            self.assertEqual(by_text["total"], 2)

            overdue = register.list_rows(conn, {"overdue": True})
            self.assertEqual([r["BG No"] for r in overdue["rows"]], ["0001TESTBG"])

            page = register.list_rows(conn, {"limit": 1, "offset": 1})
            self.assertEqual(page["total"], 2)
            self.assertEqual(len(page["rows"]), 1)

            scoped = register.list_rows(conn, {"created_by": "someone-else"})
            self.assertEqual(scoped["total"], 0)

        with store.connect() as conn:
            summary = register.stats(conn)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_status"], {"Active": 1, "Closed": 1})

    def test_due_rows_are_exactly_seven_days_out_and_open(self):
        today = dt.date(2026, 6, 1)
        self._store(sample(**{"BG No": "DUE7", "From": "2025-01-01", "To": "2026-06-08", "Claim Period": "2026-09-08"}))
        self._store(sample(**{"BG No": "DUE8", "From": "2025-01-01", "To": "2026-06-09", "Claim Period": "2026-09-09"}))
        self._store(sample(**{"BG No": "CLOSED7", "From": "2025-01-01", "To": "2026-06-08", "Claim Period": "2026-09-08", "Status": "Closed"}))
        with store.connect() as conn:
            due = register.due_rows(conn, 7, today)
            self.assertEqual([row["BG No"] for row in due], ["DUE7"])
            register.mark_reminder_sent(conn, due[0]["id"], dt.datetime(2026, 6, 1, 9, 0))
            self.assertEqual(register.due_rows(conn, 7, today), [])

    def test_export_has_the_twenty_one_tracker_columns(self):
        self._store(sample())
        with store.connect() as conn:
            page = register.list_rows(conn)
            payload = register.export_bytes(page["rows"])
        sheet = load_workbook(io.BytesIO(payload)).active
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(header, list(HEADERS))
        self.assertEqual(len(header), 21)
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual(sheet["A2"].value, "Example Eastern Projects Private Limited")
        self.assertEqual(sheet["G2"].value, "0001TESTBG")


if __name__ == "__main__":
    unittest.main()
