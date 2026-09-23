"""Dashboard endpoints (Phase 4, secured in Phase 5).

Scans are now scoped to the caller's ORGANIZATION and every endpoint requires
authentication + the appropriate RBAC permission:
- viewing (list / detail / dashboard): report:view  (any member incl. Viewer)
- re-running a scan:                    scan:run     (Member and up)
- deleting a scan:                      scan:delete  (Admin / Owner)

Data is read from the existing scans table (Phase 2/3 already store overall score,
signal scores, issues, recommendations, evidence, scanner version, duration).
"""
from __future__ import annotations

import asyncio
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import AuthContext, require_permission
from app.config import settings
from app.api.routes_scan import (
    _client_ip, _ip_hash, _normalize, build_scan_response, run_scan,
)
from app.core.cache import rate_limiter
from app.core.fetch import fetch
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import USAGE_COMPARE, AiContentInsight, MonitorHistory, Scan, Verification
from app.db.session import get_db
from app.scanner.signals.base import SignalContext, status_from_score
from app.schemas.dashboard import CompareRequest, DashboardSummary, ScanSummary, VerifyRequest
from app.schemas.scan import ScanResponse
from app.services.verification import verify_signal

router = APIRouter(prefix="/api", tags=["dashboard"])

_MAX_ROWS = 500


def _summary(row: Scan, monitor_scan_ids: set[str] = frozenset()) -> dict:
    r = row.result or {}
    overall = r.get("overall_score")
    return {
        "id": row.id,
        "url": row.url,
        "domain": _normalize(row.url),
        # created_at is stored as naive UTC; stamp it as UTC-aware so the ISO string
        # carries an offset and the browser renders it in the viewer's local time
        # (previously the naive string was parsed as local, showing the UTC clock).
        "scan_time": (row.created_at.replace(tzinfo=timezone.utc).isoformat()
                      if row.created_at else None),
        "overall_score": overall,
        "ars": row.ars,
        "duration_ms": r.get("duration_ms"),
        "status": status_from_score(overall) if overall is not None else "complete",
        "scanner_version": r.get("scanner_version"),
        "signal_scores": {s.get("id"): s.get("score") for s in r.get("sections", [])},
        "billable": row.id not in monitor_scan_ids,
    }


def _monitor_scan_ids(db: Session, scan_ids: list[str]) -> set[str]:
    """Which of these scan ids were triggered by a monitor (scheduled check / "Run
    Now"), via the same monitor_history table entitlements/the runner already use to
    track monitor scans — never a second, separate classification. ONE query for the
    whole page (never per-row), so listing scans stays a single round trip."""
    if not scan_ids:
        return set()
    rows = (db.query(MonitorHistory.scan_id)
            .filter(MonitorHistory.scan_id.in_(scan_ids))
            .all())
    return {sid for (sid,) in rows}


def _org_rows(db: Session, ctx: AuthContext) -> list[Scan]:
    """The caller's organization's scans, newest first. Empty if no org. Free plans
    see only their most recent `history_limit` scans (billing gate)."""
    if not ctx.org_id:
        return []
    from app.billing import entitlements
    hlimit = entitlements.history_limit(db, ctx.org_id)
    cap = _MAX_ROWS if hlimit is None else min(_MAX_ROWS, hlimit)
    return (db.query(Scan)
            .filter(Scan.organization_id == ctx.org_id)
            .order_by(Scan.created_at.desc())
            .limit(cap)
            .all())


def _owned_scan_or_404(db: Session, ctx: AuthContext, scan_id: str) -> Scan:
    row = db.get(Scan, scan_id)
    # 404 (not 403) so a scan outside your org is indistinguishable from missing.
    if not row or row.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return row


@router.get("/scans", response_model=list[ScanSummary])
def list_scans(
    ctx: AuthContext = Depends(require_permission("report:view")),
    db: Session = Depends(get_db),
    q: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    sort: str = "newest",
):
    """List the caller's organization's scans, with search/filter/sort."""
    rows = _org_rows(db, ctx)
    monitor_scan_ids = _monitor_scan_ids(db, [r.id for r in rows])
    items = [_summary(r, monitor_scan_ids) for r in rows]

    if q:
        ql = q.lower()
        items = [x for x in items if ql in x["url"].lower()]
    if min_score is not None:
        items = [x for x in items if (x["overall_score"] or 0) >= min_score]
    if max_score is not None:
        items = [x for x in items if (x["overall_score"] or 0) <= max_score]
    if from_date:
        items = [x for x in items if x["scan_time"] and x["scan_time"] >= from_date]
    if to_date:
        items = [x for x in items if x["scan_time"] and x["scan_time"] <= to_date]

    if sort == "oldest":
        items.sort(key=lambda x: x["scan_time"] or "")
    elif sort == "highest":
        items.sort(key=lambda x: x["overall_score"] or 0, reverse=True)
    elif sort == "lowest":
        items.sort(key=lambda x: x["overall_score"] or 0)
    else:  # newest
        items.sort(key=lambda x: x["scan_time"] or "", reverse=True)
    return items


@router.get("/scans/{scan_id}", response_model=ScanResponse)
def get_scan_detail(scan_id: str,
                    ctx: AuthContext = Depends(require_permission("report:view")),
                    db: Session = Depends(get_db)):
    from app.billing import entitlements
    row = _owned_scan_or_404(db, ctx, scan_id)
    # Free orgs see scores for every bulk page but not the per-page section detail.
    allowed = entitlements.can_view_page_details(db, ctx.org_id, row)
    return build_scan_response(row, hide_page_details=not allowed)


class ContentInsightsRequest(BaseModel):
    """Optional body for POST content-insights. `page_url` targets one page of a bulk
    scan; omitted → the scan's primary URL."""
    page_url: str | None = None


def _known_page_urls(scan: Scan) -> set[str]:
    """Every URL that belongs to this scan (the primary URL + any bulk page URLs)."""
    urls = {scan.url}
    for p in ((scan.result or {}).get("bulk") or {}).get("pages", []):
        if p.get("url"):
            urls.add(p["url"])
    return urls


@router.get("/scans/{scan_id}/content-insights")
def list_content_insights(scan_id: str,
                          ctx: AuthContext = Depends(require_permission("report:view")),
                          db: Session = Depends(get_db)):
    """Cached AI content insights already generated for this scan (read-only). Used to
    surface existing insights in the report/scan views without re-calling the model.
    Org-scoped (404 for a scan outside the caller's organization)."""
    _owned_scan_or_404(db, ctx, scan_id)
    rows = (db.query(AiContentInsight)
            .filter(AiContentInsight.scan_id == scan_id)
            .order_by(AiContentInsight.created_at.desc()).all())
    return {"insights": [{"page_url": r.page_url, "data": r.data} for r in rows]}


@router.post("/scans/{scan_id}/content-insights")
async def create_content_insight(scan_id: str, body: ContentInsightsRequest,
                                 ctx: AuthContext = Depends(require_permission("report:view")),
                                 db: Session = Depends(get_db)):
    """Generate AI content-quality insights for one page (Pro-only). Reuses the cached
    row for a (scan, page) pair; otherwise re-fetches the page (SSRF-safe), extracts its
    visible text (~6000 chars) and analyzes it. 402 for non-Pro; 503 if the model is
    unavailable / returns nothing usable."""
    scan = _owned_scan_or_404(db, ctx, scan_id)
    from app.billing import entitlements
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="Content Insights is a Pro feature.")

    target = body.page_url or scan.url
    if body.page_url and body.page_url not in _known_page_urls(scan):
        raise HTTPException(status_code=422, detail="That page is not part of this scan.")

    cached = (db.query(AiContentInsight)
              .filter(AiContentInsight.scan_id == scan_id,
                      AiContentInsight.page_url == target).first())
    if cached:
        return {"scan_id": scan_id, "page_url": target, "cached": True, "insights": cached.data}

    try:
        safe_url = validate_url(target)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    async def _fetch_and_analyze():
        # The page was already fetched successfully during the scan, so this
        # interactive re-fetch uses a reduced retry budget (not the scanner's global
        # fetch_retry_count). SSRF revalidation on every hop is unchanged.
        try:
            page = await fetch(safe_url, retries=0)
        except Exception:
            raise HTTPException(status_code=400, detail="Could not fetch that page to analyze.")
        text = SignalContext(page).text
        from app.scanner import ai_content
        # Shorter interactive AI timeout so a retry fits the budget. NOTE: if the budget
        # below times out, this threadpool thread can't be force-cancelled (sync SDK) —
        # but the AI call is capped at content_insight_ai_timeout_seconds per attempt, so
        # any orphaned call self-terminates quickly rather than burning tokens unbounded.
        return await run_in_threadpool(
            ai_content.analyze_content, text, url=target,
            timeout=settings.content_insight_ai_timeout_seconds,
            max_tokens=settings.content_insight_ai_max_tokens)

    # Hard overall deadline: return 504 rather than run long enough for an upstream
    # proxy to sever the connection (which the browser reports as "backend unreachable").
    try:
        insights = await asyncio.wait_for(
            _fetch_and_analyze(), timeout=settings.content_insight_budget_seconds)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504,
            detail="Analyzing this page took too long. Please try again in a moment.")
    if not insights:
        raise HTTPException(status_code=503, detail="AI analysis is temporarily unavailable.")

    db.add(AiContentInsight(scan_id=scan_id, organization_id=ctx.org_id,
                            page_url=target, data=insights))
    db.commit()
    return {"scan_id": scan_id, "page_url": target, "cached": False, "insights": insights}


@router.get("/scans/{scan_id}/status")
def get_scan_status(scan_id: str,
                    ctx: AuthContext = Depends(require_permission("report:view")),
                    db: Session = Depends(get_db)):
    """Lightweight progress poll for a background (full-site) scan. Returns just
    {status, progress} so the client can render a progress bar without pulling the
    whole result; the full report is read from GET /api/scans/{id} once completed.
    Org-scoped (404 for a scan outside the caller's organization)."""
    row = _owned_scan_or_404(db, ctx, scan_id)
    from app.db.models import SCAN_COMPLETED
    return {"status": getattr(row, "status", SCAN_COMPLETED) or SCAN_COMPLETED,
            "progress": row.progress}


@router.post("/compare")
def compare_scans(body: CompareRequest,
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    """Diff two of the caller's scans (returns both full reports; the client renders
    the signal-by-signal delta). Metered: the Free plan includes a fixed number of
    comparisons per calendar month, Pro is unlimited. Both scans must belong to the
    caller's org (404 otherwise). One usage event is recorded per successful compare
    — the 402 gate is checked BEFORE recording so a blocked attempt is not counted."""
    from app.billing import entitlements
    a = _owned_scan_or_404(db, ctx, body.a_id)
    b = _owned_scan_or_404(db, ctx, body.b_id)
    q = entitlements.compare_quota(db, ctx.org_id)
    if not q["unlimited"] and q["remaining"] <= 0:
        raise HTTPException(status_code=402,
            detail=f"Free plan includes {q['limit']} comparisons per month. "
                   "Upgrade to Pro for unlimited comparisons.")
    entitlements.record_usage(db, ctx.org_id, USAGE_COMPARE)
    # Per-page bulk detail stays Pro-gated even inside a comparison.
    hide_a = not entitlements.can_view_page_details(db, ctx.org_id, a)
    hide_b = not entitlements.can_view_page_details(db, ctx.org_id, b)
    return {"a": build_scan_response(a, hide_page_details=hide_a),
            "b": build_scan_response(b, hide_page_details=hide_b)}


def _serialize_verification(row: Verification) -> dict:
    # score_delta is derived (score_after - score_before), not stored as its own
    # column — no new persisted field for a value trivially computable from the two
    # already-stored scores.
    delta = (round(row.score_after - row.score_before, 1)
            if row.score_after is not None and row.score_before is not None else None)
    return {
        "id": row.id, "baseline_scan_id": row.baseline_scan_id,
        "verification_scan_id": row.verification_scan_id, "signal_id": row.signal_id,
        "verification_status": row.verification_status,
        "status_before": row.status_before, "status_after": row.status_after,
        "score_before": row.score_before, "score_after": row.score_after, "score_delta": delta,
        "resolved_issues": row.resolved_issues or [], "remaining_issues": row.remaining_issues or [],
        "new_issues": row.new_issues or [], "evidence_changes": row.evidence_changes or [],
        "created_at": (row.created_at.replace(tzinfo=timezone.utc).isoformat()
                      if row.created_at else None),
    }


@router.post("/verifications")
def create_verification(body: VerifyRequest,
                        ctx: AuthContext = Depends(require_permission("report:view")),
                        db: Session = Depends(get_db)):
    """Compute AND persist a Fix Verification result for one signal, from two of the
    caller's own scans. Server-authoritative: the comparison is computed HERE (see
    app/services/verification.py — a 1:1 port of the existing frontend verification.js
    engine, never a second/divergent algorithm), never trusted from the client. Both
    scans must belong to the caller's org (404 otherwise) — reuses the exact
    ownership check `compare_scans` already uses. Metered exactly like Compare (same
    quota, same usage-event kind) — Fix Verification has always driven its comparison
    through the same Compare API/quota; this preserves that behavior rather than
    silently changing it."""
    from app.billing import entitlements
    baseline = _owned_scan_or_404(db, ctx, body.baseline_scan_id)
    verification_scan = _owned_scan_or_404(db, ctx, body.verification_scan_id)

    q = entitlements.compare_quota(db, ctx.org_id)
    if not q["unlimited"] and q["remaining"] <= 0:
        raise HTTPException(status_code=402,
            detail=f"Free plan includes {q['limit']} comparisons per month. "
                   "Upgrade to Pro for unlimited comparisons.")

    result = verify_signal(
        body.signal_id, baseline.result, verification_scan.result,
        before_status=getattr(baseline, "status", None) or "completed",
        after_status=getattr(verification_scan, "status", None) or "completed")
    if result is None:
        raise HTTPException(status_code=422, detail="These scans cannot be compared reliably.")

    entitlements.record_usage(db, ctx.org_id, USAGE_COMPARE)

    row = Verification(
        organization_id=ctx.org_id,   # derived from authenticated context, never client input
        baseline_scan_id=baseline.id, verification_scan_id=verification_scan.id,
        signal_id=body.signal_id, verification_status=result["verification_status"],
        status_before=result["status_before"], status_after=result["status_after"],
        score_before=result["score_before"], score_after=result["score_after"],
        resolved_issues=result["resolved_issues"], remaining_issues=result["remaining_issues"],
        new_issues=result["new_issues"], evidence_changes=result["evidence_changes"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_verification(row)


@router.get("/verifications")
def list_verifications(scan_id: str, signal_id: str | None = None,
                       ctx: AuthContext = Depends(require_permission("report:view")),
                       db: Session = Depends(get_db)):
    """Persisted verification history for one scan (as the BASELINE) — newest first.
    Org-scoped directly on the query, never on the client-supplied scan_id alone:
    only rows whose organization_id matches the caller's authenticated org are ever
    returned. `_owned_scan_or_404` additionally 404s outright if `scan_id` isn't the
    caller's own scan. `signal_id` narrows to one signal's history; omitted, returns
    every signal's history for this scan (newest first per signal), letting the
    caller pick the latest per signal_id without a request per signal."""
    _owned_scan_or_404(db, ctx, scan_id)
    query = (db.query(Verification)
            .filter(Verification.organization_id == ctx.org_id,
                    Verification.baseline_scan_id == scan_id))
    if signal_id:
        query = query.filter(Verification.signal_id == signal_id)
    rows = query.order_by(Verification.created_at.desc()).limit(200).all()
    return {"verifications": [_serialize_verification(r) for r in rows]}


@router.delete("/scans/{scan_id}")
def delete_scan(scan_id: str,
                ctx: AuthContext = Depends(require_permission("scan:delete")),
                db: Session = Depends(get_db)):
    row = _owned_scan_or_404(db, ctx, scan_id)
    db.delete(row)
    db.commit()
    return {"ok": True, "id": scan_id}


@router.post("/scans/{scan_id}/rerun", response_model=ScanResponse)
async def rerun_scan(scan_id: str, request: Request,
                     ctx: AuthContext = Depends(require_permission("scan:run")),
                     db: Session = Depends(get_db)):
    from app.api.routes_scan import _scan_quota_message
    from app.billing import entitlements
    row = _owned_scan_or_404(db, ctx, scan_id)

    # A rerun is a user-initiated scan job — metered against the monthly quota.
    quota = entitlements.scan_quota(db, ctx.org_id)
    if not quota["unlimited"] and quota["remaining"] <= 0:
        raise HTTPException(status_code=402,
            detail=_scan_quota_message(entitlements.current_plan(db, ctx.org_id)))

    ip = _client_ip(request)
    allowed, remaining, retry_after = rate_limiter.check(_ip_hash(ip))
    if not allowed:
        raise HTTPException(status_code=429,
            detail="Too many scans from your network right now. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)})

    try:
        safe_url = validate_url(row.url)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # A rerun always produces a FRESH scan row (bypasses the read cache), attributed
    # to the caller's organization. run_scan() itself records the scan job the moment
    # the row is persisted — see its own docstring — so there is nothing to do here.
    payload = await run_scan(db, safe_url, ip, ctx.org_id, ctx.user.id)
    return ScanResponse(**payload, remaining_free_scans=remaining)


@router.get("/dashboard", response_model=DashboardSummary)
def dashboard(ctx: AuthContext = Depends(require_permission("report:view")),
              db: Session = Depends(get_db)):
    rows = _org_rows(db, ctx)
    summaries = [_summary(r) for r in rows]  # newest first
    scores = [s["overall_score"] for s in summaries if s["overall_score"] is not None]

    latest = summaries[0] if summaries else None
    trend = [{"scan_time": s["scan_time"], "overall_score": s["overall_score"]}
             for s in reversed(summaries) if s["overall_score"] is not None]

    buckets = ["0-20", "20-40", "40-60", "60-80", "80-100"]
    dist = {b: 0 for b in buckets}
    for sc in scores:
        dist[buckets[min(int(sc // 20), 4)]] += 1

    fail_counts: dict = {}      # signal id -> [label, count]
    issue_counts: dict = {}     # issue text -> count
    for r in rows:
        for s in (r.result or {}).get("sections", []):
            if s.get("status") == "fail":
                entry = fail_counts.setdefault(s.get("id"), [s.get("label"), 0])
                entry[1] += 1
            for iss in s.get("issues", []):
                issue_counts[iss] = issue_counts.get(iss, 0) + 1

    top_cats = sorted(
        ({"signal": k, "label": v[0], "fail_count": v[1]} for k, v in fail_counts.items()),
        key=lambda x: -x["fail_count"])[:6]
    common_failures = sorted(
        ({"issue": k, "count": v} for k, v in issue_counts.items()),
        key=lambda x: -x["count"])[:6]

    return {
        "total_scans": len(summaries),
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "highest_score": max(scores) if scores else None,
        "lowest_score": min(scores) if scores else None,
        "latest_scan": {
            "id": latest["id"], "url": latest["url"], "domain": latest["domain"],
            "overall_score": latest["overall_score"], "scan_time": latest["scan_time"],
        } if latest else None,
        "most_common_issue": common_failures[0] if common_failures else None,
        "score_trend": trend,
        "score_distribution": [{"bucket": b, "count": dist[b]} for b in buckets],
        "top_issue_categories": top_cats,
        "common_failures": common_failures,
    }
