"""Resend HTTPS email transport (PRIMARY when RESEND_API_KEY is set).

Resend's REST API is HTTPS (port 443), so it delivers on hosts that block outbound SMTP
(e.g. Render Free/Starter) where Gmail SMTP cannot connect. This module is a thin, best-
effort wrapper used behind the existing email seams (`services.email_transport` and
`core.email_smtp`); those keep their signatures and every caller/flow is unchanged.

Contract (mirrors the SMTP helpers):
- NEVER raises into the caller — returns True on a 2xx from Resend, False otherwise.
- The API key travels ONLY in the Authorization header and is NEVER logged. On failure we
  log the HTTP status + a short, non-sensitive slice of Resend's error body (e.g. an
  "domain is not verified" message), which contains no secret.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger("aeomirror.email_resend")

_ENDPOINT = "https://api.resend.com/emails"


def resend_configured() -> bool:
    """True when the Resend HTTPS transport can send (API key present)."""
    return bool(settings.resend_api_key)


def send(*, to: str, subject: str, text: str, html: str | None,
         from_addr: str, reply_to: str | None = None, headers: dict | None = None) -> bool:
    """Send one email via the Resend HTTPS API. Never raises; returns True on success.

    `from_addr` MUST be an address on a domain verified in Resend (Resend rejects
    unverified senders). `headers` carries optional extras (e.g. the digest's
    List-Unsubscribe headers) straight through to Resend.
    """
    if not settings.resend_api_key or not from_addr:
        return False

    payload: dict = {"from": from_addr, "to": [to], "subject": subject}
    if text:
        payload["text"] = text
    if html:
        payload["html"] = html
    if reply_to:
        payload["reply_to"] = reply_to
    if headers:
        payload["headers"] = {str(k): str(v) for k, v in headers.items()}

    try:
        resp = httpx.post(
            _ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            timeout=settings.smtp_timeout_seconds,
        )
        if resp.status_code // 100 == 2:
            return True
        # Non-2xx: log status + a truncated, secret-free slice of Resend's error body.
        logger.error("Resend send failed: HTTP %s %s", resp.status_code, (resp.text or "")[:200])
        return False
    except Exception:   # network/timeout — never surface to the request path
        logger.error("Resend send errored", exc_info=True)
        return False
