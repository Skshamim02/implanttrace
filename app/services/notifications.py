"""Alert Quality when orders go on hold. Email and/or Microsoft Teams; never blocks production.

Failures are logged and swallowed: a mail-server outage must not stop an import.
"""

import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger(__name__)


def _message(orders: list[str], source: str) -> tuple[str, str]:
    subject = f"[ImplantTrace] {len(orders)} order(s) ON HOLD after inspection: {', '.join(orders)}"
    body = (
        f"Out-of-tolerance results were imported from {source}.\n\n"
        f"Orders on hold: {', '.join(orders)}\n\n"
        "A Quality disposition is required before production can continue.\n"
        "Open the Quality hold queue in ImplantTrace to review and sign."
    )
    return subject, body


def _send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(settings.alert_recipients)
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def _send_teams(subject: str, body: str) -> None:
    data = json.dumps({"text": f"**{subject}**\n\n{body}"}).encode()
    req = urllib.request.Request(
        settings.teams_webhook_url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (URL is admin config)
        resp.read()


def notify_hold(orders: list[str], source: str) -> None:
    if not orders:
        return
    subject, body = _message(orders, source)
    sent = False
    if settings.smtp_host and settings.alert_recipients:
        try:
            _send_email(subject, body)
            sent = True
        except Exception:
            log.exception("Email alert failed")
    if settings.teams_webhook_url:
        try:
            _send_teams(subject, body)
            sent = True
        except Exception:
            log.exception("Teams alert failed")
    if not sent:
        log.warning("ALERT (no channel configured): %s", subject)
