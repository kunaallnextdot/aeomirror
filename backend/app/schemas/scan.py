"""API request/response schemas."""
from __future__ import annotations

from pydantic import BaseModel


class ScanRequest(BaseModel):
    url: str


class BulkScanRequest(BaseModel):
    """JSON body for POST /v1/scan/bulk. A file upload (CSV/XLSX) is the alternative
    input and is read from the multipart form instead of this model."""
    urls: list[str] = []


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


class SectionOut(BaseModel):
    """One AI-visibility signal (Phase 3). Additive to the legacy family model."""
    id: str
    label: str
    score: int
    status: str                       # pass | warn | fail
    weight: float
    issues: list[str] = []
    recommendations: list[str] = []
    evidence: dict = {}


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
    # --- Phase 3: modular AI-visibility signals (additive, backward-compatible) ---
    overall_score: int | None = None
    scanner_version: str | None = None
    scanned_at: str | None = None
    duration_ms: int | None = None
    sections: list[SectionOut] = []
    # --- bulk scan report (additive). Present only for bulk scans; None for
    # single-page scans so old rows and the single-page UI still work.
    # Shape: {"pages": [{url, overall_score, status_label, top_issue,
    #         sections_summary, error?}], "page_count", "avg_score",
    #         "best": {url, score}, "worst": {url, score}, "requested", "truncated"}
    bulk: dict | None = None
    # --- lifecycle (background bulk scans). "completed" for single-page scans; a bulk
    # scan moves pending -> running -> completed | failed. progress is the live state
    # while running: {total, done, failed, current_url}.
    status: str | None = None
    progress: dict | None = None
    # Safe, user-facing failure message for a FAILED (bulk) scan, surfaced from
    # result["error"]. None for healthy scans. Only this string is exposed — never the
    # whole internal result dict.
    error: str | None = None
    # --- AI Crawler Access Check (additive). Structured per-bot result: robots/WAF
    # status per crawler + findings. None for bulk scans, old rows, or when disabled.
    crawler_access: dict | None = None


class LeadRequest(BaseModel):
    email: str
    url: str | None = None
