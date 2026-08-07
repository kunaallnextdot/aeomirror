"""Billing service (Phase 9): checkout creation, webhook event processing, invoice
generation and subscription lifecycle. All provider webhooks funnel through the
single, idempotent `process_event` so state changes are consistent and a duplicate
webhook can never double-apply. Frontend-reported status is never trusted — only
verified provider events (or the dev-completion path) change state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.billing import emails
from app.billing.plans import PLAN_DEFS, PLAN_PRO, PLAN_REPORT, plan_price_cents
from app.billing.providers import get_provider
from app.billing.providers.base import ProviderEvent
from app.config import settings
from app.core.security import generate_token
from app.db.models import (
    KIND_ONE_TIME_REPORT, KIND_SUBSCRIPTION, PAY_DISPUTED, PAY_FAILED,
    PAY_PENDING, PAY_REFUNDED, PAY_SUCCEEDED,
    SUB_ACTIVE, SUB_CANCELED, SUB_PAST_DUE, Invoice, Organization, Payment,
    PaymentEvent, Scan, Subscription,
)

logger = logging.getLogger("aeomirror.billing")

_PERIOD_DAYS = 30


# ------------------------------- checkout -------------------------------
def create_checkout(db: Session, *, org: Organization, user, plan_code: str,
                    scan_id: str | None = None, provider_name: str | None = None) -> dict:
    plan = next((p for p in PLAN_DEFS if p["code"] == plan_code), None)
    if not plan or plan_code not in (PLAN_PRO, PLAN_REPORT):
        raise ValueError("Only 'pro' and 'report' are purchasable.")

    kind = KIND_SUBSCRIPTION if plan_code == PLAN_PRO else KIND_ONE_TIME_REPORT
    if kind == KIND_ONE_TIME_REPORT:
        if not scan_id:
            raise ValueError("A scan_id is required to buy a one-time report.")
        scan = db.get(Scan, scan_id)
        if not scan or scan.organization_id != org.id:
            raise ValueError("Scan not found for this organization.")

    if plan_code == PLAN_PRO and current_active_pro(db, org.id):
        raise ValueError("This organization already has an active Pro subscription.")

    amount = plan_price_cents(plan_code)
    reference = generate_token()[:32]
    payment = Payment(
        organization_id=org.id, user_id=getattr(user, "id", None),
        provider=(provider_name or settings.payment_provider), kind=kind,
        plan_code=plan_code, scan_id=scan_id, amount_cents=amount,
        currency=settings.billing_currency, status=PAY_PENDING, reference=reference,
        description=f"{plan['name']} purchase",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    provider = get_provider(provider_name)
    result = provider.create_checkout(
        reference=reference, kind=kind, plan_code=plan_code, amount_cents=amount,
        currency=settings.billing_currency,
        customer_email=getattr(user, "email", None),
        success_url=f"{settings.app_base_url}/billing?status=success",
        cancel_url=f"{settings.app_base_url}/billing?status=cancelled",
        metadata={"reference": reference, "org_id": org.id,
                  "plan": plan_code, "kind": kind, "scan_id": scan_id or ""},
    )
    if result.provider_ref:
        payment.provider_payment_id = result.provider_ref
        db.commit()
    return {"checkout_url": result.checkout_url, "reference": reference,
            "payment_id": payment.id, "dev_mode": result.dev_mode}


def current_active_pro(db: Session, org_id: str) -> Subscription | None:
    return (db.query(Subscription)
            .filter(Subscription.organization_id == org_id,
                    Subscription.plan_code == PLAN_PRO,
                    Subscription.status == SUB_ACTIVE)
            .first())


# ------------------------------- invoices -------------------------------
def _next_invoice_number(db: Session) -> str:
    year = datetime.utcnow().year
    seq = (db.query(func.count(Invoice.id)).scalar() or 0) + 1
    return f"AEO-{year}-{seq:05d}"


def _create_invoice(db: Session, *, org_id, payment, subscription=None,
                    period_start=None, period_end=None) -> Invoice:
    inv = Invoice(
        organization_id=org_id,
        subscription_id=getattr(subscription, "id", None),
        payment_id=getattr(payment, "id", None),
        number=_next_invoice_number(db),
        amount_cents=payment.amount_cents, currency=payment.currency,
        status="paid", period_start=period_start, period_end=period_end,
        description=payment.description,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


# ------------------------------- webhook processing -------------------------------
def _ref_from(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    return (data.get("reference") or data.get("client_reference_id")
            or (data.get("metadata") or {}).get("reference"))


def process_event(db: Session, event: ProviderEvent) -> str:
    """Idempotently apply a normalized provider event. Returns a short status
    ('processed' | 'duplicate' | 'ignored')."""
    # Idempotency: a re-delivered event with the same id is a no-op.
    if event.id:
        existing = (db.query(PaymentEvent)
                    .filter(PaymentEvent.provider_event_id == event.id).first())
        if existing and existing.processed:
            return "duplicate"
    row = PaymentEvent(provider=event.provider, provider_event_id=event.id or None,
                       type=event.type, payload=event.data, processed=False)
    db.add(row)
    db.commit()

    try:
        handler = _HANDLERS.get(event.type)
        if handler:
            handler(db, event.data)
            outcome = "processed"
        else:
            outcome = "ignored"
        row.processed = True
        row.error = None
        db.commit()
        return outcome
    except Exception as e:
        db.rollback()
        row.error = f"{type(e).__name__}: {e}"[:400]
        db.commit()
        logger.warning("billing event %s failed: %s", event.type, type(e).__name__)
        raise


def _org(db, org_id):
    return db.get(Organization, org_id)


def _on_checkout_completed(db: Session, data: dict) -> None:
    ref = _ref_from(data)
    payment = (db.query(Payment).filter(Payment.reference == ref).first()) if ref else None
    if not payment:
        logger.info("checkout_completed: no matching pending payment for ref=%s", ref)
        return
    if payment.status == PAY_SUCCEEDED:
        return  # already applied
    payment.status = PAY_SUCCEEDED
    payment.updated_at = datetime.utcnow()
    db.commit()
    org = _org(db, payment.organization_id)

    if payment.kind == KIND_SUBSCRIPTION:
        now = datetime.utcnow()
        sub = current_active_pro(db, payment.organization_id)
        if not sub:
            sub = Subscription(organization_id=payment.organization_id, plan_code=PLAN_PRO,
                               status=SUB_ACTIVE, provider=payment.provider,
                               provider_subscription_id=data.get("subscription") or f"dev_sub_{payment.id}",
                               provider_customer_id=data.get("customer"),
                               current_period_start=now, current_period_end=now + timedelta(days=_PERIOD_DAYS),
                               cancel_at_period_end=False)
            db.add(sub)
        else:
            sub.status = SUB_ACTIVE
            sub.current_period_start = now
            sub.current_period_end = now + timedelta(days=_PERIOD_DAYS)
            sub.cancel_at_period_end = False
        db.commit()
        db.refresh(sub)
        inv = _create_invoice(db, org_id=org.id, payment=payment, subscription=sub,
                              period_start=sub.current_period_start, period_end=sub.current_period_end)
        emails.send_subscription_activated(db, org, sub)
        emails.send_receipt(db, org, payment)
        emails.send_invoice(db, org, inv)
    else:  # one-time report unlock — does NOT change the org's plan
        inv = _create_invoice(db, org_id=org.id, payment=payment)
        emails.send_receipt(db, org, payment)
        emails.send_invoice(db, org, inv)


def _on_invoice_paid(db: Session, data: dict) -> None:
    """Recurring renewal payment for an existing subscription."""
    sub_ref = data.get("subscription")
    sub = (db.query(Subscription).filter(Subscription.provider_subscription_id == sub_ref).first()) if sub_ref else None
    if not sub:
        return
    now = datetime.utcnow()
    sub.status = SUB_ACTIVE
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=_PERIOD_DAYS)
    payment = Payment(organization_id=sub.organization_id, provider=sub.provider,
                      kind=KIND_SUBSCRIPTION, plan_code=PLAN_PRO,
                      amount_cents=data.get("amount_paid") or plan_price_cents(PLAN_PRO),
                      currency=settings.billing_currency, status=PAY_SUCCEEDED,
                      description="Pro subscription renewal")
    db.add(payment)
    db.commit()
    db.refresh(payment)
    org = _org(db, sub.organization_id)
    inv = _create_invoice(db, org_id=sub.organization_id, payment=payment, subscription=sub,
                          period_start=sub.current_period_start, period_end=sub.current_period_end)
    emails.send_receipt(db, org, payment)
    emails.send_invoice(db, org, inv)


def _find_sub(db, data):
    sid = data.get("id") or data.get("subscription")
    if sid:
        s = db.query(Subscription).filter(Subscription.provider_subscription_id == sid).first()
        if s:
            return s
    org_id = (data.get("metadata") or {}).get("org_id")
    if org_id:
        return current_active_pro(db, org_id)
    return None


def _on_subscription_updated(db: Session, data: dict) -> None:
    sub = _find_sub(db, data)
    if not sub:
        return
    status = data.get("status")
    if status in (SUB_ACTIVE, SUB_CANCELED, SUB_PAST_DUE):
        sub.status = status
    if data.get("cancel_at_period_end") is not None:
        sub.cancel_at_period_end = bool(data["cancel_at_period_end"])
    db.commit()


def _on_subscription_cancelled(db: Session, data: dict) -> None:
    sub = _find_sub(db, data)
    if not sub:
        return
    sub.status = SUB_CANCELED
    sub.canceled_at = datetime.utcnow()
    db.commit()
    emails.send_subscription_cancelled(db, _org(db, sub.organization_id), sub)


def _on_payment_failed(db: Session, data: dict) -> None:
    sub = _find_sub(db, data)
    if sub:
        sub.status = SUB_PAST_DUE
        db.commit()
        db.add(Payment(organization_id=sub.organization_id, provider=sub.provider,
                       kind=KIND_SUBSCRIPTION, plan_code=PLAN_PRO,
                       amount_cents=plan_price_cents(PLAN_PRO), currency=settings.billing_currency,
                       status=PAY_FAILED, description="Failed Pro renewal"))
        db.commit()
        emails.send_payment_failed(db, _org(db, sub.organization_id), sub)


def _on_refund(db: Session, data: dict) -> None:
    payment = _payment_from(db, data)
    if not payment:
        return
    payment.status = PAY_REFUNDED
    db.commit()
    if payment.kind == KIND_SUBSCRIPTION:
        sub = current_active_pro(db, payment.organization_id)
        if sub:
            sub.status = SUB_CANCELED
            sub.canceled_at = datetime.utcnow()
            db.commit()


def _on_chargeback(db: Session, data: dict) -> None:
    payment = _payment_from(db, data)
    if not payment:
        return
    payment.status = PAY_DISPUTED
    db.commit()
    sub = current_active_pro(db, payment.organization_id)
    if sub:
        sub.status = SUB_CANCELED
        sub.canceled_at = datetime.utcnow()
        db.commit()


def _payment_from(db, data):
    ref = _ref_from(data)
    if ref:
        p = db.query(Payment).filter(Payment.reference == ref).first()
        if p:
            return p
    pid = data.get("payment_intent") or data.get("charge") or data.get("id")
    if pid:
        return db.query(Payment).filter(Payment.provider_payment_id == pid).first()
    return None


_HANDLERS = {
    "checkout_completed": _on_checkout_completed,
    "invoice_paid": _on_invoice_paid,
    "subscription_created": _on_subscription_updated,
    "subscription_updated": _on_subscription_updated,
    "subscription_cancelled": _on_subscription_cancelled,
    "payment_failed": _on_payment_failed,
    "refund": _on_refund,
    "chargeback": _on_chargeback,
}


# ------------------------------- subscription management -------------------------------
def cancel_subscription(db: Session, org: Organization) -> Subscription:
    sub = current_active_pro(db, org.id)
    if not sub:
        raise ValueError("No active subscription to cancel.")
    sub.cancel_at_period_end = True
    sub.updated_at = datetime.utcnow()
    db.commit()
    emails.send_subscription_cancelled(db, org, sub)
    return sub


def resume_subscription(db: Session, org: Organization) -> Subscription:
    sub = current_active_pro(db, org.id)
    if not sub or not sub.cancel_at_period_end:
        raise ValueError("No cancellation to resume.")
    sub.cancel_at_period_end = False
    sub.updated_at = datetime.utcnow()
    db.commit()
    return sub


# ------------------------------- dev completion -------------------------------
def simulate_completion(db: Session, reference: str) -> str:
    """Dev/test only: complete a pending checkout as if the provider webhook fired.
    Never available in production or when a real Stripe key is configured."""
    event = ProviderEvent(id=f"dev_evt_{reference}", type="checkout_completed",
                          data={"reference": reference}, provider="dev")
    return process_event(db, event)
