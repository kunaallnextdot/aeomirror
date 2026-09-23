"""Billing emails (Phase 9): receipt, invoice, subscription activated/cancelled,
renewal reminder, payment failed. Best-effort via Resend; every attempt is recorded
in notification_log (status sent|skipped|failed) so delivery is observable/testable.
Gated by the admin `email` feature flag."""
from __future__ import annotations

import html
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import NotificationLog, Organization, User
from app.services import email_transport

logger = logging.getLogger("aeomirror.billing.email")


def _money(cents: int, currency: str) -> str:
    return f"{currency.upper()} {cents / 100:.2f}"


def _owner_email(db: Session, org: Organization) -> tuple[str | None, str | None]:
    if not org:
        return None, None
    u = db.get(User, org.owner_id)
    return (u.id, u.email) if u else (None, None)


def _send(to: str, subject: str, text: str, html_body: str, *, kind: str) -> str:
    """Send one billing email via the shared Gmail SMTP transport. Returns
    'sent' | 'skipped' | 'failed'. Never raises."""
    return email_transport.send_email(to, subject, text, html_body,
                                      kind=kind, log_prefix="billing")


def _emit(db: Session, org: Organization, kind: str, subject: str, text: str,
          html_body: str, meta: dict | None = None) -> str:
    # Respect the admin "email" feature flag.
    from app.admin import flags
    user_id, email = _owner_email(db, org)
    if not email or not flags.is_enabled(db, "email"):
        status = "skipped"
    else:
        status = _send(email, subject, text, html_body, kind=kind)
    db.add(NotificationLog(organization_id=org.id if org else None, user_id=user_id,
                           monitor_id=None, kind=kind, channel="email", subject=subject,
                           status=status, meta=meta or {}))
    db.commit()
    return status


def _wrap(body: str) -> str:
    return (f"<div style=\"font-family:system-ui,sans-serif;line-height:1.6;color:#0B0F14\">"
            f"{body}</div>")


def send_receipt(db, org, payment) -> str:
    amt = _money(payment.amount_cents, payment.currency)
    subject = f"Your AEOMirror receipt — {amt}"
    text = (f"Thanks for your payment of {amt}.\n\n{payment.description or ''}\n\n"
            f"Manage billing: {settings.app_base_url}")
    html_body = _wrap(f"<p>Thanks for your payment of <strong>{html.escape(amt)}</strong>.</p>"
                      f"<p>{html.escape(payment.description or '')}</p>"
                      f"<p><a href=\"{settings.app_base_url}\">Manage billing</a></p>")
    return _emit(db, org, "billing_receipt", subject, text, html_body,
                 {"payment_id": payment.id, "amount_cents": payment.amount_cents})


def send_invoice(db, org, invoice) -> str:
    amt = _money(invoice.amount_cents, invoice.currency)
    subject = f"Invoice {invoice.number} — {amt}"
    text = (f"Invoice {invoice.number}\nAmount: {amt}\nStatus: {invoice.status}\n\n"
            f"View invoices: {settings.app_base_url}")
    html_body = _wrap(f"<p>Invoice <strong>{html.escape(invoice.number)}</strong></p>"
                      f"<p>Amount: <strong>{html.escape(amt)}</strong> · {html.escape(invoice.status)}</p>"
                      f"<p><a href=\"{settings.app_base_url}\">View your invoices</a></p>")
    return _emit(db, org, "billing_invoice", subject, text, html_body, {"invoice": invoice.number})


def send_subscription_activated(db, org, subscription) -> str:
    subject = "Your AEOMirror Pro subscription is active"
    text = ("Welcome to Pro! You now get 15 scan jobs a month (each a single page or a "
            "bulk of up to 50 URLs), 10 monitors, unlimited comparisons, full per-page "
            "detail on bulk scans, AI-written reports, Content Insights, weekly report "
            f"emails and team members & roles.\n\n{settings.app_base_url}")
    html_body = _wrap("<p>Welcome to <strong>Pro</strong> 🎉 — you now get <strong>15 scan "
                      "jobs a month</strong> (each a single page or a bulk of up to 50 URLs), "
                      "10 monitors, unlimited comparisons, full per-page detail on bulk scans, "
                      "AI-written reports, Content Insights, weekly report emails and team "
                      "members &amp; roles.</p>"
                      f"<p><a href=\"{settings.app_base_url}\">Open your dashboard</a></p>")
    return _emit(db, org, "subscription_activated", subject, text, html_body,
                 {"subscription_id": subscription.id})


def send_subscription_cancelled(db, org, subscription) -> str:
    subject = "Your AEOMirror subscription has been cancelled"
    when = subscription.current_period_end.date().isoformat() if subscription.current_period_end else "the end of the period"
    text = (f"Your Pro subscription is cancelled and will remain active until {when}.\n\n"
            f"Changed your mind? Resume anytime: {settings.app_base_url}")
    html_body = _wrap(f"<p>Your Pro subscription is cancelled and will stay active until "
                      f"<strong>{html.escape(str(when))}</strong>.</p>"
                      f"<p><a href=\"{settings.app_base_url}\">Resume your subscription</a></p>")
    return _emit(db, org, "subscription_cancelled", subject, text, html_body,
                 {"subscription_id": subscription.id})


def send_renewal_reminder(db, org, subscription) -> str:
    subject = "Your AEOMirror Pro subscription renews soon"
    when = subscription.current_period_end.date().isoformat() if subscription.current_period_end else "soon"
    text = f"Your Pro subscription renews on {when}.\n\n{settings.app_base_url}"
    html_body = _wrap(f"<p>Your Pro subscription renews on <strong>{html.escape(str(when))}</strong>.</p>"
                      f"<p><a href=\"{settings.app_base_url}\">Manage billing</a></p>")
    return _emit(db, org, "renewal_reminder", subject, text, html_body,
                 {"subscription_id": subscription.id})


def send_payment_failed(db, org, subscription=None) -> str:
    subject = "Action needed: your AEOMirror payment failed"
    text = ("We couldn't process your latest payment. Please update your payment method "
            f"to keep Pro active.\n\n{settings.app_base_url}")
    html_body = _wrap("<p>We couldn't process your latest payment. Please update your "
                      "payment method to keep Pro active.</p>"
                      f"<p><a href=\"{settings.app_base_url}\">Update payment method</a></p>")
    return _emit(db, org, "payment_failed", subject, text, html_body,
                 {"subscription_id": getattr(subscription, "id", None)})
