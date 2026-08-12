"""Monitoring endpoints (Phase 7). All authenticated + org-scoped.

Monitors, their history, alerts, and manual runs. Scheduled runs happen in the
background worker (independent of these HTTP requests); a manual run is executed
inline via the same runner so the caller gets an immediate result.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import AuthContext, get_context, require_permission
from app.api.routes_scan import _normalize
from app.config import settings
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import (
    ALERT_ACK, ALERT_OPEN, FREQUENCIES, MONITOR_ACTIVE, MONITOR_PAUSED,
    Alert, Monitor, MonitorHistory, Scan, ScheduledJob,
)
from app.services.crawler_access import has_critical_block
from app.db.session import get_db
from app.monitoring import runner, scheduler
from app.schemas.monitor import CreateMonitorRequest, UpdateMonitorRequest

router = APIRouter(tags=["monitoring"])


# ------------------------------- helpers -------------------------------
def _owned_monitor(db: Session, ctx: AuthContext, monitor_id: str) -> Monitor:
    m = db.get(Monitor, monitor_id)
    if not m or m.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Monitor not found.")
    return m


def _open_alert_count(db: Session, monitor_id: str) -> int:
    return (db.query(Alert)
            .filter(Alert.monitor_id == monitor_id, Alert.status == ALERT_OPEN)
            .count())


def _trend(db: Session, monitor_id: str) -> str:
    """up | down | flat | none — from the latest two history scores."""
    rows = (db.query(MonitorHistory)
            .filter(MonitorHistory.monitor_id == monitor_id)
            .order_by(MonitorHistory.created_at.desc())
            .limit(2).all())
    if len(rows) < 2 or rows[0].overall_score is None or rows[1].overall_score is None:
        return "none"
    d = rows[0].overall_score - rows[1].overall_score
    return "up" if d > 0 else "down" if d < 0 else "flat"


def _latest_crawler_access(db: Session, m: Monitor) -> dict | None:
    """The AI-crawler access result from this monitor's latest scan (null-safe)."""
    if not m.latest_scan_id:
        return None
    scan = db.get(Scan, m.latest_scan_id)
    return (scan.result or {}).get("crawler_access") if scan else None


def _monitor_out(db: Session, m: Monitor) -> dict:
    return {
        "id": m.id, "url": m.url, "domain": m.normalized_url, "name": m.name,
        "frequency": m.frequency, "status": m.status,
        "created_at": m.created_at, "last_scan_at": m.last_scan_at,
        "next_scan_at": m.next_scan_at, "latest_score": m.latest_score,
        "latest_scan_id": m.latest_scan_id,
        "trend": _trend(db, m.id),
        "open_alert_count": _open_alert_count(db, m.id),
        # Badge signal for the Monitoring list — true if the latest scan found a critical
        # AI crawler blocked (null-safe for monitors with no scans yet).
        "critical_crawler_blocked": has_critical_block(_latest_crawler_access(db, m)),
        "history_count": db.query(MonitorHistory).filter(MonitorHistory.monitor_id == m.id).count(),
    }


def _alert_out(a: Alert) -> dict:
    return {
        "id": a.id, "monitor_id": a.monitor_id, "scan_id": a.scan_id,
        "type": a.type, "severity": a.severity, "title": a.title, "message": a.message,
        "detail": a.detail, "status": a.status,
        "created_at": a.created_at, "acknowledged_at": a.acknowledged_at,
    }


def _history_out(h: MonitorHistory) -> dict:
    return {
        "id": h.id, "scan_id": h.scan_id, "overall_score": h.overall_score,
        "status": h.status, "issue_count": h.issue_count, "scores": h.scores,
        "changes": h.changes, "created_at": h.created_at,
    }


def _build_trends(rows: list[MonitorHistory]) -> dict:
    """rows oldest -> newest."""
    score_series = [{"t": r.created_at, "score": r.overall_score} for r in rows]
    issue_series = [{"t": r.created_at, "issues": r.issue_count} for r in rows]
    category_series: dict[str, list] = {}
    improvements, regressions = [], []
    for r in rows:
        for sid, score in (r.scores or {}).items():
            category_series.setdefault(sid, []).append({"t": r.created_at, "score": score})
        ch = r.changes or {}
        for imp in ch.get("improvements", []):
            improvements.append({"t": r.created_at, "id": imp.get("id"),
                                 "label": imp.get("label"), "delta": imp.get("delta")})
        for reg in ch.get("regressions", []):
            regressions.append({"t": r.created_at, "id": reg.get("id"),
                                "label": reg.get("label"), "delta": reg.get("delta")})
    return {
        "score_series": score_series, "issue_series": issue_series,
        "category_series": category_series,
        "improvements": improvements, "regressions": regressions,
    }


# ------------------------------- monitors -------------------------------
@router.post("/monitors", status_code=201)
def create_monitor(body: CreateMonitorRequest,
                   ctx: AuthContext = Depends(require_permission("scan:run")),
                   db: Session = Depends(get_db)):
    from app.admin import flags, settings_store
    from app.billing import entitlements
    if not flags.is_enabled(db, "monitoring"):
        raise HTTPException(status_code=403, detail="Monitoring is currently disabled by the administrator.")
    if settings_store.is_maintenance(db) and not ctx.user.is_platform_admin:
        raise HTTPException(status_code=503, detail=settings_store.get(db, "maintenance_message"))
    # Billing gate: monitors are capped on BOTH plans now (Free 1, Pro 10). 402
    # (payment required) mirrors the scan-quota gate — the request is valid, the org
    # just needs to upgrade to raise the cap. When billing enforcement is off
    # (tests/dev) the quota is unlimited, so this never blocks.
    mq = entitlements.monitor_quota(db, ctx.org_id)
    if not mq["unlimited"] and mq["remaining"] <= 0:
        if entitlements.current_plan(db, ctx.org_id) == "pro":
            detail = f"You've reached your Pro plan monitor limit ({mq['limit']})."
        else:
            detail = (f"Free plan includes {mq['limit']} monitor. "
                      f"Upgrade to Pro for {settings.pro_monitor_limit} monitors.")
        raise HTTPException(status_code=402, detail=detail)
    if body.frequency not in FREQUENCIES:
        raise HTTPException(status_code=422, detail="Frequency must be daily, weekly, monthly, or manual.")
    try:
        safe_url = validate_url(body.url)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=422, detail=str(e))

    normalized = _normalize(safe_url)
    now = datetime.utcnow()
    # A new active recurring monitor is due immediately (first scan runs promptly).
    next_scan = now if body.frequency != "manual" else None
    m = Monitor(
        organization_id=ctx.org_id, user_id=ctx.user.id, name=(body.name or None),
        url=safe_url, normalized_url=normalized, frequency=body.frequency,
        status=MONITOR_ACTIVE, next_scan_at=next_scan,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _monitor_out(db, m)


@router.get("/monitors")
def list_monitors(ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    rows = (db.query(Monitor)
            .filter(Monitor.organization_id == ctx.org_id)
            .order_by(Monitor.created_at.desc())
            .all())
    monitors = [_monitor_out(db, m) for m in rows]
    active = [m for m in monitors if m["status"] == MONITOR_ACTIVE]
    paused = [m for m in monitors if m["status"] == MONITOR_PAUSED]
    return {
        "monitors": monitors,
        "counts": {
            "total": len(monitors), "active": len(active), "paused": len(paused),
            "open_alerts": sum(m["open_alert_count"] for m in monitors),
        },
    }


@router.get("/monitors/{monitor_id}")
def get_monitor(monitor_id: str,
                ctx: AuthContext = Depends(require_permission("report:view")),
                db: Session = Depends(get_db)):
    m = _owned_monitor(db, ctx, monitor_id)
    hist_desc = (db.query(MonitorHistory)
                 .filter(MonitorHistory.monitor_id == m.id)
                 .order_by(MonitorHistory.created_at.desc())
                 .limit(100).all())
    alerts = (db.query(Alert)
              .filter(Alert.monitor_id == m.id)
              .order_by(Alert.created_at.desc())
              .limit(100).all())
    trends = _build_trends(list(reversed(hist_desc)))
    return {
        "monitor": _monitor_out(db, m),
        "history": [_history_out(h) for h in hist_desc],
        "latest_changes": hist_desc[0].changes if hist_desc else None,
        "alerts": [_alert_out(a) for a in alerts],
        "trends": trends,
        "crawler_access": _latest_crawler_access(db, m),   # AI Crawler Access panel
    }


@router.patch("/monitors/{monitor_id}")
def update_monitor(monitor_id: str, body: UpdateMonitorRequest,
                   ctx: AuthContext = Depends(require_permission("scan:run")),
                   db: Session = Depends(get_db)):
    m = _owned_monitor(db, ctx, monitor_id)
    if body.name is not None:
        m.name = body.name or None
    if body.frequency is not None:
        if body.frequency not in FREQUENCIES:
            raise HTTPException(status_code=422, detail="Invalid frequency.")
        m.frequency = body.frequency
        # Recompute the next run relative to the last scan (or now).
        base = m.last_scan_at or datetime.utcnow()
        m.next_scan_at = scheduler.compute_next_scan(m.frequency, base) if m.status == MONITOR_ACTIVE else None
    if body.status is not None:
        if body.status not in (MONITOR_ACTIVE, MONITOR_PAUSED):
            raise HTTPException(status_code=422, detail="Status must be active or paused.")
        m.status = body.status
        if m.status == MONITOR_PAUSED:
            m.next_scan_at = None
        elif m.next_scan_at is None and m.frequency != "manual":
            m.next_scan_at = datetime.utcnow()   # resume -> due soon
    db.commit()
    db.refresh(m)
    return _monitor_out(db, m)


@router.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: str,
                   ctx: AuthContext = Depends(require_permission("scan:delete")),
                   db: Session = Depends(get_db)):
    m = _owned_monitor(db, ctx, monitor_id)
    # Clean up dependent rows (no FK cascade in this schema).
    db.query(ScheduledJob).filter(ScheduledJob.monitor_id == m.id).delete(synchronize_session=False)
    db.query(MonitorHistory).filter(MonitorHistory.monitor_id == m.id).delete(synchronize_session=False)
    db.query(Alert).filter(Alert.monitor_id == m.id).delete(synchronize_session=False)
    db.delete(m)
    db.commit()
    return {"ok": True, "id": monitor_id}


@router.post("/monitors/{monitor_id}/run")
async def run_monitor(monitor_id: str,
                      ctx: AuthContext = Depends(require_permission("scan:run")),
                      db: Session = Depends(get_db)):
    """Manual scan now. Enqueues a manual job and runs it inline for an immediate
    result (scheduled runs go through the background worker instead)."""
    m = _owned_monitor(db, ctx, monitor_id)
    job = scheduler.enqueue_manual(db, m)
    # Only run inline if we own a fresh manual job (dedupe returns an active one).
    await runner.process_job(db, job)
    db.refresh(m)
    latest = (db.query(MonitorHistory)
              .filter(MonitorHistory.monitor_id == m.id)
              .order_by(MonitorHistory.created_at.desc()).first())
    return {"monitor": _monitor_out(db, m),
            "latest": _history_out(latest) if latest else None,
            "job_status": job.status}


# ------------------------------- alerts -------------------------------
@router.get("/alerts")
def list_alerts(ctx: AuthContext = Depends(require_permission("report:view")),
                db: Session = Depends(get_db),
                status: str | None = Query(default=None),
                monitor_id: str | None = Query(default=None),
                limit: int = Query(default=100, le=500)):
    q = db.query(Alert).filter(Alert.organization_id == ctx.org_id)
    if status in (ALERT_OPEN, ALERT_ACK):
        q = q.filter(Alert.status == status)
    if monitor_id:
        q = q.filter(Alert.monitor_id == monitor_id)
    rows = q.order_by(Alert.created_at.desc()).limit(limit).all()
    open_count = (db.query(Alert)
                  .filter(Alert.organization_id == ctx.org_id, Alert.status == ALERT_OPEN)
                  .count())
    return {"alerts": [_alert_out(a) for a in rows], "open_count": open_count}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str,
                      ctx: AuthContext = Depends(require_permission("scan:run")),
                      db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a or a.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Alert not found.")
    a.status = ALERT_ACK
    a.acknowledged_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": alert_id}


# ------------------------------- history / trends -------------------------------
@router.get("/history/{monitor_id}")
def get_history(monitor_id: str,
                ctx: AuthContext = Depends(require_permission("report:view")),
                db: Session = Depends(get_db),
                limit: int = Query(default=200, le=1000)):
    m = _owned_monitor(db, ctx, monitor_id)
    rows_desc = (db.query(MonitorHistory)
                 .filter(MonitorHistory.monitor_id == m.id)
                 .order_by(MonitorHistory.created_at.desc())
                 .limit(limit).all())
    rows_asc = list(reversed(rows_desc))
    return {
        "monitor_id": m.id,
        "history": [_history_out(h) for h in rows_desc],
        "trends": _build_trends(rows_asc),
    }
