"""Billing API (Phase 9). Plans, checkout, provider webhooks, subscription
management, payments and invoices.

Security: webhook signatures are verified by the provider before any state change;
frontend-reported payment status is never trusted (only verified events mutate
state); duplicate events are ignored via payment_events. Purchasing/management
requires org owner/admin (org:update).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_context, require_permission
from app.billing import entitlements, service
from app.billing.providers import get_provider
from app.billing.providers.base import WebhookError
from app.config import settings
from app.db.models import Invoice, Payment, Plan, Subscription
from app.db.session import get_db
from app.schemas.billing import CheckoutRequest, DevCompleteRequest

router = APIRouter(prefix="/billing", tags=["billing"])


# ------------------------------- serializers -------------------------------
def _plan_out(p: Plan) -> dict:
    return {"code": p.code, "name": p.name, "description": p.description,
            "price_cents": p.price_cents, "currency": p.currency, "interval": p.interval,
            "features": p.features or [], "sort_order": p.sort_order}


def _sub_out(s: Subscription | None) -> dict | None:
    if not s:
        return None
    return {"id": s.id, "plan_code": s.plan_code, "status": s.status,
            "cancel_at_period_end": s.cancel_at_period_end,
            "current_period_start": s.current_period_start,
            "current_period_end": s.current_period_end,
            "canceled_at": s.canceled_at, "provider": s.provider}


def _payment_out(p: Payment) -> dict:
    return {"id": p.id, "kind": p.kind, "plan_code": p.plan_code, "scan_id": p.scan_id,
            "amount_cents": p.amount_cents, "currency": p.currency, "status": p.status,
            "description": p.description, "created_at": p.created_at}


def _invoice_out(i: Invoice) -> dict:
    return {"id": i.id, "number": i.number, "amount_cents": i.amount_cents,
            "currency": i.currency, "status": i.status, "description": i.description,
            "period_start": i.period_start, "period_end": i.period_end,
            "issued_at": i.issued_at, "created_at": i.created_at}


# ------------------------------- plans (public) -------------------------------
@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    rows = (db.query(Plan).filter(Plan.active == True)  # noqa: E712
            .order_by(Plan.sort_order).all())
    return {"plans": [_plan_out(p) for p in rows]}


# ------------------------------- current subscription + usage -------------------------------
@router.get("/subscription")
def get_subscription(ctx: AuthContext = Depends(get_context), db: Session = Depends(get_db)):
    org_id = ctx.org_id
    sub = entitlements.active_subscription(db, org_id)
    return {
        "plan": entitlements.current_plan(db, org_id),
        "entitlements": entitlements.entitlements(db, org_id),
        # Metered quotas so the frontend can render usage meters. scans/monitors/
        # compares are each {limit, used, remaining, unlimited}; bulk_trial is the
        # one-time-ever trial as {available: bool}.
        "usage": {
            "scans": entitlements.scan_quota(db, org_id),
            "monitors": entitlements.monitor_quota(db, org_id),
            "compares": entitlements.compare_quota(db, org_id),
            "bulk_trial": {"available": entitlements.bulk_trial_available(db, org_id)},
        },
        "subscription": _sub_out(sub),
    }


# ------------------------------- checkout -------------------------------
@router.post("/checkout")
def checkout(body: CheckoutRequest,
             ctx: AuthContext = Depends(require_permission("org:update")),
             db: Session = Depends(get_db)):
    if not ctx.org:
        raise HTTPException(status_code=404, detail="No organization for this account.")
    try:
        result = service.create_checkout(
            db, org=ctx.org, user=ctx.user, plan_code=body.plan_code,
            scan_id=body.scan_id, provider_name=body.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")
    return result


@router.post("/checkout/complete")
def complete_checkout_dev(body: DevCompleteRequest,
                          ctx: AuthContext = Depends(require_permission("org:update")),
                          db: Session = Depends(get_db)):
    """Dev/test only completion of a pending checkout (simulates the provider
    webhook). Disabled in production and when a real Stripe key is configured."""
    if settings.is_production or settings.stripe_configured:
        raise HTTPException(status_code=404, detail="Not found.")
    # Only allow completing a payment that belongs to the caller's org.
    payment = db.query(Payment).filter(Payment.reference == body.reference).first()
    if not payment or payment.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Checkout reference not found.")
    service.simulate_completion(db, body.reference)
    return {"ok": True, "status": "completed"}


# ------------------------------- webhooks -------------------------------
@router.post("/webhooks/{provider_name}")
async def webhook(provider_name: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature") or request.headers.get("x-signature")
    try:
        provider = get_provider(provider_name)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown provider.")
    try:
        event = provider.verify_and_parse_webhook(payload, signature)
    except WebhookError as e:
        # Invalid signature / payload — never process.
        raise HTTPException(status_code=400, detail=str(e))
    try:
        outcome = service.process_event(db, event)
    except Exception:
        # Signature was valid but processing failed; 500 asks the provider to retry.
        raise HTTPException(status_code=500, detail="Event processing failed.")
    return {"received": True, "outcome": outcome}


# ------------------------------- subscription management -------------------------------
@router.patch("/subscription/cancel")
def cancel(ctx: AuthContext = Depends(require_permission("org:update")),
           db: Session = Depends(get_db)):
    try:
        sub = service.cancel_subscription(db, ctx.org)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "subscription": _sub_out(sub)}


@router.patch("/subscription/resume")
def resume(ctx: AuthContext = Depends(require_permission("org:update")),
           db: Session = Depends(get_db)):
    try:
        sub = service.resume_subscription(db, ctx.org)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "subscription": _sub_out(sub)}


# ------------------------------- payments + invoices -------------------------------
@router.get("/payments")
def payments(ctx: AuthContext = Depends(get_context), db: Session = Depends(get_db)):
    rows = (db.query(Payment).filter(Payment.organization_id == ctx.org_id)
            .order_by(Payment.created_at.desc()).limit(200).all())
    return {"payments": [_payment_out(p) for p in rows]}


@router.get("/invoices")
def invoices(ctx: AuthContext = Depends(get_context), db: Session = Depends(get_db)):
    rows = (db.query(Invoice).filter(Invoice.organization_id == ctx.org_id)
            .order_by(Invoice.created_at.desc()).limit(200).all())
    return {"invoices": [_invoice_out(i) for i in rows]}
