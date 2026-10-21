"""Scheduler (Phase 7): the scan-job queue. Deliberately separate from scanning.

Responsibilities:
- compute a monitor's next run from its frequency,
- enqueue due monitors as `scheduled_jobs` (deduplicated: at most one active job
  per monitor),
- atomically claim the next runnable job (claim-based so a future distributed
  worker can process the same queue safely),
- record completion, and retry failures with backoff up to max_attempts.

The actual scanning happens in runner.py; this module never fetches or scores.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    GA4_STATUS_ACTIVE, JOB_COMPLETED, JOB_FAILED, JOB_KIND_BULK_SCAN, JOB_KIND_GA4_SYNC,
    JOB_KIND_URL_CRAWL, JOB_PENDING, JOB_RUNNING, MONITOR_ACTIVE, GoogleAnalyticsConnection,
    Monitor, ScheduledJob,
)

logger = logging.getLogger("aeomirror.scheduler")

FREQ_DELTA = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def compute_next_scan(frequency: str, from_time: datetime | None = None) -> datetime | None:
    """Next run time for a frequency; None for 'manual'."""
    delta = FREQ_DELTA.get(frequency)
    if not delta:
        return None
    return (from_time or datetime.utcnow()) + delta


def has_active_job(db: Session, monitor_id: str) -> ScheduledJob | None:
    return (db.query(ScheduledJob)
            .filter(ScheduledJob.monitor_id == monitor_id,
                    ScheduledJob.status.in_((JOB_PENDING, JOB_RUNNING)))
            .first())


def _new_job(monitor: Monitor, kind: str, now: datetime) -> ScheduledJob:
    return ScheduledJob(
        monitor_id=monitor.id, organization_id=monitor.organization_id, kind=kind,
        status=JOB_PENDING, attempts=0, max_attempts=settings.monitor_job_max_attempts,
        scheduled_for=now, run_after=now, created_at=now, updated_at=now,
    )


def enqueue_due(db: Session, now: datetime | None = None) -> int:
    """Create jobs for active, non-manual monitors whose next_scan_at has passed.
    Dedupe: skip monitors that already have an active job. Advances next_scan_at so
    the same monitor isn't re-enqueued every tick."""
    now = now or datetime.utcnow()
    due = (db.query(Monitor)
           .filter(Monitor.status == MONITOR_ACTIVE,
                   Monitor.frequency != "manual",
                   Monitor.next_scan_at.isnot(None),
                   Monitor.next_scan_at <= now)
           .all())
    created = 0
    for m in due:
        if has_active_job(db, m.id):
            continue
        db.add(_new_job(m, "scheduled", now))
        m.next_scan_at = compute_next_scan(m.frequency, now)
        # Commit per monitor so the partial unique index makes the enqueue atomic: if a
        # second scheduler already enqueued this monitor, the insert raises IntegrityError
        # and we skip it (its next_scan_at advance rolls back with the failed insert).
        try:
            db.commit()
            created += 1
        except IntegrityError:
            db.rollback()
            logger.info("enqueue race: monitor %s already has an active job; skipped", m.id)
    return created


def enqueue_bulk_scan(db: Session, scan_id: str, org_id: str | None,
                      now: datetime | None = None) -> ScheduledJob:
    """Queue a background bulk scan for an already-created (PENDING) Scan row. A
    bulk_scan job has no monitor; it carries the scan_id (the URL list lives on the
    Scan row) and is claimed off the same queue as monitor jobs, dispatched by `kind`
    in the runner. Retries reuse the existing max_attempts/backoff machinery."""
    now = now or datetime.utcnow()
    job = ScheduledJob(
        monitor_id=None, scan_id=scan_id, organization_id=org_id,
        kind=JOB_KIND_BULK_SCAN, status=JOB_PENDING, attempts=0,
        max_attempts=settings.monitor_job_max_attempts,
        scheduled_for=now, run_after=now, created_at=now, updated_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_manual(db: Session, monitor: Monitor, now: datetime | None = None) -> ScheduledJob:
    """Queue an immediate manual scan. If one is already active, return it (dedupe)."""
    existing = has_active_job(db, monitor.id)
    if existing:
        return existing
    now = now or datetime.utcnow()
    job = _new_job(monitor, "manual", now)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent enqueue won the race; return the active job that exists now.
        db.rollback()
        return has_active_job(db, monitor.id)
    db.refresh(job)
    return job


def has_active_ga4_sync_job(db: Session, connection_id: str) -> ScheduledJob | None:
    return (db.query(ScheduledJob)
            .filter(ScheduledJob.connection_id == connection_id,
                    ScheduledJob.status.in_((JOB_PENDING, JOB_RUNNING)))
            .first())


def enqueue_due_ga4_syncs(db: Session, now: datetime | None = None) -> int:
    """Create ga4_sync jobs for active, property-selected connections whose
    next_sync_at has passed. Exact mirror of enqueue_due()'s monitor pattern: dedupe
    via the partial unique index (uq_scheduled_jobs_active_per_connection), commit
    per-connection so a concurrent scheduler race just loses gracefully rather than
    double-enqueuing."""
    now = now or datetime.utcnow()
    due = (db.query(GoogleAnalyticsConnection)
           .filter(GoogleAnalyticsConnection.status == GA4_STATUS_ACTIVE,
                   GoogleAnalyticsConnection.ga4_property_id.isnot(None),
                   GoogleAnalyticsConnection.next_sync_at.isnot(None),
                   GoogleAnalyticsConnection.next_sync_at <= now)
           .all())
    created = 0
    for conn in due:
        if has_active_ga4_sync_job(db, conn.id):
            continue
        job = ScheduledJob(
            connection_id=conn.id, organization_id=conn.organization_id,
            kind=JOB_KIND_GA4_SYNC, status=JOB_PENDING, attempts=0,
            max_attempts=settings.monitor_job_max_attempts,
            scheduled_for=now, run_after=now, created_at=now, updated_at=now,
        )
        db.add(job)
        try:
            db.commit()
            created += 1
        except IntegrityError:
            db.rollback()
            logger.info("enqueue race: connection %s already has an active sync job; skipped",
                        conn.id)
    return created


def enqueue_manual_ga4_sync(db: Session, connection: GoogleAnalyticsConnection,
                            now: datetime | None = None) -> ScheduledJob:
    """Queue an immediate GA4 sync. If one is already active for this connection,
    return it (dedupe) rather than queueing a second one."""
    existing = has_active_ga4_sync_job(db, connection.id)
    if existing:
        return existing
    now = now or datetime.utcnow()
    job = ScheduledJob(
        connection_id=connection.id, organization_id=connection.organization_id,
        kind=JOB_KIND_GA4_SYNC, status=JOB_PENDING, attempts=0,
        max_attempts=settings.monitor_job_max_attempts,
        scheduled_for=now, run_after=now, created_at=now, updated_at=now,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return has_active_ga4_sync_job(db, connection.id)
    db.refresh(job)
    return job


def claim_job(db: Session, now: datetime | None = None) -> ScheduledJob | None:
    """Atomically claim the next runnable pending job (pending -> running). Returns
    None if none is claimable. The status-guarded UPDATE makes this safe for
    multiple workers (only one wins the row)."""
    now = now or datetime.utcnow()
    job = (db.query(ScheduledJob)
           .filter(ScheduledJob.status == JOB_PENDING,
                   (ScheduledJob.run_after.is_(None)) | (ScheduledJob.run_after <= now))
           .order_by(ScheduledJob.scheduled_for.asc())
           .first())
    if not job:
        return None
    updated = (db.query(ScheduledJob)
               .filter(ScheduledJob.id == job.id, ScheduledJob.status == JOB_PENDING)
               .update({ScheduledJob.status: JOB_RUNNING, ScheduledJob.started_at: now,
                        ScheduledJob.attempts: ScheduledJob.attempts + 1,
                        ScheduledJob.updated_at: now}, synchronize_session=False))
    db.commit()
    if not updated:
        return None  # another worker claimed it first
    db.refresh(job)
    return job


def complete_job(db: Session, job: ScheduledJob, now: datetime | None = None) -> None:
    now = now or datetime.utcnow()
    job.status = JOB_COMPLETED
    job.finished_at = now
    job.updated_at = now
    job.error = None
    db.commit()


def has_active_url_crawl_job(db: Session, crawl_job_id: str) -> ScheduledJob | None:
    return (db.query(ScheduledJob)
            .filter(ScheduledJob.crawl_job_id == crawl_job_id,
                    ScheduledJob.status.in_((JOB_PENDING, JOB_RUNNING)))
            .first())


def enqueue_url_crawl_chunk(db: Session, crawl_job_id: str, org_id: str | None,
                            now: datetime | None = None) -> ScheduledJob:
    """Queue the next bounded chunk of a CrawlJob. Used both to start a brand-new job
    and, by the runner, to re-enqueue a continuation after each chunk — the partial
    unique index guarantees at most one active chunk per crawl job, so a restart or a
    slow worker can never process the same job's state concurrently. Dedupes like
    enqueue_manual: if a chunk is already active, returns it instead of double-queueing."""
    existing = has_active_url_crawl_job(db, crawl_job_id)
    if existing:
        return existing
    now = now or datetime.utcnow()
    job = ScheduledJob(
        crawl_job_id=crawl_job_id, organization_id=org_id,
        kind=JOB_KIND_URL_CRAWL, status=JOB_PENDING, attempts=0,
        max_attempts=settings.monitor_job_max_attempts,
        scheduled_for=now, run_after=now, created_at=now, updated_at=now,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return has_active_url_crawl_job(db, crawl_job_id)
    db.refresh(job)
    return job


def fail_job(db: Session, job: ScheduledJob, error, now: datetime | None = None) -> None:
    """Retry with backoff until max_attempts, then mark failed."""
    now = now or datetime.utcnow()
    job.error = str(error)[:500]
    job.updated_at = now
    if job.attempts < job.max_attempts:
        job.status = JOB_PENDING
        job.started_at = None
        job.run_after = now + timedelta(seconds=settings.monitor_job_retry_backoff_seconds)
    else:
        job.status = JOB_FAILED
        job.finished_at = now
    db.commit()
