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

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    JOB_COMPLETED, JOB_FAILED, JOB_KIND_BULK_SCAN, JOB_PENDING, JOB_RUNNING,
    MONITOR_ACTIVE, Monitor, ScheduledJob,
)

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
        created += 1
    if created:
        db.commit()
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
    db.commit()
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
