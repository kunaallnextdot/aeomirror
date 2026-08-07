"""Request schemas for the billing API (Phase 9)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    plan_code: str = Field(description="'pro' (subscription) or 'report' (one-time)")
    scan_id: str | None = None          # required for 'report'
    provider: str | None = None         # defaults to configured provider


class DevCompleteRequest(BaseModel):
    reference: str
