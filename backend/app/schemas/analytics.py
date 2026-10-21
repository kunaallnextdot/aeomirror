"""Request schemas for first-party website analytics (AI Business Impact)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.db.models import CONVERSION_TYPES

_VALID_CONVERSION_TYPES = set(CONVERSION_TYPES)


class TrackEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    type: Literal["pageview", "conversion"]
    session_id: str = Field(min_length=1, max_length=100)
    visitor_id: str | None = Field(default=None, max_length=100)
    url: str = Field(min_length=1, max_length=2000)
    referrer: str | None = Field(default=None, max_length=2000)
    utm_source: str | None = Field(default=None, max_length=200)
    utm_medium: str | None = Field(default=None, max_length=200)
    utm_campaign: str | None = Field(default=None, max_length=200)
    # conversion-only fields:
    event_type: str | None = Field(default=None, max_length=50)
    event_name: str | None = Field(default=None, max_length=200)
    value_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, max_length=10)
    external_id: str | None = Field(default=None, max_length=200)


class TrackBatch(BaseModel):
    site_id: str = Field(min_length=1, max_length=100)
    events: list[TrackEvent] = Field(min_length=1)

    @field_validator("events")
    @classmethod
    def _cap_batch_size(cls, v: list[TrackEvent]) -> list[TrackEvent]:
        if len(v) > settings.tracking_max_events_per_batch:
            raise ValueError(f"at most {settings.tracking_max_events_per_batch} events per batch")
        return v


class ConfirmConversionRequest(BaseModel):
    """Body for the trusted, server-to-server POST /monitors/{id}/tracking/confirm —
    authenticated with the per-site tracking API key, never the public site_id alone."""
    event_type: Literal["purchase", "lead_submitted", "booking_confirmed",
                        "signup_completed", "contact_submitted", "custom"]
    external_id: str = Field(min_length=1, max_length=200)
    event_name: str | None = Field(default=None, max_length=200)
    value_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, max_length=10)
    session_id: str | None = Field(default=None, max_length=100)
