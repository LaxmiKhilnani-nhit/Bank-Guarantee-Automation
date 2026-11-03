"""Outbound mail for the T-7 reminder, with a dry-run that never leaves the box.

SMTP only in Phase 1 (``plan/06``). When no host is configured the message is
written to ``out/mail/<timestamp>-<bg no>.eml`` instead of being sent, so a
pilot can prove the reminder content and recipients without a mailbox.

The sender is deliberately tiny (``MailSender``): swapping in Microsoft Graph
later means implementing ``send`` once, not touching the reminder workflow.
"""
from __future__ import annotations

import datetime as dt
import re
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from . import config

MAX_ATTEMPTS = 3
RETRY_BACKOFF = 1.0  # seconds; tests patch this down


class MailError(Exception):
    """A message could not be delivered after all retries."""


@dataclass
class Message:
    subject: str
    body: str
    recipients: list[str] = field(default_factory=list)
    reference: str = ""

    def as_email(self, sender: str | None = None) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = self.subject
        message["From"] = formataddr((config.MAIL_FROM_NAME, sender or config.MAIL_FROM))
        message["To"] = ", ".join(self.recipients)
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain="bg-automation.local")
        if self.reference:
            message["X-BG-Reference"] = self.reference
        message.set_content(self.body)
        return message


def recipients() -> list[str]:
    """Finance and user-team distribution lists, de-duplicated, order kept."""
    seen: dict[str, None] = {}
    for address in (*config.FINANCE_DL, *config.USER_TEAM_DL):
        clean = address.strip()
        if clean and clean.lower() not in seen:
            seen[clean.lower()] = None
    return list(seen)


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip())
    return cleaned.strip("-")[:80] or "message"


def _inr(amount) -> str:
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return ""
    return f"{value:,.2f}"


def _display_date(value) -> str:
    text = str(value or "")[:10]
    try:
        return dt.date.fromisoformat(text).strftime("%d-%m-%Y")
    except ValueError:
        return text


def reminder_message(row: dict, link: str | None = None) -> Message:
    """The T-7 template from ``plan/06`` section 3, one mail per BG."""
    bg_no = str(row.get("BG No") or "").strip()
    project = str(row.get("Project Name") or "").strip()
    contractor = str(row.get("Name of Contractor") or "").strip()
    to_date = _display_date(row.get("To"))
    subject = f"BG {bg_no} expiring {to_date} — {project}/{contractor} — extend or close?"
    link = link or f"{config.APP_URL}/#register?q={bg_no}"
    body = "\n".join(
        [
            "A bank guarantee in the register is expiring in 7 days.",
            "",
            f"Company            : {row.get('Company Name') or ''}",
            f"Project            : {project}",
            f"Toll Plaza         : {row.get('Toll Plaza Name') or ''}",
            f"BG No              : {bg_no}",
            f"Amount (INR)       : {_inr(row.get('BG Amount'))}",
            f"Bank               : {row.get('Bank Name') or ''}",
            f"From               : {_display_date(row.get('From'))}",
            f"To                 : {to_date}",
            f"Claim Period       : {_display_date(row.get('Claim Period'))}",
            f"LOA Number         : {row.get('LOA Number') or ''}",
            f"PO Number          : {row.get('PO Number') or ''}",
            f"Remarks            : {row.get('Remarks') or ''}",
            "",
            f"Open the register     : {link}",
            "",
            "Please reply EXTEND or CLOSE.",
            "",
            "-- ",
            "BG Automation (local pilot). This is an automated reminder.",
        ]
    )
    return Message(subject=subject, body=body, recipients=recipients(), reference=bg_no)


class MailSender:
    """Send one message over SMTP, or write it to ``out/mail`` in dry-run."""

    def __init__(
        self,
        *,
        dry_run: bool | None = None,
        mail_dir: Path | None = None,
        attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.dry_run = config.MAIL_DRY_RUN if dry_run is None else dry_run
        self.mail_dir = Path(mail_dir or config.MAIL_DIR)
        self.attempts = max(1, attempts)

    def send(self, message: Message) -> str:
        """Deliver ``message``. Returns a provider id or the dry-run file path."""
        if not message.recipients:
            raise MailError("No recipients are configured (BG_FINANCE_DL / BG_USER_TEAM_DL).")
        if self.dry_run or not config.SMTP_HOST:
            return self._write(message)

        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                email = message.as_email()
                context = ssl.create_default_context()
                with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as client:
                    client.ehlo()
                    client.starttls(context=context)
                    client.ehlo()
                    if config.SMTP_USER:
                        client.login(config.SMTP_USER, config.SMTP_PASS)
                    refused = client.send_message(email)
                if refused:
                    raise MailError(f"SMTP refused: {sorted(refused)}")
                return email.get("Message-ID", "")
            except Exception as error:  # noqa: BLE001 - retried, then reported
                last_error = error
                if attempt < self.attempts:
                    time.sleep(RETRY_BACKOFF * attempt)
        raise MailError(f"{type(last_error).__name__}: {last_error}") from last_error

    def _write(self, message: Message) -> str:
        self.mail_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        name = f"{stamp}-{_safe_filename(message.reference or message.subject)}.eml"
        path = self.mail_dir / name
        email = message.as_email()
        # The dry-run copy is audit evidence, not a queued message: keep a banner.
        email["X-BG-Dry-Run"] = "1"
        path.write_bytes(bytes(email))
        return str(path)
