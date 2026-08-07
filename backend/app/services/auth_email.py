"""Transactional auth emails (Phase 5): email verification, password reset, and
team invitations.

Sending is best-effort and mirrors the lead-email pattern: Resend is used when
RESEND_API_KEY + EMAIL_FROM are configured, otherwise the send is skipped. The
raw one-time token only ever appears in the recipient's link. In NON-production,
when email is not configured, the link is logged so a developer can complete the
flow locally; in production the token is never logged.
"""
from __future__ import annotations

import html
import logging
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger("aeomirror.auth_email")

RESEND_ENDPOINT = "https://api.resend.com/emails"


def _send(email: str, subject: str, text_body: str, html_body: str,
          *, kind: str, link: str) -> bool:
    """Send one email via Resend. Never raises. In dev without email configured,
    logs the link so the flow is testable; never logs tokens in production."""
    if not settings.email_enabled:
        if not settings.is_production:
            # Dev convenience only — lets you click the verify/reset/invite link.
            logger.info("[auth_email:%s] email not configured; link=%s", kind, link)
        else:
            logger.info("[auth_email:%s] email not configured; send skipped.", kind)
        return False
    try:
        resp = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "to": [email],
                  "subject": subject, "text": text_body, "html": html_body},
            timeout=10.0,
        )
        if resp.status_code // 100 == 2:
            return True
        logger.warning("[auth_email:%s] Resend failed: HTTP %s", kind, resp.status_code)
        return False
    except Exception as e:
        logger.warning("[auth_email:%s] Resend error: %s", kind, type(e).__name__)
        return False


def _wrap(body_html: str) -> str:
    return (f"<div style=\"font-family:system-ui,sans-serif;line-height:1.6;"
            f"color:#0B0F14\">{body_html}</div>")


def _btn(url: str, label: str) -> str:
    return (f"<p><a href=\"{html.escape(url)}\" style=\"display:inline-block;"
            f"background:#34D3E0;color:#04222a;padding:11px 18px;border-radius:8px;"
            f"text-decoration:none;font-weight:600\">{html.escape(label)}</a></p>")


def send_verification_email(email: str, token: str) -> bool:
    link = f"{settings.app_base_url}/verify-email?token={quote(token)}"
    subject = "Verify your AEOMirror email"
    text = ("Welcome to AEOMirror!\n\n"
            f"Confirm your email to activate your account:\n{link}\n\n"
            "This link expires in 24 hours. If you didn't sign up, ignore this email.")
    html_body = _wrap(
        "<p>Welcome to <strong>AEOMirror</strong>!</p>"
        "<p>Confirm your email to activate your account.</p>"
        + _btn(link, "Verify email")
        + "<p style=\"color:#555;font-size:13px\">This link expires in 24 hours. "
          "If you didn't sign up, you can ignore this email.</p>")
    return _send(email, subject, text, html_body, kind="verify", link=link)


def send_password_reset_email(email: str, token: str) -> bool:
    link = f"{settings.app_base_url}/reset-password?token={quote(token)}"
    subject = "Reset your AEOMirror password"
    text = ("We received a request to reset your AEOMirror password.\n\n"
            f"Reset it here:\n{link}\n\n"
            "This link expires in 1 hour. If you didn't request this, ignore this email.")
    html_body = _wrap(
        "<p>We received a request to reset your AEOMirror password.</p>"
        + _btn(link, "Reset password")
        + "<p style=\"color:#555;font-size:13px\">This link expires in 1 hour. "
          "If you didn't request this, you can safely ignore it.</p>")
    return _send(email, subject, text, html_body, kind="reset", link=link)


def send_invitation_email(email: str, token: str, org_name: str, role: str,
                          inviter_name: str | None = None) -> bool:
    link = f"{settings.app_base_url}/accept-invitation?token={quote(token)}"
    who = f"{inviter_name} " if inviter_name else ""
    subject = f"You're invited to join {org_name} on AEOMirror"
    text = (f"{who}invited you to join {org_name} on AEOMirror as a {role}.\n\n"
            f"Accept your invitation:\n{link}\n\n"
            "This invitation expires in 7 days.")
    safe_org = html.escape(org_name)
    html_body = _wrap(
        f"<p>{html.escape(who)}invited you to join <strong>{safe_org}</strong> "
        f"on AEOMirror as a <strong>{html.escape(role)}</strong>.</p>"
        + _btn(link, "Accept invitation")
        + "<p style=\"color:#555;font-size:13px\">This invitation expires in 7 days.</p>")
    return _send(email, subject, text, html_body, kind="invite", link=link)
