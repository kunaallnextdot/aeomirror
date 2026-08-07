"""System health (Phase 8): database, Redis, queue, scheduler, email, API latency,
memory and disk. All checks are best-effort and never raise — a failing probe is
reported as a status string, not an exception."""
from __future__ import annotations

import resource
import shutil
import sys

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.cache import redis_configured, redis_healthy
from app.core.observability import metrics
from app.db.models import JOB_FAILED, JOB_PENDING, JOB_RUNNING, ScheduledJob


def _db_status(db: Session) -> str:
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "down"


def _redis_status() -> str:
    if not redis_configured():
        return "not_configured"   # in-memory fallback is in use
    return "ok" if redis_healthy() else "down"


def _memory_mb() -> float | None:
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports kilobytes.
        mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
        return round(mb, 1)
    except Exception:
        return None


def _disk() -> dict | None:
    try:
        total, used, free = shutil.disk_usage("/")
        return {
            "total_gb": round(total / 1e9, 1),
            "used_gb": round(used / 1e9, 1),
            "free_gb": round(free / 1e9, 1),
            "percent_used": round(used / total * 100, 1) if total else None,
        }
    except Exception:
        return None


def _queue(db: Session) -> dict:
    def n(status):
        return db.query(func.count(ScheduledJob.id)).filter(ScheduledJob.status == status).scalar() or 0
    pending, running, failed = n(JOB_PENDING), n(JOB_RUNNING), n(JOB_FAILED)
    status = "ok"
    if failed and failed >= 5:
        status = "degraded"
    if pending >= 100:
        status = "backlogged"
    return {"status": status, "pending": pending, "running": running, "failed": failed}


def system_health(db: Session) -> dict:
    db_status = _db_status(db)
    redis_status = _redis_status()
    queue = _queue(db)
    scheduler_status = "enabled" if settings.scheduler_enabled else "disabled"
    email_status = "configured" if settings.email_enabled else "not_configured"
    snap = metrics.snapshot()

    degraded = (db_status != "ok"
                or redis_status == "down"
                or queue["status"] != "ok")
    overall = "healthy" if not degraded else "degraded"

    return {
        "overall": overall,
        "database": db_status,
        "redis": redis_status,
        "queue": queue,
        "scheduler": scheduler_status,
        "worker": scheduler_status,          # in-process worker follows the scheduler flag
        "email": email_status,
        # Boolean only — never expose the key (or any prefix/length of it).
        "ai_enabled": settings.ai_enabled,
        "api_latency_ms": snap.get("avg_request_latency_ms"),
        "api_requests": snap.get("api_requests"),
        "memory_mb": _memory_mb(),
        "disk": _disk(),
        "metrics": snap,
    }


def overall_status(db: Session) -> str:
    return system_health(db)["overall"]
