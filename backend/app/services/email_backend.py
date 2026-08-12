"""Scoped email backend for the weekly digest + preflight only (C3).

Selects `console` | `resend` via settings.digest_email_backend. The console backend logs
the fully rendered email to stdout and NEVER calls Resend (local-dev / test default); the
resend backend posts to the Resend API. Never raises — returns a result dict the caller
records / returns to an admin. Existing auth / billing / summary email is untouched.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger("aeomirror.email_backend")
RESEND_ENDPOINT = "https://api.resend.com/emails"


def send(*, to: str, subject: str, text: str, html: str,
         headers: dict | None = None, backend: str | None = None) -> dict:
    """Send one email via the resolved backend. Returns a result dict with at least
    {backend, status}; status is 'logged' (console) | 'sent' | 'skipped' | 'failed'."""
    backend = backend or settings.digest_email_backend

    if backend == "console":
        log.info("[email:console] to=%s subject=%r\n--- headers ---\n%s\n--- text ---\n%s",
                 to, subject, headers or {}, text)
        return {"backend": "console", "status": "logged", "to": to, "subject": subject}

    # resend
    if not settings.email_enabled:
        return {"backend": "resend", "status": "skipped", "reason": "not_configured"}
    try:
        resp = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "to": [to], "subject": subject,
                  "text": text, "html": html, "headers": headers or {}},
            timeout=10.0,
        )
        ok = resp.status_code // 100 == 2
        provider = {}
        try:
            provider = resp.json()
        except Exception:   # noqa: BLE001 — provider body may not be JSON
            pass
        if not ok:
            log.warning("[email:resend] failed HTTP %s", resp.status_code)
        return {"backend": "resend", "status": "sent" if ok else "failed",
                "http_status": resp.status_code, "provider": provider}
    except Exception as e:   # noqa: BLE001 — transport failure is never fatal
        log.warning("[email:resend] error: %s", type(e).__name__)
        return {"backend": "resend", "status": "failed", "error": type(e).__name__}
