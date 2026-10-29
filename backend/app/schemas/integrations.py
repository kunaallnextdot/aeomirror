"""Request schemas for the Google Analytics integration API (P0)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SelectGa4PropertyRequest(BaseModel):
    property_id: str = Field(min_length=1, max_length=64)


class LinkGa4WebsiteRequest(BaseModel):
    # null unlinks. Validated server-side against the caller's own organization.
    monitor_id: str | None = Field(default=None, max_length=64)
