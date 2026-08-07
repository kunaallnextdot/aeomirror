"""Payment-provider registry (Phase 9). Providers are pluggable so Razorpay/Paddle
can be added without touching the billing service — implement PaymentProvider and
register it here."""
from __future__ import annotations

from app.billing.providers.base import PaymentProvider
from app.billing.providers.stripe_provider import StripeProvider

_PROVIDERS: dict[str, PaymentProvider] = {
    "stripe": StripeProvider(),
    # "razorpay": RazorpayProvider(),   # future
    # "paddle": PaddleProvider(),       # future
}


def get_provider(name: str | None = None) -> PaymentProvider:
    from app.config import settings
    key = (name or settings.payment_provider or "stripe").lower()
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise ValueError(f"Unknown payment provider: {key}")
    return provider


def provider_names() -> list[str]:
    return list(_PROVIDERS.keys())
