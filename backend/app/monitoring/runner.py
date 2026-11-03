"""Monitor scan runner (Phase 7): executes one scan for a monitor and records the
outcome — history snapshot, change detection, alerts, notifications, and the
monitor's rolling state. Uses the shared scanning primitive (routes_scan.run_scan)
so scanning logic is not duplicated; the scheduler decides *when*, this decides
*what happens with the result*.
"""
from __future__ import annotations

import logging
from datetime import datetime

import anyio
from sqlalchemy.orm import Session

from app.core.ssrf import validate_url
from app.db.models import (
    CRAWL_COMPLETED, CRAWL_FAILED, JOB_FAILED, JOB_KIND_BULK_SCAN, JOB_KIND_GA4_SYNC, JOB_KIND_GSC_SYNC,
    JOB_KIND_PAGE_VALUE, JOB_KIND_URL_CRAWL, JOB_KIND_URL_RECHECK,
    SCAN_COMPLETED, SCAN_FAILED, CrawlJob, GoogleAnalyticsConnection, Monitor, MonitorHistory, Scan,
)
from app.monitoring import scheduler
from app.monitoring.alerts import evaluate_alerts, persist_alerts
from app.monitoring.changes import detect_changes, issue_count, sections_by_id
from app.monitoring.notifications import notify_critical_alerts
from app.scanner.signals.base import status_from_score

logger = logging.getLogger("aeomirror.monitor_runner")


async def run_scan_for_monitor(db: Session, monitor: Monitor) -> dict:
    """Scan the monitor's URL, record history + changes + alerts, notify, and update
    the monitor. Returns a summary dict. Raises on scan failure (caller handles retry).
    Imported lazily to avoid an import cycle with the API layer."""
    from app.api.routes_scan import run_scan  # scanning primitive (lazy import)

    prev_result = None
    if monitor.latest_scan_id:
        prev = db.get(Scan, monitor.latest_scan_id)
        prev_result = prev.result if prev else None

    safe_url = validate_url(monitor.url)  # raises UnsafeUrlError on a bad URL
    # A synthetic requester id (not rate-limited: run_scan doesn't apply the limiter).
    payload = await run_scan(db, safe_url, ip="scheduler",
                             org_id=monitor.organization_id, user_id=monitor.user_id,
                             monitor_id=monitor.id)

    from app.admin import flags   # feature-flag gate (lazy import)

    curr = payload   # has 'sections' + 'overall_score'
    scan_id = payload["scan_id"]
    overall = payload.get("overall_score")
    changes = detect_changes(prev_result, curr)
    # Alert generation can be disabled globally via the admin feature flag.
    alert_dicts = evaluate_alerts(changes, prev_result, curr) if flags.is_enabled(db, "alerts") else []

    db.add(MonitorHistory(
        monitor_id=monitor.id, organization_id=monitor.organization_id, scan_id=scan_id,
        overall_score=overall,
        status=status_from_score(overall) if overall is not None else None,
        issue_count=issue_count(curr),
        scores={sid: s.get("score") for sid, s in sections_by_id(curr).items()},
        changes=changes,
    ))

    now = datetime.utcnow()
    monitor.last_scan_at = now
    monitor.latest_scan_id = scan_id
    monitor.latest_score = overall
    if monitor.frequency != "manual" and monitor.next_scan_at is None:
        monitor.next_scan_at = scheduler.compute_next_scan(monitor.frequency, now)
    db.commit()

    alerts = persist_alerts(db, monitor, scan_id, alert_dicts)
    try:
        # notify_critical_alerts does a blocking SMTP send; this runs on the async scan
        # path, so offload to a worker thread to avoid blocking the event loop.
        await anyio.to_thread.run_sync(notify_critical_alerts, db, monitor, alerts)
    except Exception as e:  # notifications are best-effort
        logger.warning("critical alert notification failed: %s", type(e).__name__)

    return {"scan_id": scan_id, "overall_score": overall,
            "alerts": len(alerts), "changes": changes}


async def process_job(db: Session, job) -> None:
    """Run one claimed job to completion, dispatching by kind. Monitor jobs scan a
    monitor; bulk_scan jobs execute a background bulk scan. Both record success or
    failure (with retry) on the same queue."""
    if job.kind == JOB_KIND_BULK_SCAN:
        await _process_bulk_scan_job(db, job)
        return
    if job.kind == JOB_KIND_GA4_SYNC:
        await _process_ga4_sync_job(db, job)
        return
    if job.kind == JOB_KIND_URL_CRAWL:
        await _process_url_crawl_job(db, job)
        return
    if job.kind == JOB_KIND_GSC_SYNC:
        await _process_gsc_sync_job(db, job)
        return
    if job.kind == JOB_KIND_PAGE_VALUE:
        _process_page_value_job(db, job)
        return
    if job.kind == JOB_KIND_URL_RECHECK:
        await _process_url_recheck_job(db, job)
        return

    monitor = db.get(Monitor, job.monitor_id)
    if not monitor:
        scheduler.fail_job(db, job, "monitor no longer exists")
        return
    # A retry of a job whose scan already finished (the worker died after recording the
    # scan but before marking the job done) must not scan, alert and email again.
    if (job.attempts or 0) > 1 and monitor.last_scan_at and job.created_at \
            and monitor.last_scan_at >= job.created_at:
        scheduler.complete_job(db, job)
        return
    try:
        await run_scan_for_monitor(db, monitor)
        scheduler.complete_job(db, job)
    except Exception as e:
        db.rollback()
        logger.warning("monitor job %s failed: %s", job.id, type(e).__name__)
        scheduler.fail_job(db, job, e)


async def _process_ga4_sync_job(db: Session, job) -> None:
    """Execute one GA4 traffic-sync job. The connection is re-loaded from the DB and
    its OWN persisted organization_id is what every downstream check (status,
    property, credential) uses — the job row is never trusted as proof of
    authorization on its own, only as a pointer to re-fetch the real record."""
    from app.services.integrations.google_analytics import run_sync

    connection = db.get(GoogleAnalyticsConnection, job.connection_id)
    if not connection:
        scheduler.fail_job(db, job, "connection no longer exists")
        return
    if connection.organization_id != job.organization_id:
        scheduler.fail_job(db, job, "connection/job organization mismatch")
        return
    if not await _run_google_sync(db, job, connection, run_sync, "ga4"):
        return
    _rescore_after_sync(db, connection.organization_id, "ga4_sync")


async def _run_google_sync(db: Session, job, connection, run_sync, label: str) -> bool:
    """Run one GA4 / Search Console sync. Retries are owned by the CONNECTION, not the
    job: a transient failure has already pushed connection.next_sync_at out with
    backoff (honouring Retry-After) and a permanent one has set status=error, so the
    job itself is complete either way — re-running it in two minutes would ignore that
    backoff. An unexpected error is recorded as a transient failure for the same
    reason, so a bug can't re-enqueue a sync every tick. True = synced."""
    from app.services.integrations.google_errors import SyncDeferred, SyncStopped, record_transient
    try:
        await run_sync(db, connection)
        scheduler.complete_job(db, job)
        return True
    except (SyncDeferred, SyncStopped) as e:
        logger.info("%s sync %s not completed: %s", label, connection.id, e)
        scheduler.complete_job(db, job)
        return False
    except Exception as e:
        db.rollback()
        logger.warning("%s sync job %s failed: %s", label, job.id, type(e).__name__)
        try:
            db.refresh(connection)
            record_transient(db, connection, "sync_failed")
        except Exception:   # noqa: BLE001
            db.rollback()
        scheduler.complete_job(db, job)
        return False


def _rescore_after_sync(db: Session, org_id: str, trigger: str) -> None:
    """New GA4 / Search Console numbers → rescore the affected websites' pages.
    Best-effort: a failure here never turns a successful sync into a failed job."""
    from app.services.url_intelligence import page_value
    try:
        page_value.request_runs_after_sync(db, org_id, trigger)
    except Exception as e:   # noqa: BLE001
        db.rollback()
        logger.warning("page value rescore after %s failed: %s", trigger, type(e).__name__)


async def _process_gsc_sync_job(db: Session, job) -> None:
    """Search Console twin of _process_ga4_sync_job: re-load the connection and trust
    its own organization_id, never the job row's."""
    from app.db.models import SearchConsoleConnection
    from app.services.integrations.search_console import run_sync

    connection = db.get(SearchConsoleConnection, job.connection_id)
    if not connection:
        scheduler.fail_job(db, job, "connection no longer exists")
        return
    if connection.organization_id != job.organization_id:
        scheduler.fail_job(db, job, "connection/job organization mismatch")
        return
    if not await _run_google_sync(db, job, connection, run_sync, "gsc"):
        return
    _rescore_after_sync(db, connection.organization_id, "gsc_sync")


def _process_page_value_job(db: Session, job) -> None:
    """One bounded chunk of a PageValueRun, then a continuation if work remains —
    the same resumable pattern as url_crawl."""
    from app.db.models import PV_ACTIVE, PV_FAILED, PageValueRun
    from app.services.url_intelligence import page_value

    run = db.get(PageValueRun, job.page_value_run_id)
    if not run or run.organization_id != job.organization_id:
        scheduler.fail_job(db, job, "page value run no longer exists")
        return
    try:
        page_value.run_chunk(db, run)
        scheduler.complete_job(db, job)
        if page_value.needs_work(run):
            scheduler.enqueue_page_value_chunk(db, run.id, run.organization_id)
    except Exception as e:
        db.rollback()
        logger.warning("page_value job %s failed: %s", job.id, type(e).__name__)
        scheduler.fail_job(db, job, e)
        if job.status == JOB_FAILED:
            run = db.get(PageValueRun, job.page_value_run_id)
            if run and run.status == "completed" and run.analysis_status in page_value.ANALYSIS_ACTIVE:
                # Scores are complete and stay; earlier page analyses are kept.
                run.analysis_status = "failed"
                run.analysis_error = "Page analysis stopped early. Already analysed pages are kept."
                db.commit()
            elif run and run.status in PV_ACTIVE:
                run.status, run.finished_at = PV_FAILED, datetime.utcnow()
                run.error = "Scoring could not be completed. Please try again."
                db.commit()


async def _process_url_recheck_job(db: Session, job) -> None:
    """One bounded batch of a website's due fix re-checks and performance snapshots,
    then a delayed continuation if more are due. Per-page failures are handled inside
    the batch; only an unexpected error fails (and retries) the job."""
    from app.services.url_intelligence import optimization

    monitor = db.get(Monitor, job.recheck_monitor_id)
    if not monitor or monitor.organization_id != job.organization_id:
        scheduler.fail_job(db, job, "website no longer exists")
        return
    try:
        res = await optimization.run_due_checks(db, monitor)
        scheduler.complete_job(db, job)
        if res["more"]:
            scheduler.enqueue_url_recheck(db, monitor.id, monitor.organization_id, delay_seconds=60)
    except Exception as e:
        db.rollback()
        logger.warning("url_recheck job %s failed: %s", job.id, type(e).__name__)
        scheduler.fail_job(db, job, e)


def _mark_scan_failed(db: Session, scan_id: str, error: Exception) -> None:
    """Finalize a bulk scan that exhausted its retries: FAILED status + a safe,
    user-facing error message in result (never leaks internal exception detail)."""
    scan = db.get(Scan, scan_id)
    if not scan:
        return
    scan.status = SCAN_FAILED
    scan.result = {**(scan.result or {}),
                   "error": "The bulk scan could not be completed. Please try again."}
    if not scan.progress:
        scan.progress = {"total": None, "done": 0, "failed": 0, "current_url": None}
    db.commit()


async def _process_bulk_scan_job(db: Session, job) -> None:
    """Execute a bulk scan job. Per-URL failures never reach here (they're handled
    inside execute_bulk_scan); only a fatal failure raises. On a fatal error the job
    is retried via fail_job, and on the FINAL attempt the Scan row is marked FAILED so
    the client stops polling and can offer a retry."""
    from app.api.routes_scan import execute_bulk_scan   # lazy import (avoids cycle)

    scan = db.get(Scan, job.scan_id)
    if not scan:
        scheduler.fail_job(db, job, "scan no longer exists")
        return
    if scan.status == SCAN_COMPLETED:   # finished before an interrupted worker could mark the job
        scheduler.complete_job(db, job)
        return
    try:
        await execute_bulk_scan(db, scan)
        scheduler.complete_job(db, job)
    except Exception as e:
        db.rollback()
        logger.warning("bulk scan job %s failed: %s", job.id, type(e).__name__)
        scheduler.fail_job(db, job, e)
        if job.status == JOB_FAILED:        # no retries left -> the scan is done, failed
            _mark_scan_failed(db, job.scan_id, e)


def _score_completed_crawl(db: Session, crawl_job) -> None:
    """A finished crawl → score its pages (Pro only; best-effort)."""
    from app.billing import entitlements
    from app.services.url_intelligence import page_value
    try:
        if entitlements.is_pro(db, crawl_job.organization_id):
            page_value.request_run(db, org_id=crawl_job.organization_id, monitor_id=crawl_job.monitor_id,
                                   trigger="crawl_completed")
    except Exception as e:   # noqa: BLE001
        db.rollback()
        logger.warning("page value run after crawl %s failed: %s", crawl_job.id, type(e).__name__)


async def _process_url_crawl_job(db: Session, job) -> None:
    """Execute ONE bounded chunk of a CrawlJob (discovery/crawl/analyze — see
    app.services.url_intelligence.orchestrator) and, if the job isn't finished yet,
    re-enqueue a continuation chunk. This is what makes a 100k-URL crawl resumable
    across many worker ticks instead of needing one long-running task; the partial
    unique index on scheduled_jobs.crawl_job_id guarantees only one chunk per crawl
    job is ever active at a time."""
    from app.services.url_intelligence import orchestrator

    crawl_job = db.get(CrawlJob, job.crawl_job_id)
    if not crawl_job:
        scheduler.fail_job(db, job, "crawl job no longer exists")
        return
    try:
        await orchestrator.run_chunk(db, crawl_job)
        scheduler.complete_job(db, job)
        if not orchestrator.is_terminal(crawl_job.phase):
            scheduler.enqueue_url_crawl_chunk(db, crawl_job.id, crawl_job.organization_id)
        elif crawl_job.phase == CRAWL_COMPLETED:
            _score_completed_crawl(db, crawl_job)
    except Exception as e:
        db.rollback()
        logger.warning("url_crawl job %s failed: %s", job.id, type(e).__name__)
        scheduler.fail_job(db, job, e)
        if job.status == JOB_FAILED:   # no retries left -> the crawl job itself is done, failed
            cj = db.get(CrawlJob, job.crawl_job_id)
            if cj:
                cj.phase = CRAWL_FAILED
                cj.error = "The crawl could not be completed. Please try again."
                cj.finished_at = datetime.utcnow()
                db.commit()
