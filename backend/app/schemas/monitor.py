"""Request schemas for the monitoring API (Phase 7). Responses are plain dicts
(built in routes_monitors) to keep the nested history/trend payloads flexible."""
from __future__ import annotations

from pydantic import BaseModel, Field

FREQUENCIES = ("daily", "weekly", "monthly", "manual")


class CreateMonitorRequest(BaseModel):
    url: str = Field(min_length=3, max_length=2048)
    frequency: str = "weekly"
    name: str | None = Field(default=None, max_length=120)


class UpdateMonitorRequest(BaseModel):
    frequency: str | None = None
    status: str | None = None          # active | paused
    name: str | None = Field(default=None, max_length=120)
    digest_enabled: bool | None = None  # include this monitor in the weekly digest


class AcknowledgeAlertRequest(BaseModel):
    pass
