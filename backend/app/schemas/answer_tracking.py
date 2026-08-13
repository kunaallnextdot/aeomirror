"""Request schemas for AI Answer Tracking (Part A). Responses are plain dicts built
in the routes (consistent with the monitoring endpoints)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreatePromptSetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    monitor_id: str | None = None          # optional link to a Monitor (must be same org)


class UpdatePromptSetRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    monitor_id: str | None = None          # set/clear the linked monitor


class CreatePromptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class UpdatePromptRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    is_active: bool | None = None
