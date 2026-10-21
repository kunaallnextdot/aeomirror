"""URL Intelligence Engine + AI Expert Analysis Engine + Expert Action Center API
(Phases 3/5/7/9). Every endpoint is authenticated and org-scoped: a crawl job is
looked up by (job_id, organization_id) — never a client-supplied org id — and a
mismatch is a 404, never a 403, so a job's existence is never leaked across orgs.

Free plan: crawl jobs are capped at a small sample (`entitlements.url_crawl_max_urls`)
and the Action Center shows a short, real (never fabricated) preview of the highest-
priority findings. Pro unlocks the full paginated/filterable inventory, the full
findings list, bulk status updates, and recrawl/verification — same Pro-gating
pattern already used by AI Business Impact / AI Visibility (`entitlements.is_pro`).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.api.routes_monitors import _owned_monitor
from app.billing import entitlements
from app.db.models import (
    CRAWL_CANCELLED, CRAWL_COMPLETED, CRAWL_FAILED, CrawlJob, CrawlUrl, FINDING_STATUSES,
    UrlFinding,
)
from app.db.session import get_db
from app.monitoring import scheduler
from app.scanner.bulk import normalize_url
from app.schemas.url_intelligence import CreateCrawlJobRequest, UpdateFindingStatusRequest
from app.services.url_intelligence.prioritization import priority_score
from app.services.url_intelligence.verification import verify_finding

router = APIRouter(tags=["url-intelligence"])

_FREE_FINDINGS_PREVIEW = 5
_ACTIVE_PHASES = ("pending", "discovering", "crawling", "analyzing")


def _require_pro(db: Session, ctx: AuthContext) -> None:
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="The full Expert Action Center is a Pro feature.")


def _owned_job(db: Session, ctx: AuthContext, monitor_id: str, job_id: str) -> CrawlJob:
    _owned_monitor(db, ctx, monitor_id)
    job = db.get(CrawlJob, job_id)
    if not job or job.organization_id != ctx.org_id or job.monitor_id != monitor_id:
        raise HTTPException(status_code=404, detail="Crawl job not found.")
    return job


def _job_summary(job: CrawlJob) -> dict:
    return {
        "id": job.id, "monitor_id": job.monitor_id, "seed_url": job.seed_url,
        "max_urls": job.max_urls, "phase": job.phase,
        "discovered_count": job.discovered_count, "eligible_count": job.eligible_count,
        "crawled_count": job.crawled_count, "analyzed_count": job.analyzed_count,
        "failed_count": job.failed_count, "skipped_count": job.skipped_count,
        "ai_calls_total": job.ai_calls_total, "ai_calls_cached": job.ai_calls_cached,
        "ai_tokens_input_total": job.ai_tokens_input_total,
        "ai_tokens_output_total": job.ai_tokens_output_total,
        "error": job.error, "created_at": job.created_at, "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@router.post("/monitors/{monitor_id}/crawl-jobs")
def create_crawl_job(monitor_id: str, body: CreateCrawlJobRequest,
                     ctx: AuthContext = Depends(require_permission("scan:run")),
                     db: Session = Depends(get_db)):
    monitor = _owned_monitor(db, ctx, monitor_id)
    active = (db.query(CrawlJob)
             .filter(CrawlJob.monitor_id == monitor_id, CrawlJob.phase.in_(_ACTIVE_PHASES))
             .first())
    if active:
        raise HTTPException(status_code=409, detail="A crawl is already running for this site.")

    ceiling = entitlements.url_crawl_max_urls(db, ctx.org_id)
    max_urls = min(body.max_urls, ceiling) if body.max_urls else ceiling

    seed = normalize_url(monitor.url) or monitor.url
    job = CrawlJob(
        organization_id=ctx.org_id, user_id=ctx.user.id, monitor_id=monitor_id,
        seed_url=seed, normalized_seed_url=seed, max_urls=max_urls,
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
    job.finished_at = datetime.utcnow()
    db.commit()
    return _job_summary(job)


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}/urls")
def list_crawl_urls(monitor_id: str, job_id: str,
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
                    status: str | None = Query(default=None), page_type: str | None = Query(default=None),
                    template_id: str | None = Query(default=None), q: str | None = Query(default=None),
                    ctx: AuthContext = Depends(require_permission("report:view")),
                    db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    query = db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == job.id)
    if status:
        query = query.filter(CrawlUrl.status == status)
    if page_type:
        query = query.filter(CrawlUrl.page_type == page_type)
    if template_id:
        query = query.filter(CrawlUrl.template_id == template_id)
    if q:
        query = query.filter(CrawlUrl.url.ilike(f"%{q}%"))
    total = query.count()
    rows = (query.order_by(CrawlUrl.discovered_at.asc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "total": total, "page": page, "page_size": page_size,
        "urls": [{
            "id": r.id, "url": r.url, "status": r.status, "skip_reason": r.skip_reason,
            "http_status": r.http_status, "canonical_url": r.canonical_url,
            "page_type": r.page_type, "template_id": r.template_id,
            "discovery_source": r.discovery_source, "discovery_depth": r.discovery_depth,
            "analyzed": r.analyzed, "error": r.error,
            "discovered_at": r.discovered_at, "crawled_at": r.crawled_at,
        } for r in rows],
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
            .order_by(func.count(CrawlUrl.id).desc())
            .all())
    return {"templates": [
        {"template_id": t, "page_type": pt, "url_count": c, "representative_url": u}
        for t, pt, c, u in rows
    ]}


def _finding_dict(f: UrlFinding) -> dict:
    scored = priority_score(f)
    return {
        "id": f.id, "url": f.url, "template_id": f.template_id,
        "affected_url_count": f.affected_url_count, "page_type": f.page_type,
        "finding_title": f.finding_title, "category": f.category,
        "why_it_matters": f.why_it_matters, "recommended_fix": f.recommended_fix,
        "expected_benefit": f.expected_benefit, "priority": f.priority,
        "confidence": f.confidence, "evidence_limitations": f.evidence_limitations,
        "verification_procedure": f.verification_procedure, "status": f.status,
        "verification_status": f.verification_status, "model_used": f.model_used,
        "cache_hit": f.cache_hit, "first_detected_at": f.first_detected_at,
        "last_checked_at": f.last_checked_at,
        "priority_score": scored["score"], "priority_reason": scored["reason"],
    }


@router.get("/monitors/{monitor_id}/crawl-jobs/{job_id}/findings")
def list_findings(monitor_id: str, job_id: str,
                  page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                  status: str | None = Query(default=None), priority: str | None = Query(default=None),
                  page_type: str | None = Query(default=None),
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    job = _owned_job(db, ctx, monitor_id, job_id)
    query = db.query(UrlFinding).filter(UrlFinding.crawl_job_id == job.id)
    if status:
        query = query.filter(UrlFinding.status == status)
    if priority:
        query = query.filter(UrlFinding.priority == priority)
    if page_type:
        query = query.filter(UrlFinding.page_type == page_type)

    is_pro = entitlements.is_pro(db, ctx.org_id)
    all_rows = query.all()
    scored = sorted(all_rows, key=lambda f: priority_score(f)["score"], reverse=True)
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
