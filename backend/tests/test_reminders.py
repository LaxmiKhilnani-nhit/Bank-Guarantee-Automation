"""T-7 reminders: dry-run mail, one per BG, retries, resend, scheduler clock."""
from __future__ import annotations

import datetime as dt
import json
import unittest

from bgauto import config, register, scheduler, store
from bgauto.mailer import MailError, MailSender, reminder_message
from support import TempDatabase


class ReminderTests(TempDatabase):
    def setUp(self):
        super().setUp()
        self.bootstrap_admin()
        with store.connect() as conn:
            self.admin_id = conn.execute(
                "SELECT id FROM users WHERE username='admin'"
            ).fetchone()["id"]
        self.today = dt.date(2026, 6, 1)
        self.sender = MailSender(dry_run=True, mail_dir=self.root / "mail")

    def add_bg(self, bg_no="R001", days=7, status="Active", **overrides):
        due = (self.today + dt.timedelta(days=days)).isoformat()
        row = {
            "Company Name": "Example Eastern Projects Private Limited",
            "Project Name": "AB",
            "Toll Plaza Name": "First Plaza",
            "Name of Contractor": "Example Infra Limited",
            "Types of BG": "PBG",
            "Nature of Service": "Repair & Rehabilitation",
            "BG No": bg_no,
            "BG Amount": 1000000,
            "Bank Name": "HDFC Bank",
            "From": "2025-01-01",
            "To": due,
            "Claim Period": (self.today + dt.timedelta(days=97)).isoformat(),
            "Status": status,
            "Extended": "No",
            "Remarks": "",
            "LOA Number": f"LOA/{bg_no}",
            "PO Number": "",
            "source_pdf": f"{bg_no}.pdf",
            "created_by": "admin",
            "created_at": dt.datetime(2026, 1, 1),
            "reminder_sent_at": None,
        }
        row.update(overrides)
        with store.connect() as conn:
            outcome, detail = register.store_row(conn, row, self.admin_id)
        self.assertEqual(outcome, "written")
        return detail["id"]

    def test_template_names_the_bg_and_asks_extend_or_close(self):
        message = reminder_message(
            {
                "BG No": "R001",
                "Project Name": "AB",
                "Name of Contractor": "Example Infra",
                "To": "2026-06-08",
                "BG Amount": 1234567.5,
            }
        )
        self.assertIn("BG R001 expiring 08-06-2026", message.subject)
        self.assertIn("extend or close?", message.subject)
        self.assertIn("1,234,567.50", message.body)
        self.assertIn("reply EXTEND or CLOSE", message.body)
        self.assertEqual(message.recipients, ["finance@example.test", "users@example.test"])

    def test_one_mail_per_bg_and_never_twice(self):
        bg_id = self.add_bg()
        self.add_bg("R008", days=8)
        self.add_bg("RCLOSED", days=7, status="Closed")

        report = scheduler.run_daily(today=self.today, sender=self.sender)
        self.assertEqual(report["due"], 1)
        self.assertEqual(report["sent"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["results"][0]["BG No"], "R001")

        mails = sorted((self.root / "mail").glob("*.eml"))
        self.assertEqual(len(mails), 1)
        body = mails[0].read_text(encoding="utf-8", errors="replace")
        self.assertIn("BG R001 expiring 08-06-2026", body)

        with store.connect() as conn:
            stored = conn.execute(
                "SELECT status, sent_at, recipients, subject FROM reminders"
            ).fetchall()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["status"], "sent")
            self.assertIsNotNone(stored[0]["sent_at"])
            self.assertEqual(list(stored[0]["recipients"]), ["finance@example.test", "users@example.test"])
            self.assertIsNotNone(register.find_existing_by_id(conn, bg_id)["reminder_sent_at"])
            actions = [r["action"] for r in conn.execute("SELECT action FROM audit_logs")]
            self.assertIn("reminder_sent", actions)

        # Re-running on the same day (or later) does not send again.
        again = scheduler.run_daily(today=self.today, sender=self.sender)
        self.assertEqual(again["due"], 0)
        self.assertEqual(len(list((self.root / "mail").glob("*.eml"))), 1)

    def test_failure_is_logged_and_left_unreminded_for_manual_queue(self):
        class BrokenSender(MailSender):
            def send(self, message):
                raise MailError("smtp down")

        bg_id = self.add_bg()
        report = scheduler.run_daily(
            today=self.today,
            sender=BrokenSender(dry_run=True, mail_dir=self.root / "mail"),
        )
        self.assertEqual(report["failed"], 1)
        self.assertTrue(report["results"][0]["manual_queue"])
        with store.connect() as conn:
            reminder = conn.execute("SELECT status, error FROM reminders").fetchone()
            self.assertEqual(reminder["status"], "failed")
            self.assertIn("smtp down", reminder["error"])
            self.assertIsNone(register.find_existing_by_id(conn, bg_id)["reminder_sent_at"])
            self.assertIn("remind", [r["action"] for r in conn.execute("SELECT action FROM audit_logs")])

    def test_missing_recipients_is_a_failure_not_a_silent_send(self):
        self.add_bg()
        config.FINANCE_DL = []
        config.USER_TEAM_DL = []
        report = scheduler.run_daily(today=self.today, sender=self.sender)
        self.assertEqual(report["failed"], 1)
        self.assertIn("No recipients", report["results"][0]["error"])

    def test_resend_updates_the_same_reminder_row(self):
        bg_id = self.add_bg()
        scheduler.run_daily(today=self.today, sender=self.sender)
        report = scheduler.resend(
            bg_id, sender=self.sender, now=store.utcnow()
        )
        self.assertEqual(report["status"], "sent")
        with store.connect() as conn:
            rows = conn.execute("SELECT status, sent_at FROM reminders").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "sent")
            actions = [r["action"] for r in conn.execute("SELECT action FROM audit_logs")]
            self.assertIn("reminder_resend", actions)

    def test_list_reminders_returns_recipients_and_subject(self):
        self.add_bg()
        scheduler.run_daily(today=self.today, sender=self.sender)
        with store.connect() as conn:
            listed = scheduler.list_reminders(conn)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["BG No"], "R001")
        self.assertIn("BG R001", listed[0]["subject"])
        self.assertEqual(listed[0]["recipients"], ["finance@example.test", "users@example.test"])

    def test_next_run_is_nine_am_ist(self):
        before = dt.datetime(2026, 6, 1, 8, 59, tzinfo=config.IST)
        self.assertEqual(
            scheduler.next_run(before),
            dt.datetime(2026, 6, 1, 9, 0, tzinfo=config.IST),
        )
        after = dt.datetime(2026, 6, 1, 9, 1, tzinfo=config.IST)
        self.assertEqual(
            scheduler.next_run(after),
            dt.datetime(2026, 6, 2, 9, 0, tzinfo=config.IST),
        )


if __name__ == "__main__":
    unittest.main()
