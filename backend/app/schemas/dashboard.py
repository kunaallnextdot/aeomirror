"""Dashboard API schemas (Phase 4). Read-only aggregates over stored scans."""
from __future__ import annotations

from pydantic import BaseModel


class ScanSummary(BaseModel):
    id: str
    url: str
    domain: str
    scan_time: str | None = None
    overall_score: int | None = None
    ars: int
    duration_ms: int | None = None
    status: str
    scanner_version: str | None = None
    signal_scores: dict = {}
    # False only for a scan a monitor triggered (scheduled check / "Run Now") — those
    # don't consume the org's scan-job quota (see entitlements.scans_this_month), which
    # is why this org's scan HISTORY count can legitimately exceed the sidebar's "Scan
    # jobs used" count. True for every ordinary (single-page, bulk, rerun) scan.
    billable: bool = True


class LatestScan(BaseModel):
    id: str
    url: str
    domain: str
    overall_score: int | None = None
    scan_time: str | None = None


class TrendPoint(BaseModel):
    scan_time: str | None = None
    overall_score: int | None = None


class DistributionBucket(BaseModel):
    bucket: str
    count: int


class IssueCategory(BaseModel):
    signal: str
    label: str
    fail_count: int


class CommonFailure(BaseModel):
    issue: str
    count: int


class CompareRequest(BaseModel):
    a_id: str   # the "previous" scan
    b_id: str   # the "current" scan


class VerifyRequest(BaseModel):
    baseline_scan_id: str
    verification_scan_id: str
    signal_id: str


class DashboardSummary(BaseModel):
    total_scans: int
    average_score: float | None = None
    highest_score: int | None = None
    lowest_score: int | None = None
    latest_scan: LatestScan | None = None
    most_common_issue: CommonFailure | None = None
    score_trend: list[TrendPoint] = []
    score_distribution: list[DistributionBucket] = []
    top_issue_categories: list[IssueCategory] = []
    common_failures: list[CommonFailure] = []
