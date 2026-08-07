"""Monitor scan runner (Phase 7): executes one scan for a monitor and records the
outcome — history snapshot, change detection, alerts, notifications, and the
monitor's rolling state. Uses the shared scanning primitive (routes_scan.run_scan)
so scanning logic is not duplicated; the scheduler decides *when*, this decides
*what happens with the result*.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.ssrf import validate_url
from app.db.models import (
    JOB_FAILED, JOB_KIND_BULK_SCAN, SCAN_FAILED, Monitor, MonitorHistory, Scan,
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
                             org_id=monitor.organization_id, user_id=monitor.user_id)

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
        notify_critical_alerts(db, monitor, alerts)
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

    monitor = db.get(Monitor, job.monitor_id)
    if not monitor:
        scheduler.fail_job(db, job, "monitor no longer exists")
        return
    try:
        await run_scan_for_monitor(db, monitor)
        scheduler.complete_job(db, job)
    except Exception as e:
        db.rollback()
        logger.warning("monitor job %s failed: %s", job.id, type(e).__name__)
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
    try:
        await execute_bulk_scan(db, scan)
        scheduler.complete_job(db, job)
    except Exception as e:
        db.rollback()
        logger.warning("bulk scan job %s failed: %s", job.id, type(e).__name__)
        scheduler.fail_job(db, job, e)
        if job.status == JOB_FAILED:        # no retries left -> the scan is done, failed
            _mark_scan_failed(db, job.scan_id, e)
