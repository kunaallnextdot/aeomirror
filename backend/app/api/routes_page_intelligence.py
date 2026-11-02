"""Page Intelligence for one URL — the detail view of Page Opportunities (Site
Intelligence). Org-scoped through `_owned_monitor` (another org's website or page is a
404) and Pro-gated like Page Opportunities. Reads the latest stored analysis; the two
POSTs analyse one page now (deterministic, no AI) or generate AI suggestions on request.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.api.routes_monitors import _owned_monitor
from app.billing import entitlements
from app.config import settings
from app.db.models import PV_COMPLETED, CrawlJob, CrawlUrl, PageValueRun, PageValueScore
from app.db.session import get_db
from app.services.url_intelligence import overview as ov
from app.services.url_intelligence import page_intelligence as pi
from app.services.url_intelligence import page_suggestions as ps
from app.services.url_intelligence import page_value as pv

router = APIRouter(tags=["page-intelligence"])


def _require_pro(db: Session, ctx: AuthContext) -> None:
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="Page Intelligence is a Pro feature.")


def _page(db: Session, ctx: AuthContext, monitor_id: str, crawl_url_id: str):
    _owned_monitor(db, ctx, monitor_id)
    _require_pro(db, ctx)
    run = pv.latest_run(db, monitor_id, PV_COMPLETED)
    score = (db.query(PageValueScore)
             .filter(PageValueScore.run_id == run.id, PageValueScore.crawl_url_id == crawl_url_id,
                     PageValueScore.organization_id == ctx.org_id).first()) if run else None
    row = db.get(CrawlUrl, crawl_url_id) if score else None
    if score is None or row is None or row.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Page not found.")
    return run, score, row


def _performance(run: PageValueRun, s: PageValueScore) -> dict:
    site_ctr = (run.inputs or {}).get("site_ctr")
    return {
        **{k: getattr(s, k) for k in ("ai_sessions", "organic_sessions", "total_sessions", "conversions",
                                      "conversion_source", "revenue", "revenue_currency", "gsc_clicks",
                                      "gsc_impressions", "gsc_position", "score", "priority")},
        "ctr_pct": round(100 * (s.gsc_clicks or 0) / s.gsc_impressions, 2) if s.gsc_impressions else None,
        "site_ctr_pct": round(100 * site_ctr, 2) if site_ctr else None,
        "conversions_per_100_sessions": round(100 * s.conversions / s.total_sessions, 2)
                                        if s.conversions is not None and s.total_sessions else None,
        "window": (run.inputs or {}).get("window"),
        "sources": pv.public_sources(run.inputs),
    }


def _payload(db: Session, run: PageValueRun, score: PageValueScore, row: CrawlUrl) -> dict:
    analysis = pi.latest_analysis(db, run.monitor_id, row.normalized_key)
    job = db.get(CrawlJob, row.crawl_job_id)
    _, issues = ov.page_issue_details(row)
    findings = ov.applicable_findings(db, job, row, issues, is_pro=True, free_preview=5) if job else []
    sugg = ps.cached(db, run.monitor_id, row)
    if analysis is None:
        status = "queued" if run.analysis_status in pv.ANALYSIS_ACTIVE else "not_analyzed"
    else:
        status = "current" if analysis.crawl_job_id == row.crawl_job_id else "from_earlier_crawl"
    return {
        "page": {"crawl_url_id": row.id, "url": row.url, "page_title": row.page_title,
                 "page_type": score.business_page_type, "classification_status": score.classification_status,
                 "classification_confidence": score.classification_confidence,
                 "classification_evidence": score.classification_evidence or [], "crawled_at": row.crawled_at},
        "performance": _performance(run, score),
        "status": status,
        "analysis": None if analysis is None else {**analysis.result, "analyzed_at": analysis.created_at,
                                                   "crawl_job_id": analysis.crawl_job_id},
        "history": pi.history(db, run.monitor_id, row.normalized_key),
        "verification": [{"finding_id": f["id"], "title": f["finding_title"], "status": f["status"],
                          "verification_status": f.get("verification_status"),
                          "last_verification": f.get("last_verification")} for f in findings],
        "suggestions": None if sugg is None else {**sugg.suggestions, "generated_at": sugg.created_at,
                                                  "model": sugg.model_used},
        "ai_suggestions_available": settings.ai_enabled,
        "run": {"id": run.id, "analysis_status": run.analysis_status, "analyzed_urls": run.analyzed_urls,
                "analysis_total": run.analysis_total},
    }


@router.get("/monitors/{monitor_id}/page-intelligence/{crawl_url_id}")
def get_page_intelligence(monitor_id: str, crawl_url_id: str,
                          ctx: AuthContext = Depends(require_permission("report:view")),
                          db: Session = Depends(get_db)):
    run, score, row = _page(db, ctx, monitor_id, crawl_url_id)
    return _payload(db, run, score, row)


@router.post("/monitors/{monitor_id}/page-intelligence/{crawl_url_id}/analyze")
def analyze_now(monitor_id: str, crawl_url_id: str,
                ctx: AuthContext = Depends(require_permission("scan:run")),
                db: Session = Depends(get_db)):
    """Analyse this one page now (deterministic; reuses the current analysis when
    nothing it depends on has changed)."""
    run, score, row = _page(db, ctx, monitor_id, crawl_url_id)
    if row.status != "crawled" or not row.evidence:
        raise HTTPException(status_code=409, detail="This page wasn't crawled, so it can't be analysed.")
    if not (run.inputs or {}).get("page_intelligence"):
        inputs = dict(run.inputs or {})
        inputs["page_intelligence"] = pi.prepare(db, run)
        run.inputs = inputs
        db.commit()
    counts = pi.analyze_rows(db, run, [score])
    if counts["failed"]:
        raise HTTPException(status_code=500, detail="The analysis failed. The previous analysis is kept.")
    return {**_payload(db, run, score, row), "result": "reused" if counts["reused"] else "analyzed"}


@router.post("/monitors/{monitor_id}/page-intelligence/{crawl_url_id}/suggestions")
def suggestions(monitor_id: str, crawl_url_id: str,
                ctx: AuthContext = Depends(require_permission("scan:run")),
                db: Session = Depends(get_db)):
    run, score, row = _page(db, ctx, monitor_id, crawl_url_id)
    analysis = pi.latest_analysis(db, monitor_id, row.normalized_key)
    try:
        s, from_cache = ps.generate(db, org_id=ctx.org_id, monitor_id=monitor_id, row=row, score=score,
                                    analysis=analysis.result if analysis else None, user_id=ctx.user.id)
    except ps.SuggestionUnavailable as e:
        raise HTTPException(status_code=e.status, detail=e.reason)
    return {**s.suggestions, "generated_at": s.created_at, "model": s.model_used, "cached": from_cache}
