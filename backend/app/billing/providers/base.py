"""Payment-provider interface (Phase 9). A provider turns a checkout request into a
hosted checkout URL and verifies + parses incoming webhooks into a normalized
ProviderEvent. The billing service never talks to a provider SDK directly."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class WebhookError(Exception):
    """Raised when a webhook signature is missing/invalid or unparseable."""


@dataclass
class CheckoutResult:
    checkout_url: str
    provider_ref: str | None = None
    dev_mode: bool = False


@dataclass
class ProviderEvent:
    """Normalized webhook event across providers."""
    id: str
    type: str                       # normalized: payment_succeeded | subscription_created | ...
    data: dict = field(default_factory=dict)
    provider: str = "stripe"
    raw_type: str | None = None     # the provider's original event type


class PaymentProvider(ABC):
    name: str = "base"

    @abstractmethod
    def create_checkout(self, *, reference: str, kind: str, plan_code: str,
                        amount_cents: int, currency: str, customer_email: str | None,
                        success_url: str, cancel_url: str,
                        metadata: dict) -> CheckoutResult:
        ...

    @abstractmethod
    def verify_and_parse_webhook(self, payload: bytes, signature: str | None) -> ProviderEvent:
        ...
