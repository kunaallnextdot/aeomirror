"""Contact service: persist a support request, then send the two emails.

Separation of concerns:
  - validation      -> app.schemas.contact.ContactRequest
  - transport        -> app.core.email_smtp (Gmail SMTP)
  - email content    -> app.services.contact_email
  - persistence      -> this module + app.db.models.Contact

The request is stored FIRST and always; both emails are strictly best-effort and
can never fail the submission (a submission is not lost because email is down).
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

from app.db.models import Contact
from app.schemas.contact import ContactRequest
from app.services.contact_email import (
    send_contact_confirmation, send_contact_notification,
)

logger = logging.getLogger("aeomirror.contact")

# Defensive control-character stripping applied to every stored value on top of the
# schema-level validation (belt and suspenders against odd Unicode control chars).
_CTRL = {c: None for c in range(0, 32) if c not in (9, 10)}  # keep \t and \n


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    return value.translate(_CTRL).strip()


def create_contact(db: Session, data: ContactRequest, *, ip_hash: str | None = None) -> Contact:
    """Store the submission and return the persisted row.

    Storage happens synchronously (a submission is never lost). Email delivery is
    NOT done here — the SMTP round-trips are slow and must never block the request;
    the controller schedules `deliver_emails` as a background task instead."""
    contact = Contact(
        name=_clean(data.name),
        email=_clean(str(data.email)).lower(),
        website=_clean(data.website),
        subject=_clean(data.subject),
        message=_clean(data.message),
        ip_hash=ip_hash,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def deliver_emails(contact: Contact) -> None:
    """Send the notification + confirmation emails. Best-effort: every failure is
    logged (type only) and never raised. Safe to run off-request with a detached
    Contact (only already-loaded attributes are read; no DB access)."""
    try:
        send_contact_notification(contact)
    except Exception as e:
        logger.warning("contact notification raised: %s", type(e).__name__)
    try:
        send_contact_confirmation(contact)
    except Exception as e:
        logger.warning("contact confirmation raised: %s", type(e).__name__)


def schedule_email_delivery(contact: Contact) -> None:
    """Fire-and-forget the emails on a daemon thread so the SMTP round-trips never
    block or slow the HTTP response. A thread (rather than FastAPI BackgroundTasks)
    is used deliberately: the app's request-logging BaseHTTPMiddleware is not
    compatible with response-attached background tasks in this Starlette version."""
    threading.Thread(target=deliver_emails, args=(contact,), daemon=True).start()
