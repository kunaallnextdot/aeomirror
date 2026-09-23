"""Report endpoints (Phase 6): the actionable AI-visibility report + exports.

All endpoints are authenticated and org-scoped (a scan outside your org is 404).
The report JSON is built by the rule-based engine and cached in `reports`; PDF/CSV
are generated on demand at download time — never during a scan. PDF generation is
CPU-bound, so it runs in a threadpool (`run_in_threadpool`) and does not block the
event loop. This is the seam where a future background-job queue would slot in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import AuthContext, is_platform_admin, require_permission
from app.config import settings
from app.db.models import Scan
from app.db.session import get_db
from app.reports import shares
from app.reports.content_intelligence import gate_content_intelligence
from app.reports.exporters import to_csv_bytes, to_json_bytes
from app.reports.insights import gate_insights, gate_recommendations
from app.reports.pdf import build_pdf
from app.reports.crawl_graph import gate_crawl_graph
from app.reports.phase4 import gate_phase4
from app.reports.service import generate_report, get_or_build_report, record_export
from app.reports.technical_seo import gate_technical_seo

router = APIRouter(prefix="/reports", tags=["reports"])


def _share_public(row) -> dict:
    """Share info returned to the OWNER — includes the token + path so they can retrieve
    and re-copy their own share URL. Never includes a viewer IP (not stored)."""
    return {
        "id": row.id, "scan_id": row.scan_id,
        "token": row.token, "path": f"/r/{row.token}",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "view_count": row.view_count or 0,
        "last_viewed_at": row.last_viewed_at.isoformat() if row.last_viewed_at else None,
    }


def _owned_scan(db: Session, ctx: AuthContext, scan_id: str) -> Scan:
    row = db.get(Scan, scan_id)
    # 404 (not 403) so a scan outside your org is indistinguishable from missing.
    if not row or row.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return row


def _safe_domain(report: dict) -> str:
    dom = (report.get("domain") or "report").replace("/", "-").replace(":", "")
    return dom or "report"


def _export_gate(db: Session, ctx: AuthContext, scan_id: str) -> JSONResponse | None:
    """Billing gate for report exports (PDF/CSV/JSON): allowed for Pro or a one-time $9
    report purchase of this scan. Returns a 402 JSONResponse (with an `unlock` hint the
    client turns into a purchase) when locked, else None. Viewing the on-screen report
    stays free."""
    from app.billing import entitlements
    if entitlements.export_unlocked(db, ctx.org_id, scan_id):
        return None
    return JSONResponse(status_code=402, content={
        "detail": "Unlock this report's exports for $9, or go Pro.",
        "unlock": {"kind": "report", "scan_id": scan_id},
    })


@router.get("/{scan_id}")
def get_report(scan_id: str, refresh_ai: int = 0,
               ctx: AuthContext = Depends(require_permission("report:view")),
               db: Session = Depends(get_db)):
    """The AI-visibility report (JSON) for a scan. Viewing the on-screen report is free.
    An AI-written narrative is merged under report["ai"] for ALL plans: the paid tier
    (Pro org or a $9-unlocked report) gets full insights + an action plan, the free tier
    gets the summary + top-3 insights (subject to the free guardrails). Exports stay
    gated. `?refresh_ai=1` forces regeneration (admin / non-production only).

    The persisted report (cached in `reports`, reused across tiers) always carries the
    FULL Phase 2 score-impact simulator + action plan + every recommendation — trimming
    it there would corrupt the cache for when the org later upgrades. Instead the
    response is trimmed here, per the caller's CURRENT entitlement, via the same
    `gate_insights`/`gate_recommendations` used by the dedicated `/insights` endpoint —
    so a free caller's `insights.score_impact`/`action_plan` and locked recommendations
    never leave the server (client-side blur elsewhere is UX, not the protection)."""
    scan = _owned_scan(db, ctx, scan_id)
    allow_refresh = bool(refresh_ai) and (not settings.is_production or is_platform_admin(ctx.user))
    report, _row = generate_report(db, scan, user=ctx.user, refresh_ai=allow_refresh)
    from app.billing import entitlements
    unlocked = entitlements.export_unlocked(db, ctx.org_id, scan_id)
    if not unlocked:
        report = {
            **report,
            "insights": gate_insights(report.get("insights"), unlocked=False),
            "phase4": gate_phase4(report.get("phase4"), unlocked=False),
            "technical_seo": gate_technical_seo(report.get("technical_seo"), unlocked=False),
            "crawl_graph": gate_crawl_graph(report.get("crawl_graph"), unlocked=False),
            "content_intelligence": gate_content_intelligence(
                report.get("content_intelligence"), unlocked=False),
            **gate_recommendations(report.get("recommendations"), unlocked=False),
        }
    return report


@router.get("/{scan_id}/access")
def report_access(scan_id: str,
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    """Lightweight unlock check for the report's exports: {unlocked: bool}. Lets the
    client decide download-vs-purchase without triggering a 402 first."""
    _owned_scan(db, ctx, scan_id)   # 404 for a scan outside the org
    from app.billing import entitlements
    return {"unlocked": entitlements.export_unlocked(db, ctx.org_id, scan_id)}


def _page_summary(scan_row, unlocked: bool) -> dict:
    """Negative-first page-level view for a bulk scan. Scores + status + top-issue
    one-liners are always returned; the full per-page signal breakdown is only included
    when the caller is unlocked (Pro or a $9 report purchase). The locked detail is never
    placed in the payload for a free caller — only a `details_locked` flag."""
    r = scan_row.result or {}
    bulk = r.get("bulk")
    if not bulk:
        return {"is_bulk": False}
    keep = ("url", "overall_score", "status_label", "top_issue")
    out_pages = []
    for p in bulk.get("pages", []) or []:
        if "error" in p:
            out_pages.append({"url": p.get("url"), "error": p.get("error")})
            continue
        base = {k: p.get(k) for k in keep if k in p}
        if unlocked:                      # paid: full per-page signal breakdown + issues
            base["sections_summary"] = p.get("sections_summary", [])
        out_pages.append(base)
    return {
        "is_bulk": True,
        "page_count": bulk.get("page_count"),
        "avg_score": bulk.get("avg_score"),
        "requested": bulk.get("requested"),
        "best": bulk.get("best"),
        "worst": bulk.get("worst"),
        "truncated": bulk.get("truncated"),
        "details_locked": not unlocked,
        "pages": out_pages,
    }


@router.get("/{scan_id}/insights")
def get_report_insights(scan_id: str,
                        ctx: AuthContext = Depends(require_permission("report:view")),
                        db: Session = Depends(get_db)):
    """Negative-first insights for a scan: score-loss breakdown, the biggest problems
    ('why is my score low'), an estimated recovery projection, and a FREE/paid split of
    the recommendations. Free callers get the top 3 recommendations in full plus a count
    of what's locked — the locked recommendation bodies are never sent. Org-scoped (a
    scan outside your org is 404). Read-only: builds the report but changes no score."""
    scan = _owned_scan(db, ctx, scan_id)
    from app.billing import entitlements
    unlocked = entitlements.export_unlocked(db, ctx.org_id, scan_id)

    # Deterministic-only (no AI call) — reuses the SAME canonical cache as
    # GET /reports/{scan_id} (see get_or_build_report) instead of rebuilding the whole
    # pipeline from scratch on every call; a pending/running scan is never persisted as
    # if it were final, so this still recomputes fresh every time for those.
    report, _row = get_or_build_report(db, scan)
    insights = report.get("insights") or {}

    # Same `gate_recommendations` helper as GET /reports/{scan_id} so the two never
    # diverge; field names below are this endpoint's own established shape.
    free_limit = 3
    rec_gate = gate_recommendations(report.get("recommendations"), unlocked, free_limit=free_limit)
    free_recs, locked_count = rec_gate["recommendations"], rec_gate["locked_recommendation_count"]

    # Phase 2 — score-impact simulator + 30-day action plan, gated. Free gets a limited
    # preview (top opportunities / first tasks + a locked count); paid gets everything.
    # Same `gate_insights` helper as GET /reports/{scan_id} so the two never diverge.
    gated = gate_insights(insights, unlocked)
    score_impact_out = gated.get("score_impact") or {}
    action_plan_out = gated.get("action_plan") or {}
    # Phase 4 — Schema / Internal Link / Entity intelligence + scan-only Question
    # Mining, gated identically to GET /reports/{scan_id}.
    phase4_out = gate_phase4(report.get("phase4"), unlocked)

    return {
        "scan_id": scan_id,
        "unlocked": unlocked,
        "overall_score": insights.get("overall_score"),
        "total_points_lost": insights.get("total_points_lost"),
        "issue_count": insights.get("issue_count"),
        "severity_counts": insights.get("severity_counts"),
        "score_breakdown": insights.get("score_breakdown", []),
        "top_problems": insights.get("top_problems", []),
        "strongest_signals": insights.get("strongest_signals", []),
        "weakest_signals": insights.get("weakest_signals", []),
        "projected_recovery": insights.get("projected_recovery"),
        "max_recovery": insights.get("max_recovery"),
        "explanation": insights.get("explanation"),
        "free_recommendations": free_recs,
        "free_recommendation_limit": free_limit,
        "locked_recommendation_count": locked_count,
        "score_impact": score_impact_out,
        "action_plan": action_plan_out,
        "phase4": phase4_out,
        "page_summary": _page_summary(scan, unlocked),
    }


def _resolve_monitor_run(db: Session, ctx: AuthContext, scan: Scan):
    """The monitor (if any) linked to this scan, its prompt set, and its latest
    terminal Answer Tracking run — shared by /ai-visibility and /question-bank so this
    resolution (by latest_scan_id, else by normalized_url within the org) lives in
    exactly one place. Each of the three is None when it doesn't exist; callers decide
    what an absent monitor/run means for their own response."""
    from app.db.models import _RUN_TERMINAL, Monitor, PromptRun
    from app.services.answer_tracking import service

    monitor = (db.query(Monitor)
               .filter(Monitor.organization_id == ctx.org_id,
                       Monitor.latest_scan_id == scan.id).first()
               or db.query(Monitor)
               .filter(Monitor.organization_id == ctx.org_id,
                       Monitor.normalized_url == scan.normalized_url).first())
    if not monitor:
        return None, None, None
    ps = service.prompt_set_for_monitor(db, ctx.org_id, monitor.id, create=False)
    if not ps:
        return monitor, None, None
    run = (db.query(PromptRun)
           .filter(PromptRun.prompt_set_id == ps.id,
                   PromptRun.status.in_(_RUN_TERMINAL))
           .order_by(PromptRun.created_at.desc()).first())
    return monitor, ps, run


@router.get("/{scan_id}/ai-visibility")
def get_report_ai_visibility(scan_id: str,
                             ctx: AuthContext = Depends(require_permission("report:view")),
                             db: Session = Depends(get_db)):
    """Phase 3 report integration — the AI Visibility layer for the scan's SITE, sourced
    from the monitor's latest Answer Tracking run (read-side; no duplicate storage). The
    scan is linked to a monitor by latest_scan_id, else by normalized_url within the same
    org. Returns {available: False, reason} with a clean empty state when there is no
    monitor or no run yet — never fabricated data. Org-scoped; gated per entitlement."""
    scan = _owned_scan(db, ctx, scan_id)
    from app.billing import entitlements
    from app.services.answer_tracking import aggregation, visibility

    monitor, ps, run = _resolve_monitor_run(db, ctx, scan)
    if not monitor:
        return {"available": False, "reason": "no_monitor"}
    if not run:
        return {"available": False, "reason": "no_run", "monitor_id": monitor.id}

    summary = aggregation.run_summary(db, run)
    trend = aggregation.set_trend(db, ps, n=10)
    # Reuses the SAME canonical cache as GET /reports/{scan_id} (see get_or_build_report)
    # to cross-link score-loss/Phase 4 opportunities — never an independent rebuild.
    scan_report, _row = get_or_build_report(db, scan)
    payload = visibility.build_ai_visibility(summary, scan_report=scan_report, trend=trend)
    gated = visibility.gate_visibility(payload, entitlements.ai_visibility_access(db, ctx.org_id))
    return {"available": True, "monitor_id": monitor.id, **gated}


@router.get("/{scan_id}/question-bank")
def get_report_question_bank(scan_id: str,
                             ctx: AuthContext = Depends(require_permission("report:view")),
                             db: Session = Depends(get_db)):
    """Question Bank: every REAL question AEOMirror already knows about for this
    site — scan-derived (FAQ schema + question-shaped headings, from the SAME cached
    report Phase 4 already computed) merged with Answer Tracking's actually-tracked
    prompts (when a monitor + run exist), cross-linked to any opportunity that already
    references the same question. Unlike /ai-visibility, the absence of a monitor/run
    does NOT make the whole feature unavailable — scan-derived questions are useful on
    their own; only the Answer Tracking enrichment is then simply absent.

    Reuses the exact `report_incomplete` signal Phase 4 already computed (a scan that
    hasn't finished has no real questions to bank yet) and the SAME canonical report
    cache (get_or_build_report) — never an independent build_report() call. Org-scoped;
    gated per the report's existing entitlement (same one Phase 4 uses)."""
    scan = _owned_scan(db, ctx, scan_id)
    from app.billing import entitlements
    from app.services.answer_tracking import aggregation, visibility

    unlocked = entitlements.export_unlocked(db, ctx.org_id, scan_id)
    scan_report, _row = get_or_build_report(db, scan)
    phase4 = scan_report.get("phase4") or {}
    if phase4.get("available") is False:
        return dict(phase4)   # {"available": False, "reason": "scan_incomplete"}

    monitor, _ps, run = _resolve_monitor_run(db, ctx, scan)
    summary = aggregation.run_summary(db, run) if run else None
    opportunities = visibility.build_opportunities(summary or {}, scan_report, phase4)
    bank = visibility.build_question_bank(phase4.get("questions"), summary, opportunities)
    gated = visibility.gate_question_bank(bank, unlocked)
    return {"available": True, "monitor_id": monitor.id if monitor else None, **gated}


@router.get("/{scan_id}/answer-simulation")
def get_report_answer_simulation(scan_id: str,
                                 ctx: AuthContext = Depends(require_permission("report:view")),
                                 db: Session = Depends(get_db)):
    """AEO Answer Simulator cross-link for a scan's report: a summary of the site's
    (monitor's) most recent simulator run, if any. Read-only — never triggers a
    simulation, never calls build_report() directly (reuses the SAME resolution
    helper /ai-visibility and /question-bank already share), and writes nothing into
    the cached `reports` row (the simulator, like Answer Tracking, stays entirely
    separate from the persisted report object). {"available": false, "reason":
    "no_monitor"|"no_run"} when there's nothing real to show yet."""
    from app.db.models import PromptRun

    scan = _owned_scan(db, ctx, scan_id)
    monitor, ps, _provider_run = _resolve_monitor_run(db, ctx, scan)
    if not monitor:
        return {"available": False, "reason": "no_monitor"}
    if not ps:
        return {"available": False, "reason": "no_run", "monitor_id": monitor.id}

    run = (db.query(PromptRun)
          .filter(PromptRun.prompt_set_id == ps.id, PromptRun.run_mode == "simulator")
          .order_by(PromptRun.created_at.desc()).first())
    if not run:
        return {"available": False, "reason": "no_run", "monitor_id": monitor.id}

    from app.db.models import PromptResult

    results = (db.query(PromptResult)
              .filter(PromptResult.run_id == run.id).all())
    level_counts: dict[str, int] = {}
    for r in results:
        level_counts[r.answerability or "UNKNOWN"] = level_counts.get(r.answerability or "UNKNOWN", 0) + 1

    return {
        "available": True, "monitor_id": monitor.id, "run_id": run.id,
        "status": run.status, "question_count": len(results),
        "answerability_breakdown": level_counts,
        "llm_step_used_count": sum(1 for r in results if r.llm_step_used),
        "run_at": run.completed_at or run.created_at,
    }


@router.get("/{scan_id}/technical-seo")
def get_report_technical_seo(scan_id: str,
                             ctx: AuthContext = Depends(require_permission("report:view")),
                             db: Session = Depends(get_db)):
    """Technical SEO & Indexability Intelligence for a scan: for every crawled URL,
    the technically-grounded indexability signals (HTTP status, robots/meta-robots/
    X-Robots-Tag, canonical, redirects, sitemap presence) — never a claim about actual
    Google indexing (no search-engine integration exists). Reuses the SAME canonical report cache
    as GET /reports/{scan_id} (see get_or_build_report) — never an independent
    build_report() call. Org-scoped; gated per the report's existing entitlement (same
    one Phase 4 / Question Bank use)."""
    scan = _owned_scan(db, ctx, scan_id)
    from app.billing import entitlements

    unlocked = entitlements.export_unlocked(db, ctx.org_id, scan_id)
    report, _row = get_or_build_report(db, scan)
    block = report.get("technical_seo") or {}
    if block.get("available") is False:
        return dict(block)   # {"available": False, "reason": "scan_incomplete"}
    return gate_technical_seo(block, unlocked)


@router.get("/{scan_id}/crawl-graph")
def get_report_crawl_graph(scan_id: str,
                           ctx: AuthContext = Depends(require_permission("report:view")),
                           db: Session = Depends(get_db)):
    """Real Crawl Graph + True Orphan Detection for a scan: for a multi-page (bulk)
    crawl, the real internal-link graph — inbound/outbound counts, reachability and
    depth from the crawl seed, and true orphan pages (zero inbound internal links from
    other crawled pages). A single-page scan returns
    {"available": false, "reason": "multi_page_crawl_required"} — it has no site-wide
    graph to build. Reuses the SAME canonical report cache as GET /reports/{scan_id}
    (see get_or_build_report) — never an independent build_report() call. Org-scoped;
    gated per the report's existing entitlement."""
    scan = _owned_scan(db, ctx, scan_id)
    from app.billing import entitlements

    unlocked = entitlements.export_unlocked(db, ctx.org_id, scan_id)
    report, _row = get_or_build_report(db, scan)
    block = report.get("crawl_graph") or {}
    if block.get("available") is False:
        return dict(block)
    return gate_crawl_graph(block, unlocked)


@router.get("/{scan_id}/content-intelligence")
def get_report_content_intelligence(scan_id: str,
                                    ctx: AuthContext = Depends(require_permission("report:view")),
                                    db: Session = Depends(get_db)):
    """Content Cannibalization & Duplicate Content Intelligence for a scan: for a
    multi-page (bulk) crawl, near-duplicate/overlap/potential-cannibalization clusters,
    duplicate page elements (title/H1/meta description), and relative thin-content
    candidates. This is TECHNICAL evidence only (content similarity/duplicate
    elements). A single-page scan returns
    {"available": false, "reason": "multi_page_crawl_required"}. Reuses the SAME
    canonical report cache as GET /reports/{scan_id} (see get_or_build_report) — never
    an independent build_report() call. Org-scoped; gated per the report's existing
    entitlement."""
    scan = _owned_scan(db, ctx, scan_id)
    from app.billing import entitlements

    unlocked = entitlements.export_unlocked(db, ctx.org_id, scan_id)
    report, _row = get_or_build_report(db, scan)
    block = report.get("content_intelligence") or {}
    if block.get("available") is False:
        return dict(block)
    return gate_content_intelligence(block, unlocked)


@router.get("/{scan_id}/json")
def download_json(scan_id: str,
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    scan = _owned_scan(db, ctx, scan_id)
    gate = _export_gate(db, ctx, scan_id)
    if gate:
        return gate
    # Reaching here means exports are unlocked → the caller qualifies for the paid narrative.
    report, row = generate_report(db, scan, user=ctx.user)
    body = to_json_bytes(report)
    record_export(db, report_id=row.id if row else None, scan_row=scan, user_id=ctx.user.id,
                  fmt="json", size=len(body))
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="aeomirror-report-{_safe_domain(report)}.json"'})


@router.get("/{scan_id}/csv")
def download_csv(scan_id: str,
                 ctx: AuthContext = Depends(require_permission("report:view")),
                 db: Session = Depends(get_db)):
    scan = _owned_scan(db, ctx, scan_id)
    gate = _export_gate(db, ctx, scan_id)
    if gate:
        return gate
    report, row = generate_report(db, scan, user=ctx.user)
    body = to_csv_bytes(report)
    record_export(db, report_id=row.id if row else None, scan_row=scan, user_id=ctx.user.id,
                  fmt="csv", size=len(body))
    return Response(
        content=body, media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="aeomirror-report-{_safe_domain(report)}.csv"'})


@router.get("/{scan_id}/pdf")
async def download_pdf(scan_id: str,
                       ctx: AuthContext = Depends(require_permission("report:view")),
                       db: Session = Depends(get_db)):
    from app.admin import flags
    if not flags.is_enabled(db, "pdf_export"):
        raise HTTPException(status_code=403, detail="PDF export is currently disabled.")
    scan = _owned_scan(db, ctx, scan_id)
    gate = _export_gate(db, ctx, scan_id)
    if gate:
        return gate
    # generate_report may call the AI narrative on first generation — run it (and the
    # CPU-bound PDF render) off the event loop so scans/requests are never blocked.
    report, row = await run_in_threadpool(generate_report, db, scan, user=ctx.user)
    body = await run_in_threadpool(build_pdf, report)
    record_export(db, report_id=row.id if row else None, scan_row=scan, user_id=ctx.user.id,
                  fmt="pdf", size=len(body))
    return Response(
        content=body, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="aeomirror-report-{_safe_domain(report)}.pdf"'})


# ------------------------------- public share links -------------------------------
# Creating/listing/revoking a share requires only report:view (any org member who can
# view the report). It is NOT export-gated: the share link is a distribution channel
# (its point is the "Scanned with AEOMirror" backlink), and the public page serves only
# the free-viewable report with no exports. The active-share COUNT is capped per plan.
@router.post("/{scan_id}/share")
def create_report_share(scan_id: str,
                        ctx: AuthContext = Depends(require_permission("report:view")),
                        db: Session = Depends(get_db)):
    """Mint a public read-only share link for a scan's report. Returns the share incl.
    its token/path; the URL stays retrievable via the list endpoint. 402 when the org's
    active-share cap is reached."""
    _owned_scan(db, ctx, scan_id)   # 404 for a scan outside the org
    from app.billing import entitlements
    q = entitlements.share_quota(db, ctx.org_id)
    if not q["unlimited"] and q["remaining"] <= 0:
        return JSONResponse(status_code=402, content={
            "detail": f"You've reached your limit of {q['limit']} active share links. "
                      "Revoke one, or go Pro for unlimited.",
            "unlock": {"kind": "share"},
        })
    row, _raw = shares.create_share(
        db, scan_id=scan_id, org_id=ctx.org_id, user_id=ctx.user.id,
        ttl_seconds=settings.report_share_ttl_seconds)
    return _share_public(row)   # includes token + path; retrievable again via the list endpoint


@router.get("/{scan_id}/shares")
def list_report_shares(scan_id: str,
                       ctx: AuthContext = Depends(require_permission("report:view")),
                       db: Session = Depends(get_db)):
    """Active (unrevoked, unexpired) share links for this scan, incl. the token/path so
    the owner can re-copy the URL. Used by the report view to show/manage a link."""
    _owned_scan(db, ctx, scan_id)
    rows = [r for r in shares.active_shares(db, ctx.org_id) if r.scan_id == scan_id]
    return {"shares": [_share_public(r) for r in rows]}


@router.delete("/shares/{share_id}")
def revoke_report_share(share_id: str,
                        ctx: AuthContext = Depends(require_permission("report:view")),
                        db: Session = Depends(get_db)):
    """Revoke one of the org's share links (the public URL stops resolving immediately)."""
    if not shares.revoke_share(db, share_id=share_id, org_id=ctx.org_id):
        raise HTTPException(status_code=404, detail="Share not found.")
    return {"revoked": True}
