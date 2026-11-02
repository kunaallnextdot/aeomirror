"""Page Opportunities: business-value scoring of a website's crawled URLs (a view of
Site Intelligence, not a separate dashboard). Org-scoped through `_owned_monitor`
(another org's website is a 404) and Pro-gated like AI Business Impact. Reads only the
persisted PageValueScore rows of the website's latest completed run — listing a
100k-URL site is a paginated SQL query, never an on-request computation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.api.routes_monitors import _owned_monitor
from app.billing import entitlements
from app.db.models import PV_ACTIVE, PV_COMPLETED, CrawlJob, CrawlUrl, PageValueRun, PageValueScore
from app.db.session import get_db
from app.services.url_intelligence import overview as ov
from app.services.url_intelligence import page_value as pv
from app.services.url_intelligence.classification import BUSINESS_PAGE_TYPES

router = APIRouter(tags=["page-opportunities"])

_FREE_FINDINGS_PREVIEW = 5   # same preview size as the Action Center (routes_url_intelligence)

DEFINITIONS = {
    "score": ("Business Value Score (0–100): how much a page appears to matter to the business, from the "
              "signals available for this website. Signals that aren't available are left out, never "
              "counted as zero. A high score does not guarantee revenue."),
    "priority": ("Priority combines the score with the page's issues: a valuable page with a medium or worse "
                 "issue is high priority; a page with no issue has nothing to fix."),
    "measured_vs_inferred": ("Measured: GA4, Search Console, AEOMirror's own verified conversions and the "
                             "crawl's internal links. Inferred: the page's purpose, classified from its URL, "
                             "title, headings and structured data."),
    "search_console": ("Search Console shows Google Search clicks and impressions. It is not AI-assistant "
                       "data, and its queries are Google searches, not prompts people typed into AI tools."),
    "conversions": ("Conversions are counted on sessions that started on the page (session-level, not "
                    "per visitor). Server-verified conversions are used when the site records them; "
                    "otherwise GA4 key events. The two are never added together."),
}
_SORTS = {
    "-score": (PageValueScore.score.desc(),),
    "-priority": (PageValueScore.priority_rank.desc(), PageValueScore.score.desc()),
    "-ai_sessions": (func.coalesce(PageValueScore.ai_sessions, -1).desc(), PageValueScore.score.desc()),
    "-organic_sessions": (func.coalesce(PageValueScore.organic_sessions, -1).desc(), PageValueScore.score.desc()),
    "-gsc_clicks": (func.coalesce(PageValueScore.gsc_clicks, -1).desc(), PageValueScore.score.desc()),
    "-conversions": (func.coalesce(PageValueScore.conversions, -1).desc(), PageValueScore.score.desc()),
    "url": (PageValueScore.url.asc(),),
}
_HAS = {
    "ai": PageValueScore.ai_sessions > 0,
    "organic": PageValueScore.organic_sessions > 0,
    "search": PageValueScore.gsc_clicks > 0,
    "conversions": PageValueScore.conversions > 0,
    "issues": PageValueScore.issue_count > 0,
}


def _require_pro(db: Session, ctx: AuthContext) -> None:
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="Page Opportunities is a Pro feature.")


def _run_block(run: PageValueRun | None) -> dict | None:
    if run is None:
        return None
    return {"id": run.id, "status": run.status, "trigger": run.trigger, "crawl_job_id": run.crawl_job_id,
            "total_urls": run.total_urls, "processed_urls": run.processed_urls, "error": run.error,
            "created_at": run.created_at, "started_at": run.started_at, "finished_at": run.finished_at,
            "updated_at": run.updated_at, "active": run.status in PV_ACTIVE,
            "analysis_status": run.analysis_status, "analyzed_urls": run.analyzed_urls,
            "analysis_total": run.analysis_total}


def _completed_run(db: Session, monitor_id: str) -> PageValueRun | None:
    return pv.latest_run(db, monitor_id, PV_COMPLETED)


@router.get("/monitors/{monitor_id}/page-opportunities")
def overview(monitor_id: str, ctx: AuthContext = Depends(require_permission("report:view")),
             db: Session = Depends(get_db)):
    monitor = _owned_monitor(db, ctx, monitor_id)
    _require_pro(db, ctx)
    latest = pv.latest_run(db, monitor_id)
    done = _completed_run(db, monitor_id)
    crawl = pv.latest_completed_crawl(db, ctx.org_id, monitor_id)
    out = {"website": {"id": monitor.id, "name": monitor.name, "url": monitor.url},
           "has_completed_crawl": crawl is not None,
           "latest_run": _run_block(latest), "results": None, "definitions": DEFINITIONS}
    if done is None:
        return out
    q = db.query(PageValueScore).filter(PageValueScore.run_id == done.id)
    sources = pv.public_sources(done.inputs)
    ga4_ok = bool(sources.get("ga4", {}).get("available"))
    gsc_ok = bool(sources.get("gsc", {}).get("available"))
    conv_ok = sources.get("conversion_source") is not None

    def count(*conds):
        return q.filter(*conds).count()

    crawl_job = db.get(CrawlJob, done.crawl_job_id)
    types = dict(q.with_entities(PageValueScore.business_page_type, func.count(PageValueScore.id))
                 .group_by(PageValueScore.business_page_type).all())
    statuses = dict(q.with_entities(PageValueScore.classification_status, func.count(PageValueScore.id))
                    .group_by(PageValueScore.classification_status).all())
    out["results"] = {
        "run": _run_block(done),
        "scored_at": done.finished_at,
        "stale": crawl is not None and crawl.id != done.crawl_job_id,
        "counts": {
            "discovered_urls": crawl_job.discovered_count if crawl_job else None,
            "scored_urls": q.count(),
            "commercial_pages": count(PageValueScore.is_commercial.is_(True)),
            "high_priority": count(PageValueScore.priority == "high"),
            # None = the source isn't available for this website (not zero pages).
            "pages_with_ai_traffic": count(PageValueScore.ai_sessions > 0) if ga4_ok else None,
            "pages_with_organic_traffic": count(PageValueScore.organic_sessions > 0) if ga4_ok else None,
            "pages_with_search_clicks": count(PageValueScore.gsc_clicks > 0) if gsc_ok else None,
            "pages_with_conversions": count(PageValueScore.conversions > 0) if conv_ok else None,
        },
        "page_types": types, "classification_status": statuses, "sources": sources,
    }
    return out


@router.get("/monitors/{monitor_id}/page-opportunities/pages")
def list_pages(monitor_id: str,
               page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
               q: str | None = Query(default=None, max_length=200),
               page_type: str | None = Query(default=None, max_length=32),
               priority: str | None = Query(default=None, max_length=16),
               commercial: bool | None = Query(default=None),
               has: str | None = Query(default=None, max_length=16),
               sort: str = Query(default="-priority"),
               ctx: AuthContext = Depends(require_permission("report:view")),
               db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    _require_pro(db, ctx)
    if sort not in _SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(_SORTS)}.")
    if has is not None and has not in _HAS:
        raise HTTPException(status_code=422, detail=f"has must be one of {sorted(_HAS)}.")
    if page_type is not None and page_type not in BUSINESS_PAGE_TYPES:
        raise HTTPException(status_code=422, detail="Unknown page type.")
    if priority is not None and priority not in pv.PRIORITY_RANK:
        raise HTTPException(status_code=422, detail="Unknown priority.")
    done = _completed_run(db, monitor_id)
    if done is None:
        return {"total": 0, "page": page, "page_size": page_size, "pages": [], "scored": False}
    query = db.query(PageValueScore).filter(PageValueScore.run_id == done.id,
                                            PageValueScore.organization_id == ctx.org_id)
    if q:
        like = f"%{q}%"
        query = query.filter(PageValueScore.url.ilike(like) | PageValueScore.page_title.ilike(like))
    if page_type:
        query = query.filter(PageValueScore.business_page_type == page_type)
    if priority:
        query = query.filter(PageValueScore.priority == priority)
    if commercial is not None:
        query = query.filter(PageValueScore.is_commercial.is_(commercial))
    if has:
        query = query.filter(_HAS[has])
    total = query.count()
    rows = (query.order_by(*_SORTS[sort], PageValueScore.url.asc(), PageValueScore.id.asc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "page_size": page_size, "sort": sort, "scored": True,
            "pages": [pv.score_dict(r) for r in rows]}


@router.get("/monitors/{monitor_id}/page-opportunities/pages/{crawl_url_id}")
def page_detail(monitor_id: str, crawl_url_id: str,
                ctx: AuthContext = Depends(require_permission("report:view")),
                db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    _require_pro(db, ctx)
    done = _completed_run(db, monitor_id)
    row = (db.query(PageValueScore)
           .filter(PageValueScore.run_id == done.id, PageValueScore.crawl_url_id == crawl_url_id,
                   PageValueScore.organization_id == ctx.org_id).first()) if done else None
    if row is None:
        raise HTTPException(status_code=404, detail="Page not found.")
    crawl_url = db.get(CrawlUrl, crawl_url_id)
    job = db.get(CrawlJob, row.crawl_job_id)
    issues: list[dict] = []
    findings: list[dict] = []
    if crawl_url is not None and job is not None:
        _evaluated, issues = ov.page_issue_details(crawl_url)
        findings = ov.applicable_findings(db, job, crawl_url, issues, is_pro=True,
                                          free_preview=_FREE_FINDINGS_PREVIEW)
    comps = row.score_components or {}
    data_signals = [{"code": c, **{k: pv.DATA_ISSUES[c][k] for k in ("title", "severity", "kind", "why", "fix")}}
                    for c in comps.get("issue_codes") or [] if c in pv.DATA_ISSUES]
    return {
        **pv.score_dict(row),
        "classification": {"page_type": row.business_page_type, "status": row.classification_status,
                           "confidence": row.classification_confidence,
                           "evidence": row.classification_evidence or [],
                           "crawl_page_type": crawl_url.page_type if crawl_url else None},
        "why_it_matters": pv.why_it_matters(row),
        "score_breakdown": comps.get("components") or [],
        "priority_reason": comps.get("priority_reason"),
        "issues": issues, "data_signals": data_signals,
        "findings": [{k: f[k] for k in ("id", "finding_title", "priority", "status", "kind", "applies_because",
                                        "recommended_fix")} for f in findings],
        "recommended_action": pv.recommended_action(row, issues, findings),
        "sources": pv.public_sources(done.inputs),
        "crawl_job_id": row.crawl_job_id, "scored_at": done.finished_at,
    }


@router.post("/monitors/{monitor_id}/page-opportunities/refresh")
def refresh(monitor_id: str, ctx: AuthContext = Depends(require_permission("scan:run")),
            db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    _require_pro(db, ctx)
    run = pv.request_run(db, org_id=ctx.org_id, monitor_id=monitor_id, trigger="manual", user_id=ctx.user.id)
    if run is None:
        raise HTTPException(status_code=409, detail="Run a Site Intelligence analysis of this website first.")
    return _run_block(run)


@router.post("/monitors/{monitor_id}/page-opportunities/runs/{run_id}/cancel")
def cancel(monitor_id: str, run_id: str, ctx: AuthContext = Depends(require_permission("scan:run")),
           db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    _require_pro(db, ctx)
    run = db.get(PageValueRun, run_id)
    if not run or run.organization_id != ctx.org_id or run.monitor_id != monitor_id:
        raise HTTPException(status_code=404, detail="Run not found.")
    if not pv.needs_work(run):
        raise HTTPException(status_code=409, detail="This run has already finished.")
    pv.cancel_run(db, run)
    return _run_block(run)
