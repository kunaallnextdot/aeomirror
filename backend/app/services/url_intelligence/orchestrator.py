"""CrawlJob phase state machine: PENDING -> DISCOVERING -> CRAWLING -> ANALYZING ->
PREPARING -> COMPLETED (or FAILED/CANCELLED). `run_chunk` performs exactly ONE bounded unit of work
and returns — the caller (app.monitoring.runner) decides whether to re-enqueue a
continuation job, which is what makes a large crawl resumable/checkpointed across
many worker ticks instead of one long-running task.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    CrawlJob, CrawlUrl, CRAWL_ANALYZING, CRAWL_CANCELLED, CRAWL_COMPLETED,
    CRAWL_CRAWLING, CRAWL_DISCOVERING, CRAWL_FAILED, CRAWL_PENDING, CRAWL_PREPARING, URL_CRAWLED,
)
from app.services.url_intelligence import ai_analysis, discovery, recommendations


async def _run_analyze_chunk(db: Session, job: CrawlJob) -> bool:
    """Analyze up to `url_crawl_analyze_batch_size` NOT-YET-analyzed templates this
    chunk, one AI call per template cluster (Phase 6). Returns True when every
    crawled template has been analyzed."""
    template_ids = [
        row[0] for row in (
            db.query(CrawlUrl.template_id)
            .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED,
                    CrawlUrl.analyzed.is_(False), CrawlUrl.template_id.isnot(None))
            .distinct()
            .limit(settings.url_crawl_analyze_batch_size)
            .all()
        )
    ]
    for template_id in template_ids:
        members = (db.query(CrawlUrl)
                   .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED,
                           CrawlUrl.template_id == template_id)
                   .order_by(CrawlUrl.crawled_at.asc())
                   .all())
        if not members:
            continue
        representative = members[0]
        ai_analysis.analyze_template(db, job, representative, affected_url_count=len(members))
        for m in members:
            m.analyzed = True
            m.analyzed_at = datetime.utcnow()
        job.analyzed_count += len(members)
        db.commit()

    remaining = (db.query(CrawlUrl)
                 .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED,
                         CrawlUrl.analyzed.is_(False))
                 .count())
    return remaining == 0


def _cancelled(db: Session, job: CrawlJob) -> bool:
    """Re-read the phase before changing it: a user may have cancelled while this chunk
    ran, and the in-memory phase is then stale. Without this check the chunk's phase
    transition would silently overwrite "cancelled" and the crawl would carry on."""
    db.refresh(job, attribute_names=["phase"])
    return job.phase == CRAWL_CANCELLED


def _advance(db: Session, job: CrawlJob, phase: str) -> None:
    if _cancelled(db, job):
        return
    job.phase = phase
    if phase == CRAWL_COMPLETED:
        job.finished_at = datetime.utcnow()
    job.updated_at = datetime.utcnow()
    db.commit()


async def run_chunk(db: Session, job: CrawlJob) -> None:
    """Advance `job` by exactly one bounded chunk of work. `updated_at` is stamped on
    every chunk, so the UI can show when progress was last made (and flag a stall)."""
    if job.phase in (CRAWL_COMPLETED, CRAWL_FAILED, CRAWL_CANCELLED):
        return

    if job.phase == CRAWL_PENDING:
        job.started_at = job.started_at or datetime.utcnow()
        _advance(db, job, CRAWL_DISCOVERING)
        return   # exactly one bounded unit of work per call — the caller's next chunk
                 # (re-enqueued by app.monitoring.runner) performs the first real discovery step

    phase = job.phase   # read now: the chunk commits, and a reload may already say "cancelled"
    if phase == CRAWL_DISCOVERING:
        done = await discovery.run_discovery_chunk(db, job)
    elif phase == CRAWL_CRAWLING:
        done = await discovery.run_crawl_chunk(db, job)
    elif phase == CRAWL_ANALYZING:
        done = await _run_analyze_chunk(db, job)
    elif phase == CRAWL_PREPARING:
        if not _cancelled(db, job):
            recommendations.build_deterministic_findings(db, job)
            recommendations.carry_forward_status(db, job)
        done = True
    else:
        return

    nxt = {CRAWL_DISCOVERING: CRAWL_CRAWLING, CRAWL_CRAWLING: CRAWL_ANALYZING,
           CRAWL_ANALYZING: CRAWL_PREPARING, CRAWL_PREPARING: CRAWL_COMPLETED}[phase]
    if done:
        _advance(db, job, nxt)
    elif not _cancelled(db, job):
        job.updated_at = datetime.utcnow()
        db.commit()


def is_terminal(phase: str) -> bool:
    return phase in (CRAWL_COMPLETED, CRAWL_FAILED, CRAWL_CANCELLED)
