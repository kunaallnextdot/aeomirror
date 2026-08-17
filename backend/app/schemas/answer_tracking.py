"""Request schemas for AI Answer Tracking (Part A). Responses are plain dicts built
in the routes (consistent with the monitoring endpoints)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreatePromptSetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    monitor_id: str | None = None          # optional link to a Monitor (must be same org)
    # Part B — brand identity (seeded from the monitor when omitted; see the route).
    brand_name: str | None = Field(default=None, max_length=200)
    brand_domain: str | None = Field(default=None, max_length=255)
    brand_aliases: list[str] | None = None
    competitor_domains: list[str] | None = Field(default=None, max_length=5)   # max 5 items


class UpdatePromptSetRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    monitor_id: str | None = None          # set/clear the linked monitor
    brand_name: str | None = Field(default=None, max_length=200)
    brand_domain: str | None = Field(default=None, max_length=255)
    brand_aliases: list[str] | None = None
    competitor_domains: list[str] | None = Field(default=None, max_length=5)


class UpdateMonitorBrandRequest(BaseModel):
    """Edit the brand identity that now lives on the monitor (the site)."""
    brand_name: str | None = Field(default=None, max_length=200)
    brand_domain: str | None = Field(default=None, max_length=255)
    brand_aliases: list[str] | None = None
    competitor_domains: list[str] | None = Field(default=None, max_length=5)


class CreatePromptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class UpdatePromptRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    is_active: bool | None = None
