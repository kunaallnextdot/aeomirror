"""API request/response schemas."""
from __future__ import annotations

from pydantic import BaseModel


class ScanRequest(BaseModel):
    url: str


class CheckOut(BaseModel):
    id: str
    label: str
    status: str
    detail: str
    fix_hint: str
    weight: float
    earned: float


class FamilyOut(BaseModel):
    id: str
    label: str
    weight: float
    earned: float
    checks: list[CheckOut]


class IssueOut(BaseModel):
    id: str
    label: str
    status: str
    detail: str
    fix_hint: str
    family: str


class CrawlerOut(BaseModel):
    id: str
    label: str
    status: str


class ScanResponse(BaseModel):
    scan_id: str
    url: str
    domain: str
    ars: int
    rubric_version: str
    families: list[FamilyOut]
    top_issues: list[IssueOut]
    crawlers: list[CrawlerOut]
    remaining_free_scans: int


class LeadRequest(BaseModel):
    email: str
    url: str | None = None
