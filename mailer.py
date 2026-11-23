"""Sends HackFindr's emails.

With SMTP_USER and SMTP_PASSWORD in .env (for Gmail: your address and a 16-letter App password)
emails really go out. Without them nothing is sent: each email is saved as a .eml file in
data/outbox/ instead, so reminders can still be tried and shown in a demo.
"""

import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465") or 465)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").replace(" ", "").strip()   # Google shows it in 4 groups
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip() or (f"HackFindr <{SMTP_USER}>" if SMTP_USER else "HackFindr <noreply@hackfindr.local>")
OUTBOX = Path(os.environ.get("HACKFINDR_DB", Path(__file__).resolve().parent / "data" / "x")).parent / "outbox"


def live():
    """True when emails really go out (SMTP is set up)."""
    return bool(SMTP_USER and SMTP_PASSWORD)


def send(to, subject, html, text, unsubscribe_url=""):
    """Send one email. Returns 'sent' or the outbox file path. Raises if SMTP refuses."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = MAIL_FROM, to, subject
    if unsubscribe_url:   # lets Gmail show its own "Unsubscribe" button
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if not live():
        OUTBOX.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "-", subject)[:50].strip("-")
        path = OUTBOX / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.eml"
        path.write_bytes(bytes(msg))
        return str(path)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)
    return "sent"
