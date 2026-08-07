"""Gmail SMTP email transport (reusable).

This mirrors the Gmail SMTP architecture used in The Doc Mirror: a single small
`send_email` that authenticates to Gmail with an App Password over STARTTLS and
delivers a text (+ optional HTML) message. It is the ONLY mail transport for the
Contact & Support system — no third-party email API is used.

Design:
- Credentials come exclusively from settings (env: GMAIL_USER / GMAIL_APP_PASSWORD).
  Nothing is hardcoded and no secret is ever logged.
- Best-effort: sending never raises into the caller. Returns True on a successful
  send, False when SMTP is not configured or the send failed.
- Header-injection safe: header values (subject, addresses, reply-to) are sanitized
  to a single line before use, and `email.message.EmailMessage` (RFC-compliant
  header handling) is used rather than hand-built header strings.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("aeomirror.email_smtp")


def _one_line(value: str) -> str:
    """Collapse CR/LF (and stray control chars) so a value can never inject extra
    email headers (header-injection / email-splitting defense)."""
    return "".join(ch for ch in (value or "") if ch not in "\r\n").strip()


def smtp_enabled() -> bool:
    return settings.smtp_configured


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    reply_to: str | None = None,
) -> bool:
    """Send one email via Gmail SMTP. Never raises.

    Returns False (and logs a single non-sensitive line) when SMTP is not
    configured or the send fails, so the caller's request path is never broken.
    """
    from_addr = settings.smtp_from
    if not settings.smtp_configured or not from_addr:
        # In dev this is expected (no credentials); log so it's visible, never the
        # message body or any secret.
        logger.info("SMTP not configured (GMAIL_USER/GMAIL_APP_PASSWORD); skipping send to %s",
                    _one_line(to)[:120])
        return False

    to_addr = _one_line(to)
    if "@" not in to_addr:
        logger.warning("SMTP send skipped: invalid recipient")
        return False

    msg = EmailMessage()
    msg["From"] = _one_line(from_addr)
    msg["To"] = to_addr
    msg["Subject"] = _one_line(subject)
    if reply_to:
        rt = _one_line(reply_to)
        if "@" in rt:
            msg["Reply-To"] = rt
    msg.set_content(text_body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                          timeout=settings.smtp_timeout_seconds) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.gmail_user, settings.gmail_app_password)
            server.send_message(msg)
        return True
    except Exception as e:  # never surface SMTP errors to the request
        logger.warning("SMTP send failed: %s", type(e).__name__)
        return False
