"""URL Intelligence Engine + AI Expert Analysis Engine + Expert Action Center API
(Phases 3/5/7/9), and the Site Intelligence overview / inventory / comparison read
models. Every endpoint is authenticated and org-scoped: a monitor is resolved through
`_owned_monitor` and a crawl job by (job_id, organization_id, monitor_id) — never a
client-supplied org id — and a mismatch is a 404, never a 403, so a job's existence is
never leaked across orgs.

Free plan: crawl jobs are capped at a small sample (`entitlements.url_crawl_max_urls`)
and findings are a short, real (never fabricated) preview of the highest-priority
ones; Pro unlocks the full findings list, bulk status updates, and recrawl/
verification — same Pro-gating pattern used by AI Business Impact / AI Visibility
(`entitlements.is_pro`). The URL inventory and each page's own evidence are available
on every plan (the free crawl itself is small).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from app.api.deps import AuthContext, require_permission
from app.api.routes_monitors import _owned_monitor
from app.billing import entitlements
from app.db.models import (
    CRAWL_ACTIVE_PHASES, CRAWL_CANCELLED, CRAWL_COMPLETED, CRAWL_ENGINE_VERSION, CRAWL_FAILED,
    FINDING_STATUSES, URL_CRAWLED, URL_DISCOVERED, URL_ELIGIBLE, URL_FAILED, URL_SKIPPED,
    CrawlJob, CrawlUrl, UrlFinding,
)
from app.db.session import get_db
from app.monitoring import scheduler
from app.scanner.bulk import normalize_url
from app.schemas.url_intelligence import CreateCrawlJobRequest, UpdateFindingStatusRequest
from app.services.url_intelligence import overview as ov
from app.services.url_intelligence.prioritization import priority_score
from app.services.url_intelligence.recommendations import previous_comparable_job
from app.services.url_intelligence.site_profile import build_site_profile
from app.services.url_intelligence.verification import verify_finding

router = APIRouter(tags=["url-intelligence"])

_FREE_FINDINGS_PREVIEW = 5
_OVERVIEW_PRIORITIES = 5


def _require_pro(db: Session, ctx: AuthContext) -> None:
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="The full Expert Action Center is a Pro feature.")


def _owned_job(db: Session, ctx: AuthContext, monitor_id: str, job_id: str) -> CrawlJob:
    _owned_monitor(db, ctx, monitor_id)
    job = db.get(CrawlJob, job_id)
    if not job or job.organization_id != ctx.org_id or job.monitor_id != monitor_id:
        raise HTTPException(status_code=404, detail="Crawl job not found.")
    return job


_job_summary = ov.job_summary
_finding_dict = ov.finding_dict


@router.post("/monitors/{monitor_id}/crawl-jobs")
def create_crawl_job(monitor_id: str, body: CreateCrawlJobRequest,
                     ctx: AuthContext = Depends(require_permission("scan:run")),
                     db: Session = Depends(get_db)):
    monitor = _owned_monitor(db, ctx, monitor_id)
    active = (db.query(CrawlJob)
             .filter(CrawlJob.monitor_id == monitor_id, CrawlJob.phase.in_(CRAWL_ACTIVE_PHASES))
             .first())
    if active:
        raise HTTPException(status_code=409, detail="A crawl is already running for this site.")

    ceiling = entitlements.url_crawl_max_urls(db, ctx.org_id)
    max_urls = min(body.max_urls, ceiling) if body.max_urls else ceiling

    seed = normalize_url(monitor.url) or monitor.url
    job = CrawlJob(
        organization_id=ctx.org_id, user_id=ctx.user.id, monitor_id=monitor_id,
        seed_url=seed, normalized_seed_url=seed, max_urls=max_urls,
        engine_version=CRAWL_ENGINE_VERSION, updated_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    scheduler.enqueue_url_crawl_chunk(db, job.id, ctx.org_id)
    return _job_summary(job)


@router.get("/monitors/{monitor_id}/crawl-jobs")
def list_crawl_jobs(monitor_id: str, ctx: AuthContext = Depends(require_permission("report:view")),
                    db: Session = Depends(get_db)):
    _owned_monitor(db, ctx, monitor_id)
    jobs = (db.query(CrawlJob)
            .filter(CrawlJob.monitor_id == monitor_id, CrawlJob.organization_id == ctx.org_id)
            .order_by(CrawlJob.created_at.desc())
            .limit(20).all())
    return {"jobs": [_job_summary(j) for j in jobs]}


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}")
def get_crawl_job(monitor_id: str, job_id: str,
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    return _job_summary(job)


@router.post("/monitors/{monitor_id}/crawl-jobs/{job_id}/cancel")
def cancel_crawl_job(monitor_id: str, job_id: str,
                     ctx: AuthContext = Depends(require_permission("scan:run")),
                     db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    if job.phase in (CRAWL_COMPLETED, CRAWL_FAILED, CRAWL_CANCELLED):
        return _job_summary(job)
    job.phase = CRAWL_CANCELLED
    job.finished_at = job.updated_at = datetime.utcnow()
    db.commit()
    return _job_summary(job)


# ------------------------------------------------------------------ overview

@router.get("/monitors/{monitor_id}/site-intelligence")
def site_intelligence_overview(monitor_id: str,
                               ctx: AuthContext = Depends(require_permission("report:view")),
                               db: Session = Depends(get_db)):
    """Everything the Site Intelligence landing view needs, in one request: the
    latest analysis's real state and progress, and results from the latest COMPLETED
    one (a running or failed analysis never replaces finished results)."""
    monitor = _owned_monitor(db, ctx, monitor_id)
    latest, completed = ov.latest_jobs(db, ctx.org_id, monitor_id)
    is_pro = entitlements.is_pro(db, ctx.org_id)
    out = {
        "website": {"id": monitor.id, "name": monitor.name, "url": monitor.url, "domain": monitor.normalized_url},
        "is_pro": is_pro, "latest_job": None, "status": None, "results": None,
        "can_start": latest is None or latest.phase not in CRAWL_ACTIVE_PHASES,
    }
    if latest is not None:
        counts = ov.url_counts(db, latest)
        out["latest_job"] = _job_summary(latest)
        out["status"] = {**ov.status_block(db, latest, counts), "counts": counts}
    if completed is not None:
        counts = counts if latest is completed else ov.url_counts(db, completed)
        fc = ov.finding_counts(db, completed)
        limit = _OVERVIEW_PRIORITIES if is_pro else min(_OVERVIEW_PRIORITIES, _FREE_FINDINGS_PREVIEW)
        baseline = previous_comparable_job(db, completed)
        cmp = ov.comparability(completed, baseline)
        if cmp["available"]:
            cmp = {"available": True, "baseline_job_id": baseline.id, "baseline_finished_at": baseline.finished_at,
                   "findings": ov.compare_findings(db, completed, baseline)}
        out["results"] = {
            "job": _job_summary(completed), "counts": counts, "findings": fc,
            "status": ov.status_block(db, completed, counts),
            "top_priorities": ov.top_priorities(db, completed, limit),
            "locked_count": 0 if is_pro else max(0, fc["open"] - _FREE_FINDINGS_PREVIEW),
            "comparison": cmp,
        }
    return out


@router.get("/monitors/{monitor_id}/site-intelligence/compare")
def compare_crawls(monitor_id: str, job_id: str | None = Query(default=None),
                   baseline_id: str | None = Query(default=None),
                   ctx: AuthContext = Depends(require_permission("report:view")),
                   db: Session = Depends(get_db)):
    """Current vs baseline analysis of the SAME website. Defaults: latest completed vs
    the completed one before it. Refuses incompatible baselines rather than comparing."""
    _owned_monitor(db, ctx, monitor_id)
    if job_id:
        current = _owned_job(db, ctx, monitor_id, job_id)
    else:
        current = ov.latest_jobs(db, ctx.org_id, monitor_id)[1]
    if current is None or current.phase != CRAWL_COMPLETED:
        return {"available": False, "reason": "no_completed_analysis",
                "message": "There is no completed analysis to compare yet."}
    baseline = (_owned_job(db, ctx, monitor_id, baseline_id) if baseline_id
                else previous_comparable_job(db, current))
    if baseline is not None and (baseline.phase != CRAWL_COMPLETED or baseline.id == current.id):
        raise HTTPException(status_code=422, detail="The baseline must be a different, completed analysis.")
    return ov.compare_jobs(db, current, baseline, include_pages=True)


# ------------------------------------------------------------------ URL inventory

_VIEWS = {
    "all": lambda q: q,
    "not_crawled": lambda q: q.filter(CrawlUrl.status.in_((URL_DISCOVERED, URL_ELIGIBLE))),
    "not_analyzed": lambda q: q.filter(CrawlUrl.status == URL_CRAWLED, CrawlUrl.analyzed.is_(False)),
    "failed": lambda q: q.filter(CrawlUrl.status == URL_FAILED),
    "skipped": lambda q: q.filter(CrawlUrl.status == URL_SKIPPED),
    "has_issues": lambda q: q.filter(CrawlUrl.issue_count > 0),
    "high_priority": lambda q: q.filter(CrawlUrl.issue_severity >= 3),
}
_SORTS = {
    "url": (CrawlUrl.url.asc(),),
    "-url": (CrawlUrl.url.desc(),),
    "-issues": (func.coalesce(CrawlUrl.issue_severity, -1).desc(), func.coalesce(CrawlUrl.issue_count, -1).desc(), CrawlUrl.url.asc()),
    "issues": (func.coalesce(CrawlUrl.issue_severity, -1).asc(), func.coalesce(CrawlUrl.issue_count, -1).asc(), CrawlUrl.url.asc()),
    "-crawled_at": (CrawlUrl.crawled_at.desc(), CrawlUrl.url.asc()),
    "crawled_at": (CrawlUrl.crawled_at.asc(), CrawlUrl.url.asc()),
    "http_status": (func.coalesce(CrawlUrl.http_status, 0).desc(), CrawlUrl.url.asc()),
    "discovered": (CrawlUrl.discovered_at.asc(),),
}
_HTTP_CLASSES = {"2xx": (200, 300), "3xx": (300, 400), "4xx": (400, 500), "5xx": (500, 600)}


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}/urls")
def list_crawl_urls(monitor_id: str, job_id: str,
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
                    status: str | None = Query(default=None), page_type: str | None = Query(default=None),
                    template_id: str | None = Query(default=None), q: str | None = Query(default=None, max_length=200),
                    view: str = Query(default="all"), http: str | None = Query(default=None, max_length=8),
                    sort: str = Query(default="discovered"),
                    ctx: AuthContext = Depends(require_permission("report:view")),
                    db: Session = Depends(get_db)):
    """Server-side filtered, sorted and paginated page inventory. Summary columns only —
    a page's evidence is fetched by the detail endpoint when it is opened. Every sort
    ends on a unique key so pagination is stable."""
    job = _owned_job(db, ctx, monitor_id, job_id)
    if view not in _VIEWS:
        raise HTTPException(status_code=422, detail=f"view must be one of {sorted(_VIEWS)}.")
    if sort not in _SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(_SORTS)}.")
    query = _VIEWS[view](db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == job.id))
    if status:
        query = query.filter(CrawlUrl.status == status)
    if page_type:
        query = query.filter(CrawlUrl.page_type == page_type)
    if template_id:
        query = query.filter(CrawlUrl.template_id == template_id)
    if http:
        if http in _HTTP_CLASSES:
            lo, hi = _HTTP_CLASSES[http]
            query = query.filter(CrawlUrl.http_status >= lo, CrawlUrl.http_status < hi)
        elif http.isdigit():
            query = query.filter(CrawlUrl.http_status == int(http))
        else:
            raise HTTPException(status_code=422, detail="http must be 2xx/3xx/4xx/5xx or a status code.")
    if q:
        like = f"%{q}%"
        query = query.filter(CrawlUrl.url.ilike(like) | CrawlUrl.page_title.ilike(like))
    total = query.count()
    rows = (query.options(load_only(
                CrawlUrl.id, CrawlUrl.url, CrawlUrl.status, CrawlUrl.skip_reason, CrawlUrl.http_status,
                CrawlUrl.canonical_url, CrawlUrl.page_type, CrawlUrl.template_id, CrawlUrl.discovery_source,
                CrawlUrl.discovery_depth, CrawlUrl.analyzed, CrawlUrl.error, CrawlUrl.discovered_at,
                CrawlUrl.crawled_at, CrawlUrl.analyzed_at, CrawlUrl.page_title, CrawlUrl.issue_count,
                CrawlUrl.issue_severity))
            .order_by(*_SORTS[sort], CrawlUrl.id.asc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "total": total, "page": page, "page_size": page_size, "view": view, "sort": sort,
        "urls": [{
            "id": r.id, "url": r.url, "status": r.status, "skip_reason": r.skip_reason,
            "http_status": r.http_status, "canonical_url": r.canonical_url,
            "page_type": r.page_type, "template_id": r.template_id,
            "discovery_source": r.discovery_source, "discovery_depth": r.discovery_depth,
            "analyzed": r.analyzed, "error": r.error,
            "discovered_at": r.discovered_at, "crawled_at": r.crawled_at, "analyzed_at": r.analyzed_at,
            "page_title": r.page_title, "issue_count": r.issue_count,
            "priority": ov.SEVERITY_LABEL.get(r.issue_severity) if r.issue_severity is not None else None,
        } for r in rows],
    }


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}/urls/{url_id}")
def get_crawl_url(monitor_id: str, job_id: str, url_id: str,
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    """One page: its full evidence, the issues checked on it, the findings that apply
    (and on what basis), its template, and stored traffic for it."""
    job = _owned_job(db, ctx, monitor_id, job_id)
    row = db.get(CrawlUrl, url_id)
    if not row or row.crawl_job_id != job.id or row.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Page not found.")
    monitor = _owned_monitor(db, ctx, monitor_id)
    is_pro = entitlements.is_pro(db, ctx.org_id)
    evaluated, issues = ov.page_issue_details(row)

    template = None
    if row.template_id:
        size = (db.query(func.count(CrawlUrl.id))
                .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED,
                        CrawlUrl.template_id == row.template_id).scalar() or 0)
        rep = (db.query(CrawlUrl.id, CrawlUrl.url)
               .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED,
                       CrawlUrl.template_id == row.template_id)
               .order_by(CrawlUrl.crawled_at.asc(), CrawlUrl.id.asc()).first())
        template = {"template_id": row.template_id, "page_type": row.page_type, "page_count": size,
                    "representative_url": rep[1] if rep else None,
                    "is_representative": bool(rep and rep[0] == row.id)}

    applies = ov.applicable_findings(db, job, row, issues, is_pro=is_pro, free_preview=_FREE_FINDINGS_PREVIEW)

    return {
        "id": row.id, "url": row.url, "status": row.status, "skip_reason": row.skip_reason,
        "http_status": row.http_status, "canonical_url": row.canonical_url, "page_type": row.page_type,
        "page_title": row.page_title, "error": row.error, "discovery_source": row.discovery_source,
        "discovery_depth": row.discovery_depth, "discovered_at": row.discovered_at,
        "crawled_at": row.crawled_at, "analyzed": row.analyzed, "analyzed_at": row.analyzed_at,
        "evidence": row.evidence, "evidence_extracted_at": row.crawled_at,
        "issues_evaluated": evaluated, "issues": issues,
        "template": template, "findings": applies, "is_pro": is_pro,
        "traffic": ov.page_traffic(db, monitor, row.url, is_pro),
    }


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}/templates")
def list_templates(monitor_id: str, job_id: str,
                   ctx: AuthContext = Depends(require_permission("report:view")),
                   db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    rows = (db.query(CrawlUrl.template_id, CrawlUrl.page_type, func.count(CrawlUrl.id),
                     func.min(CrawlUrl.url))
            .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == "crawled",
                    CrawlUrl.template_id.isnot(None))
            .group_by(CrawlUrl.template_id, CrawlUrl.page_type)
            .order_by(func.count(CrawlUrl.id).desc(), CrawlUrl.template_id.asc())
            .all())
    return {"templates": [
        {"template_id": t, "page_type": pt, "url_count": c, "representative_url": u}
        for t, pt, c, u in rows
    ]}


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}/site-profile")
def get_site_profile(monitor_id: str, job_id: str,
                     ctx: AuthContext = Depends(require_permission("report:view")),
                     db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    return build_site_profile(db, job)


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}/findings")
def list_findings(monitor_id: str, job_id: str,
                  page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                  status: str | None = Query(default=None), priority: str | None = Query(default=None),
                  page_type: str | None = Query(default=None), kind: str | None = Query(default=None),
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    """Findings ranked by the explainable priority score (ties broken by id, so the
    order is deterministic). `kind=insufficient_evidence` lists the findings the
    analysis could not support; every other view excludes them."""
    job = _owned_job(db, ctx, monitor_id, job_id)
    query = db.query(UrlFinding).filter(UrlFinding.crawl_job_id == job.id)
    if kind == "insufficient_evidence":
        query = query.filter(UrlFinding.confidence == "insufficient_evidence")
    else:
        query = ov.actionable(query)
    if status:
        query = query.filter(UrlFinding.status == status)
    if priority:
        query = query.filter(UrlFinding.priority == priority)
    if page_type:
        query = query.filter(UrlFinding.page_type == page_type)

    is_pro = entitlements.is_pro(db, ctx.org_id)
    rows = query.all()
    if kind in ("confirmed", "potential"):
        rows = [f for f in rows if ov.finding_kind(f) == kind]
    scored = sorted(rows, key=lambda f: (-priority_score(f)["score"], f.id))
    total = len(scored)

    if not is_pro:
        preview = scored[:_FREE_FINDINGS_PREVIEW]
        return {"total": total, "page": 1, "page_size": _FREE_FINDINGS_PREVIEW,
                "is_pro": False, "locked_count": max(0, total - len(preview)),
                "findings": [_finding_dict(f) for f in preview]}

    start = (page - 1) * page_size
    rows = scored[start:start + page_size]
    return {"total": total, "page": page, "page_size": page_size, "is_pro": True,
            "locked_count": 0, "findings": [_finding_dict(f) for f in rows]}


def _owned_finding(db: Session, ctx: AuthContext, job: CrawlJob, finding_id: str) -> UrlFinding:
    f = db.get(UrlFinding, finding_id)
    if not f or f.organization_id != ctx.org_id or f.crawl_job_id != job.id:
        raise HTTPException(status_code=404, detail="Finding not found.")
    return f


@router.patch("/monitors/{monitor_id}/crawl-jobs/{job_id}/findings/{finding_id}")
def update_finding_status(monitor_id: str, job_id: str, finding_id: str,
                          body: UpdateFindingStatusRequest,
                          ctx: AuthContext = Depends(require_permission("scan:run")),
                          db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    _require_pro(db, ctx)
    if body.status not in FINDING_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {FINDING_STATUSES}.")
    f = _owned_finding(db, ctx, job, finding_id)
    f.status = body.status
    db.commit()
    return _finding_dict(f)


@router.post("/monitors/{monitor_id}/crawl-jobs/{job_id}/findings/bulk-status")
def bulk_update_finding_status(monitor_id: str, job_id: str, body: dict,
                               ctx: AuthContext = Depends(require_permission("scan:run")),
                               db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    _require_pro(db, ctx)
    status = body.get("status")
    finding_ids = body.get("finding_ids") or []
    if status not in FINDING_STATUSES or not isinstance(finding_ids, list) or not finding_ids:
        raise HTTPException(status_code=422, detail="Provide status and a non-empty finding_ids list.")
    updated = (db.query(UrlFinding)
               .filter(UrlFinding.id.in_(finding_ids[:500]), UrlFinding.crawl_job_id == job.id,
                       UrlFinding.organization_id == ctx.org_id)
               .update({UrlFinding.status: status}, synchronize_session=False))
    db.commit()
    return {"updated": updated}


@router.post("/monitors/{monitor_id}/crawl-jobs/{job_id}/findings/{finding_id}/recrawl")
async def recrawl_finding(monitor_id: str, job_id: str, finding_id: str,
                          ctx: AuthContext = Depends(require_permission("scan:run")),
                          db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    _require_pro(db, ctx)
    f = _owned_finding(db, ctx, job, finding_id)
    status = await verify_finding(db, f)
    return {"verification_status": status, "finding": _finding_dict(f)}
