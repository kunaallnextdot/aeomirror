"""Request/response schemas for the Contact & Support system.

Validation (server-side, authoritative — the frontend validates too for UX):
  - name / subject: required, length-bounded, single line
  - email: valid email format (Pydantic EmailStr, backed by email-validator)
  - website: optional, length-bounded
  - message: required, min/max length (from settings)
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.config import settings


class ContactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    website: str | None = Field(default=None, max_length=300)
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=20_000)

    @field_validator("name", "subject")
    @classmethod
    def _single_line(cls, v: str) -> str:
        v = " ".join((v or "").split())  # collapse newlines/runs of whitespace
        if not v:
            raise ValueError("This field is required.")
        return v

    @field_validator("website")
    @classmethod
    def _clean_website(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = "".join(ch for ch in v if ch not in "\r\n").strip()
        return v or None

    @field_validator("message")
    @classmethod
    def _message_length(cls, v: str) -> str:
        v = (v or "").strip()
        lo, hi = settings.contact_message_min_len, settings.contact_message_max_len
        if len(v) < lo:
            raise ValueError(f"Message must be at least {lo} characters.")
        if len(v) > hi:
            raise ValueError(f"Message must be at most {hi} characters.")
        return v


class ContactResponse(BaseModel):
    ok: bool
    message: str


# --------------------------- admin (Support Inbox) ---------------------------
class ContactRow(BaseModel):
    id: str
    name: str
    email: str
    website: str | None = None
    subject: str
    message: str
    status: str
    created_at: datetime | None = None


class ContactStatusUpdate(BaseModel):
    status: str  # new | open | closed


class ContactActionResult(BaseModel):
    ok: bool
    id: str
    status: str | None = None
