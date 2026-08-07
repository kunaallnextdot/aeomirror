"""Stripe provider (Phase 9).

- create_checkout: when a Stripe key is configured, creates a real Checkout Session
  via the Stripe API; otherwise (dev/test) returns a URL to the local checkout-
  completion page so the flow is exercisable without Stripe.
- verify_and_parse_webhook: verifies the `Stripe-Signature` header using the webhook
  signing secret (the real Stripe scheme, implemented here so it is testable
  offline) and normalizes the event.

Signature scheme: header "t=<ts>,v1=<hexdigest>", digest = HMAC-SHA256(secret,
f"{ts}.{payload}").
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from app.billing.providers.base import (
    CheckoutResult, PaymentProvider, ProviderEvent, WebhookError,
)
from app.config import settings

logger = logging.getLogger("aeomirror.billing.stripe")

STRIPE_API = "https://api.stripe.com/v1/checkout/sessions"
_SIG_TOLERANCE = 60 * 60 * 24   # 24h — generous; we don't reject on age in dev

# Stripe event type -> our normalized type.
EVENT_MAP = {
    "checkout.session.completed": "checkout_completed",
    "invoice.paid": "invoice_paid",
    "invoice.payment_succeeded": "invoice_paid",
    "customer.subscription.created": "subscription_created",
    "customer.subscription.updated": "subscription_updated",
    "customer.subscription.deleted": "subscription_cancelled",
    "invoice.payment_failed": "payment_failed",
    "charge.refunded": "refund",
    "charge.dispute.created": "chargeback",
}


def make_stripe_signature(payload: bytes, secret: str, timestamp: int) -> str:
    signed = f"{timestamp}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class StripeProvider(PaymentProvider):
    name = "stripe"

    def create_checkout(self, *, reference, kind, plan_code, amount_cents, currency,
                        customer_email, success_url, cancel_url, metadata) -> CheckoutResult:
        if not settings.stripe_configured:
            # Dev/test: the frontend completion page drives /billing/checkout/complete.
            url = f"{settings.app_base_url}/billing/complete?ref={reference}"
            return CheckoutResult(checkout_url=url, provider_ref=None, dev_mode=True)

        mode = "subscription" if kind == "subscription" else "payment"
        data = {
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": reference,
            "metadata[reference]": reference,
        }
        for k, v in (metadata or {}).items():
            data[f"metadata[{k}]"] = str(v)
        if customer_email:
            data["customer_email"] = customer_email
        if mode == "subscription" and settings.stripe_price_pro:
            data["line_items[0][price]"] = settings.stripe_price_pro
            data["line_items[0][quantity]"] = "1"
        else:
            data["line_items[0][price_data][currency]"] = currency
            data["line_items[0][price_data][product_data][name]"] = plan_code
            data["line_items[0][price_data][unit_amount]"] = str(amount_cents)
            data["line_items[0][quantity]"] = "1"
        try:
            resp = httpx.post(STRIPE_API, data=data,
                              auth=(settings.stripe_secret_key, ""), timeout=15.0)
            resp.raise_for_status()
            body = resp.json()
            return CheckoutResult(checkout_url=body["url"], provider_ref=body["id"], dev_mode=False)
        except Exception as e:  # surfaced by the endpoint as a 502
            logger.warning("Stripe checkout failed: %s", type(e).__name__)
            raise

    def verify_and_parse_webhook(self, payload: bytes, signature: str | None) -> ProviderEvent:
        secret = settings.stripe_webhook_secret
        if not secret:
            raise WebhookError("Webhook secret is not configured.")
        if not signature:
            raise WebhookError("Missing signature header.")
        parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
        ts, sig = parts.get("t"), parts.get("v1")
        if not ts or not sig:
            raise WebhookError("Malformed signature header.")
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise WebhookError("Signature verification failed.")

        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception:
            raise WebhookError("Invalid JSON payload.")
        raw_type = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        return ProviderEvent(
            id=event.get("id") or "", type=EVENT_MAP.get(raw_type, raw_type),
            data=obj, provider="stripe", raw_type=raw_type,
        )
