"""Optional confirmation e-mail. Does nothing unless SMTP_HOST is set. Never raises into the request."""
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def send_confirmation(to_email, reg_code, event_name, confirmation_url):
    host = os.getenv("SMTP_HOST")
    if not host:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"ASTRA FEST 2026 — registration {reg_code}"
        msg["From"] = os.getenv("SMTP_FROM", "astra@example.com")
        msg["To"] = to_email
        msg.set_content(f"Your registration {reg_code} for {event_name} is recorded.\nView/print: {confirmation_url}\n")
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as s:
            s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except Exception:  # e-mail must never break registration
        log.exception("confirmation e-mail failed")
        return False
