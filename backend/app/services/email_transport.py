"""Shared outbound email transport — Gmail SMTP (the ONLY transport).

Every transactional email (monitor alerts, auth links, billing receipts, the weekly
digest, lead welcomes) sends through this one function. There is no third-party HTTP
email API. Best-effort: sending NEVER raises into the caller — it returns a status
string the caller records.

Header-injection safe: header values are collapsed to a single line before use (a CR/LF
in a subject or address can never inject extra headers).

Note: `headers` is an OPTIONAL, additive extension to the required signature — it carries
the digest's RFC 8058 List-Unsubscribe headers so one-click unsubscribe is preserved.
Callers that don't need custom headers simply omit it.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("aeomirror.email")


def _one_line(value: str) -> str:
    """Collapse CR/LF (and stray control chars) so a value can never inject extra email
    headers (header-injection / email-splitting defense)."""
    return "".join(ch for ch in (value or "") if ch not in "\r\n").strip()


def send_email(to: str, subject: str, text: str, html_body: str,
               *, kind: str, log_prefix: str, headers: dict | None = None) -> str:
    """Send one email via Gmail SMTP. Returns 'sent' | 'skipped' | 'failed'. NEVER raises.

    - Not configured -> 'skipped' (logs a single line only outside production).
    - MIMEMultipart('alternative'); the plain-text part is attached BEFORE the HTML part.
    - From: settings.smtp_from   Reply-To: settings.gmail_user
    - Port 465 uses SMTP_SSL (no STARTTLS); any other port uses SMTP + starttls().
    """
    if not settings.email_enabled:
        if not settings.is_production:
            logger.info("[%s:%s] email not configured; would send to %s: %s",
                        log_prefix, kind, _one_line(to), _one_line(subject))
        return "skipped"

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = _one_line(settings.smtp_from or "")
        msg["To"] = _one_line(to)
        msg["Subject"] = _one_line(subject)
        reply_to = _one_line(settings.gmail_user or "")
        if reply_to:
            msg["Reply-To"] = reply_to
        for hk, hv in (headers or {}).items():
            msg[_one_line(str(hk))] = _one_line(str(hv))
        # plain text FIRST, then HTML (last alternative = preferred by the client)
        msg.attach(MIMEText(text or "", "plain"))
        msg.attach(MIMEText(html_body or "", "html"))

        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                  timeout=settings.smtp_timeout_seconds) as server:
                server.login(settings.gmail_user, settings.gmail_app_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                              timeout=settings.smtp_timeout_seconds) as server:
                server.starttls()
                server.login(settings.gmail_user, settings.gmail_app_password)
                server.send_message(msg)
        return "sent"
    except Exception:   # never surface SMTP errors to the caller
        logger.error("[%s:%s] SMTP send failed", log_prefix, kind, exc_info=True)
        return "failed"
