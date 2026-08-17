"""Scoped email backend for the weekly digest + preflight only (C3).

Selects `console` | `smtp` via settings.digest_email_backend. The console backend logs the
fully rendered email to stdout and NEVER sends (local-dev / test default); the smtp backend
delivers through the shared Gmail SMTP transport, preserving the digest's List-Unsubscribe
headers. Never raises — returns a result dict the caller records / returns to an admin.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.services import email_transport

log = logging.getLogger("aeomirror.email_backend")


def send(*, to: str, subject: str, text: str, html: str,
         headers: dict | None = None, backend: str | None = None) -> dict:
    """Send one email via the resolved backend. Returns a result dict with at least
    {backend, status}; status is 'logged' (console) | 'sent' | 'skipped' | 'failed'."""
    backend = backend or settings.digest_email_backend

    if backend == "console":
        log.info("[email:console] to=%s subject=%r\n--- headers ---\n%s\n--- text ---\n%s",
                 to, subject, headers or {}, text)
        return {"backend": "console", "status": "logged", "to": to, "subject": subject}

    # smtp — the digest's List-Unsubscribe headers are carried through the transport
    status = email_transport.send_email(to, subject, text, html,
                                        kind="digest", log_prefix="digest", headers=headers)
    return {"backend": "smtp", "status": status}
