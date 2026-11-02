"""Request schemas for the Google Analytics integration API (P0)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SelectGa4PropertyRequest(BaseModel):
    property_id: str = Field(min_length=1, max_length=64)


class LinkGa4WebsiteRequest(BaseModel):
    # null unlinks. Validated server-side against the caller's own organization.
    monitor_id: str | None = Field(default=None, max_length=64)


class Ga4EventMappingItem(BaseModel):
    event_name: str = Field(min_length=1, max_length=40)
    role: str = Field(min_length=1, max_length=20)          # conversion | funnel_step
    conversion_type: str | None = Field(default=None, max_length=20)
    label: str | None = Field(default=None, max_length=80)
    counts_revenue: bool = False


class Ga4EventMappingsRequest(BaseModel):
    # Replaces the whole mapping set for the connected property (an empty list clears it).
    mappings: list[Ga4EventMappingItem] = Field(default_factory=list, max_length=50)


class SelectSearchConsoleSiteRequest(BaseModel):
    # "https://www.example.com/" or "sc-domain:example.com"; checked against the
    # connected account's own verified properties before it is stored.
    site_url: str = Field(min_length=1, max_length=512)
