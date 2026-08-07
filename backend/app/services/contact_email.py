"""Contact & Support emails (Gmail SMTP only).

Two transactional emails, both best-effort (they never raise into the request):

  EMAIL #1  notification  -> CONTACT_EMAIL   ("New Contact Form Submission")
  EMAIL #2  confirmation  -> the submitter   ("We've received your message")

All user-controlled values are HTML-escaped for the HTML part and single-lined
for header values, so a submission can neither inject HTML into the inbox view
nor forge extra email headers.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from app.config import settings
from app.core.email_smtp import send_email
from app.db.models import Contact

logger = logging.getLogger("aeomirror.contact_email")


def _fmt_time(dt: datetime | None) -> str:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _wrap(body_html: str) -> str:
    return (f"<div style=\"font-family:system-ui,-apple-system,sans-serif;"
            f"line-height:1.6;color:#0B0F14\">{body_html}</div>")


def send_contact_notification(contact: Contact) -> bool:
    """EMAIL #1 — notify the support mailbox of a new submission."""
    recipient = settings.contact_recipient
    if not recipient:
        logger.info("No CONTACT_EMAIL/GMAIL_USER configured; skipping notification.")
        return False

    website = contact.website or "—"
    submitted = _fmt_time(contact.created_at)

    subject = "New Contact Form Submission"
    text_body = (
        "New contact form submission on AEOMirror.\n\n"
        f"Name:            {contact.name}\n"
        f"Email:           {contact.email}\n"
        f"Website:         {website}\n"
        f"Subject:         {contact.subject}\n"
        f"Submission Time: {submitted}\n\n"
        "Message:\n"
        f"{contact.message}\n"
    )
    html_body = _wrap(
        "<h2 style=\"margin:0 0 12px\">New Contact Form Submission</h2>"
        "<table style=\"border-collapse:collapse;font-size:14px\">"
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#555\"><strong>Name</strong></td><td>{html.escape(contact.name)}</td></tr>"
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#555\"><strong>Email</strong></td><td>{html.escape(contact.email)}</td></tr>"
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#555\"><strong>Website</strong></td><td>{html.escape(website)}</td></tr>"
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#555\"><strong>Subject</strong></td><td>{html.escape(contact.subject)}</td></tr>"
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#555\"><strong>Submission Time</strong></td><td>{html.escape(submitted)}</td></tr>"
        "</table>"
        "<p style=\"margin:16px 0 6px;color:#555\"><strong>Message</strong></p>"
        f"<p style=\"white-space:pre-wrap;background:#f4f6f8;padding:12px;border-radius:8px\">{html.escape(contact.message)}</p>"
    )
    # Reply-To the submitter so support can reply straight from the notification.
    return send_email(to=recipient, subject=subject, text_body=text_body,
                      html_body=html_body, reply_to=contact.email)


def send_contact_confirmation(contact: Contact) -> bool:
    """EMAIL #2 — auto-confirmation to the person who submitted the form."""
    subject = "We've received your message"
    text_body = (
        f"Hi {contact.name},\n\n"
        "Thank you for contacting AEOMirror.\n\n"
        "We've successfully received your message.\n\n"
        "Our support team will reply within 24 hours.\n\n"
        "Regards,\n"
        "AEOMirror Team\n"
    )
    html_body = _wrap(
        f"<p>Hi {html.escape(contact.name)},</p>"
        "<p>Thank you for contacting <strong>AEOMirror</strong>.</p>"
        "<p>We've successfully received your message.</p>"
        "<p>Our support team will reply within 24 hours.</p>"
        "<p style=\"margin-top:18px\">Regards,<br/>AEOMirror Team</p>"
    )
    return send_email(to=contact.email, subject=subject, text_body=text_body,
                      html_body=html_body)
