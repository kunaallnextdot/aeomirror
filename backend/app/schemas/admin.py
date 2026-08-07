"""Request/response schemas for the admin API (Phase 8)."""
from __future__ import annotations

from pydantic import BaseModel


class UpdateSettingsRequest(BaseModel):
    settings: dict | None = None          # system_settings key -> value
    feature_flags: dict | None = None     # flag name -> bool


class UserActionResult(BaseModel):
    ok: bool
    id: str
    status: str | None = None
