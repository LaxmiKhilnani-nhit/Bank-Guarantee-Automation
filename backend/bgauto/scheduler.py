"""T-7 expiry reminders and the once-a-day scheduler.

One mail per BG whose ``To`` is exactly ``REMINDER_DAYS`` away, still open and
not reminded yet (``plan/05`` flow C, ``plan/06`` section 4). Every attempt is
recorded in ``reminders``; ``UNIQUE (bg_id, scheduled_for)`` is what stops a BG
from ever getting the same T-7 mail twice.

The scheduler is an in-process thread, started by the web app's lifespan. The
same work is exposed as ``POST /internal/daily-reminder`` for an external Task
Scheduler (shared-secret header) and as an admin-only button in the UI.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from typing import Callable

from . import config, register, store
from .mailer import MailError, MailSender, Message, reminder_message

log = logging.getLogger("bgauto.scheduler")

SCHEDULE_FILE = config.DATA_DIR / "scheduler-last-run.txt"


# --- reading -------------------------------------------------------------


def list_reminders(conn, *, status: str | None = None, limit: int = 200) -> list[dict]:
    clauses = []
    params: list = []
    if status:
        clauses.append("m.status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT m.id, m.bg_id, m.scheduled_for, m.sent_at, m.recipients, m.subject, "
        "       m.status, m.error, m.created_at, r.bg_no, r.to_date "
        "FROM reminders m JOIN bg_records r ON r.id = m.bg_id"
        f"{where} ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
        (*params, max(1, min(int(limit), 500))),
    ).fetchall()
    result = []
    for row in rows:
        try:
            to = json.loads(row["recipients"])
        except (TypeError, ValueError):
            to = []
        result.append(
            {
                "id": row["id"],
                "bg_id": row["bg_id"],
                "BG No": row["bg_no"],
                "To": row["to_date"],
                "scheduled_for": row["scheduled_for"],
                "sent_at": row["sent_at"],
                "recipients": to,
                "subject": row["subject"],
                "status": row["status"],
                "error": row["error"],
                "created_at": row["created_at"],
            }
        )
    return result


# --- sending -------------------------------------------------------------


def _upsert(conn, bg_id: str, scheduled_for: str, message: Message) -> str:
    row = conn.execute(
        "SELECT id FROM reminders WHERE bg_id = ? AND scheduled_for = ?",
        (bg_id, scheduled_for),
    ).fetchone()
    payload = (
        json.dumps(message.recipients),
        message.subject,
        message.body,
    )
    if row:
        conn.execute(
            "UPDATE reminders SET recipients = ?, subject = ?, body = ?, status = 'pending', "
            "error = NULL WHERE id = ?",
            (*payload, row["id"]),
        )
        return str(row["id"])
    cursor = conn.execute(
        "INSERT INTO reminders (bg_id, scheduled_for, recipients, subject, body, status) "
        "VALUES (?, ?, ?, ?, ?, 'pending')",
        (bg_id, scheduled_for, *payload),
    )
    return str(
        conn.execute(
            "SELECT id FROM reminders WHERE rowid = ?", (cursor.lastrowid,)
        ).fetchone()[0]
    )


def _mark_sent(conn, reminder_id: str, bg_id: str, when: dt.datetime, provider: str) -> None:
    conn.execute(
        "UPDATE reminders SET sent_at = ?, status = 'sent', provider_msg_id = ?, error = NULL "
        "WHERE id = ?",
        (store.iso(when), provider, reminder_id),
    )
    register.mark_reminder_sent(conn, bg_id, when)


def _mark_failed(conn, reminder_id: str, error: str) -> None:
    conn.execute(
        "UPDATE reminders SET status = 'failed', error = ? WHERE id = ?",
        (error[:500], reminder_id),
    )


def _send_one(
    conn,
    row: dict,
    *,
    days: int,
    sender: MailSender,
    now: dt.datetime,
    user_id: str | None = None,
    action: str = "reminder_sent",
    force: bool = False,
) -> dict:
    bg_id = str(row["id"])
    to_date = dt.date.fromisoformat(str(row["To"])[:10])
    scheduled_for = (to_date - dt.timedelta(days=days)).isoformat()

    existing = conn.execute(
        "SELECT id, status FROM reminders WHERE bg_id = ? AND scheduled_for = ?",
        (bg_id, scheduled_for),
    ).fetchone()
    if existing and existing["status"] == "sent" and not force:
        return {"bg_id": bg_id, "BG No": row["BG No"], "status": "skipped", "reason": "already_sent"}

    message = reminder_message(row)
    reminder_id = _upsert(conn, bg_id, scheduled_for, message)
    try:
        provider = sender.send(message)
    except MailError as error:
        _mark_failed(conn, reminder_id, str(error))
        store.audit(
            conn,
            "remind",
            user_id=user_id,
            bg_no=row["BG No"],
            new={"status": "failed", "error": str(error), "recipients": message.recipients},
        )
        return {
            "bg_id": bg_id,
            "BG No": row["BG No"],
            "status": "failed",
            "error": str(error),
            "recipients": message.recipients,
            "manual_queue": True,
        }
    _mark_sent(conn, reminder_id, bg_id, now, provider)
    store.audit(
        conn,
        action,
        user_id=user_id,
        bg_no=row["BG No"],
        new={
            "status": "sent",
            "recipients": message.recipients,
            "subject": message.subject,
            "provider": provider,
        },
    )
    return {
        "bg_id": bg_id,
        "BG No": row["BG No"],
        "status": "sent",
        "recipients": message.recipients,
        "subject": message.subject,
        "dry_run": sender.dry_run or not config.SMTP_HOST,
    }


def run_daily(
    *,
    days: int | None = None,
    today: dt.date | None = None,
    sender: MailSender | None = None,
    db_path=None,
    user_id: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Find the BGs due today and send exactly one mail per BG."""
    days = config.REMINDER_DAYS if days is None else days
    sender = sender or MailSender()
    now = now or store.utcnow()
    results: list[dict] = []
    with store.connect(db_path) as conn:
        due = register.due_rows(conn, days, today)
        for row in due:
            results.append(_send_one(conn, row, days=days, sender=sender, now=now, user_id=user_id))
    sent = [item for item in results if item["status"] == "sent"]
    failed = [item for item in results if item["status"] == "failed"]
    skipped = [item for item in results if item["status"] == "skipped"]
    report = {
        "checked_at": store.iso(now),
        "days": days,
        "due": len(results),
        "sent": len(sent),
        "failed": len(failed),
        "skipped": len(skipped),
        "results": results,
        "dry_run": sender.dry_run or not config.SMTP_HOST,
    }
    if not results:
        log.info("T-%s reminder run: no rows due", days)
    else:
        log.info(
            "T-%s reminder run: %s due, %s sent, %s failed, %s already sent",
            days, len(results), len(sent), len(failed), len(skipped),
        )
    return report


def resend(
    bg_id: str,
    *,
    sender: MailSender | None = None,
    db_path=None,
    user_id: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Admin action: send the T-7 mail again for one BG (``plan/03`` matrix)."""
    sender = sender or MailSender()
    now = now or store.utcnow()
    with store.connect(db_path) as conn:
        row = register.find_existing_by_id(conn, bg_id)
        if row is None:
            raise LookupError("BG not found")
        if not row.get("To"):
            raise ValueError("This BG has no expiry date to remind about.")
        return _send_one(
            conn,
            row,
            days=config.REMINDER_DAYS,
            sender=sender,
            now=now,
            user_id=user_id,
            action="reminder_resend",
            force=True,
        )


# --- the clock -----------------------------------------------------------


def scheduled_time() -> dt.time:
    try:
        hour, minute = (int(part) for part in config.SCHEDULER_TIME.split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 9, 0
    return dt.time(hour=max(0, min(hour, 23)), minute=max(0, min(minute, 59)))


def next_run(now: dt.datetime | None = None) -> dt.datetime:
    """The next 09:00 IST (fixed offset, no tzdata dependency)."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(config.IST)
    target = dt.datetime.combine(now.date(), scheduled_time(), tzinfo=config.IST)
    if target <= now:
        target += dt.timedelta(days=1)
    return target


def _last_run_date() -> dt.date | None:
    try:
        return dt.date.fromisoformat(SCHEDULE_FILE.read_text(encoding="utf-8").strip()[:10])
    except (OSError, ValueError):
        return None


def _mark_ran(when: dt.date) -> None:
    try:
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULE_FILE.write_text(when.isoformat() + "\n", encoding="utf-8")
    except OSError:
        log.warning("could not write the scheduler marker %s", SCHEDULE_FILE)


class DailyScheduler:
    """A daemon thread that wakes at 09:00 IST and runs once per day."""

    def __init__(self, runner: Callable[[], dict] | None = None) -> None:
        self._runner = runner or (lambda: run_daily())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="bg-reminders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Catch up if the app starts after 09:00 and today's run never happened.
        try:
            now_ist = dt.datetime.now(dt.timezone.utc).astimezone(config.IST)
            if now_ist.time() >= scheduled_time() and _last_run_date() != now_ist.date():
                self._run("catch-up")
        except Exception:  # noqa: BLE001 - the loop must survive one bad run
            log.exception("catch-up reminder run failed")

        while not self._stop.is_set():
            target = next_run()
            delay = max(1.0, (target - dt.datetime.now(dt.timezone.utc)).total_seconds())
            if self._stop.wait(delay):
                return
            self._run("scheduled")

    def _run(self, reason: str) -> None:
        log.info("reminder scheduler: %s run", reason)
        try:
            report = self._runner()
            _mark_ran(dt.datetime.now(dt.timezone.utc).astimezone(config.IST).date())
            log.info("reminder scheduler: %s", {k: report.get(k) for k in ("due", "sent", "failed")})
        except Exception:  # noqa: BLE001 - log, then wait for tomorrow
            log.exception("reminder scheduler run failed")


_scheduler: DailyScheduler | None = None


def start() -> DailyScheduler | None:
    global _scheduler
    if not config.SCHEDULER_ENABLED:
        return None
    if _scheduler is None:
        _scheduler = DailyScheduler()
    _scheduler.start()
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
