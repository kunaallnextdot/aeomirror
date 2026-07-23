"""Lead capture + welcome email.

The lead is stored FIRST and always; the email is strictly best-effort and can
never fail the user's scan or the lead write. Email uses Resend (INTEGRATIONS.md
B1) when RESEND_API_KEY + EMAIL_FROM are configured; otherwise it is skipped with
a log line. A welcome email is sent only when the lead is newly created, so repeat
submissions of the same email do not re-send.
"""
from __future__ import annotations

import html
import logging

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Lead, Scan

logger = logging.getLogger("aeomirror.leads")

RESEND_ENDPOINT = "https://api.resend.com/emails"


def _normalize(url: str) -> str:
    return (url.strip().lower()
            .replace("https://", "").replace("http://", "")
            .replace("www.", "").rstrip("/"))


def capture_lead(db: Session, email: str, url: str | None) -> Lead:
    email = email.strip().lower()
    lead = db.query(Lead).filter(Lead.email == email).first()
    is_new = lead is None
    if lead:
        lead.scan_count += 1
    else:
        lead = Lead(email=email, first_scanned_url=url, scan_count=1)
        db.add(lead)
    db.commit()
    db.refresh(lead)

    # Send the welcome email only on first capture (dedupe repeat submissions).
    if is_new:
        context = _latest_scan_context(db, url)
        try:
            send_welcome_email(email, **context)
        except Exception as e:  # belt-and-suspenders: never surface email errors
            logger.warning("welcome email raised unexpectedly: %s", type(e).__name__)
    return lead


def _latest_scan_context(db: Session, url: str | None) -> dict:
    """Best-effort enrichment: most recent scan for this URL, if any."""
    ctx: dict = {"domain": None, "ars": None, "top_issues": None}
    if not url:
        return ctx
    try:
        row = (db.query(Scan)
               .filter(Scan.normalized_url == _normalize(url))
               .order_by(Scan.created_at.desc())
               .first())
        if row:
            ctx.update(domain=_normalize(row.url), ars=row.ars,
                       top_issues=(row.result or {}).get("top_issues"))
    except Exception as e:
        logger.warning("lead email enrichment lookup failed: %s", type(e).__name__)
    return ctx


def send_welcome_email(email: str, *, domain=None, ars=None,
                       top_issues=None) -> bool:
    """Send the welcome/report email via Resend. Best-effort: returns True on a
    2xx send, False otherwise (missing config, provider error). Never raises out.

    All user-controlled values (domain, issue text) are HTML-escaped. No secrets
    or internal scan details are included or logged.
    """
    if not settings.email_enabled:
        logger.info("Email not configured (RESEND_API_KEY/EMAIL_FROM); skipping send.")
        return False

    subject, text_body, html_body = _render_welcome(domain, ars, top_issues)
    try:
        resp = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.email_from,
                "to": [email],
                "subject": subject,
                "text": text_body,
                "html": html_body,
            },
            timeout=10.0,
        )
        if resp.status_code // 100 == 2:
            return True
        # Do not log the response body (may echo the request); status only.
        logger.warning("Resend send failed: HTTP %s", resp.status_code)
        return False
    except Exception as e:
        logger.warning("Resend send error: %s", type(e).__name__)
        return False


def _render_welcome(domain, ars, top_issues):
    """Build (subject, text, html). Escapes user-controlled values for HTML."""
    dash_url = settings.app_base_url
    safe_domain = html.escape(str(domain)) if domain else "your site"
    raw_domain = str(domain) if domain else "your site"
    score_line = f"AI Readiness Score: {int(ars)}/100" if ars is not None else \
        "Your AI Readiness Score is ready."

    issues = []
    for it in (top_issues or [])[:3]:
        label = it.get("label") if isinstance(it, dict) else None
        fix = it.get("fix_hint") if isinstance(it, dict) else None
        if label:
            issues.append((label, fix or ""))

    subject = (f"Your AEOMirror scan for {raw_domain}: {int(ars)}/100"
               if ars is not None else "Your AEOMirror scan is ready")

    # ---- plain text ----
    lines = [f"Thanks for scanning {raw_domain} with AEOMirror.", "", score_line, ""]
    if issues:
        lines.append("Top things to fix:")
        lines += [f"  - {lbl}: {fx}" if fx else f"  - {lbl}" for lbl, fx in issues]
        lines.append("")
    lines += [f"See your full report and fixes: {dash_url}", "",
              "Want prompt-level answer visibility and generated fixes? Reply to this "
              "email and we'll help you get started."]
    text_body = "\n".join(lines)

    # ---- simple HTML (all dynamic values escaped) ----
    issues_html = ""
    if issues:
        items = "".join(
            f"<li><strong>{html.escape(lbl)}</strong>"
            + (f" — {html.escape(fx)}" if fx else "") + "</li>"
            for lbl, fx in issues
        )
        issues_html = f"<p>Top things to fix:</p><ul>{items}</ul>"
    html_body = (
        f"<div style=\"font-family:system-ui,sans-serif;line-height:1.5\">"
        f"<p>Thanks for scanning <strong>{safe_domain}</strong> with AEOMirror.</p>"
        f"<p style=\"font-size:18px\"><strong>{html.escape(score_line)}</strong></p>"
        f"{issues_html}"
        f"<p><a href=\"{html.escape(dash_url)}\">See your full report and fixes</a></p>"
        f"<p style=\"color:#555\">Want prompt-level answer visibility and generated "
        f"fixes? Just reply — we'll help you get started.</p>"
        f"</div>"
    )
    return subject, text_body, html_body
