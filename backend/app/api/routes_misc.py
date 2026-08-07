"""Operational endpoints: liveness, readiness, and metrics.

- /health, /healthz   — liveness (process is up; never touches optional deps hard).
- /ready,  /readyz    — readiness (DB reachable; Redis reported but non-blocking).
- /metrics            — Prometheus exposition (admin-gated in production).
- /debug/metrics      — admin-only JSON metrics + durable counters.

All placeholder/stub endpoints from earlier phases have been removed for the
production launch (real auth lives under /auth/*).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.cache import redis_configured, redis_healthy
from app.core.observability import metrics
from app.db.session import engine, get_db
from app.scanner.rubric import RUBRIC_VERSION

router = APIRouter(tags=["ops"])


def _db_healthy() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _redis_state() -> str:
    return ("ok" if redis_healthy()
            else ("unavailable" if redis_configured() else "not_configured"))


# ------------------------------- liveness -------------------------------
@router.get("/health")
@router.get("/healthz")
def health():
    """Liveness. Cache/limiter fail open, so Redis being down does not make the
    service unhealthy; it is reported for visibility only."""
    return {
        "status": "ok",
        "rubric_version": RUBRIC_VERSION,
        "database": "ok" if _db_healthy() else "unavailable",
        "redis": _redis_state(),
    }


# ------------------------------- readiness -------------------------------
@router.get("/ready")
@router.get("/readyz")
def ready(response: Response):
    """Readiness gates on the database (required to persist scans). Redis is
    optional (the scanner fails open), so it is reported but does not block."""
    db_ok = _db_healthy()
    if not db_ok:
        response.status_code = 503
    return {"status": "ready" if db_ok else "not_ready",
            "database": "ok" if db_ok else "unavailable", "redis": _redis_state()}


# ------------------------------- admin gate -------------------------------
def require_admin(x_admin_token: str | None = Header(default=None)):
    """Gate the metrics/debug endpoints. If ADMIN_TOKEN is set, require a matching
    header; otherwise allow only outside production. Returns 404 to avoid disclosure."""
    token = settings.admin_token
    if token:
        if x_admin_token != token:
            raise HTTPException(status_code=404, detail="Not found.")
        return
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not found.")


# ------------------------------- metrics -------------------------------
@router.get("/metrics", dependencies=[Depends(require_admin)])
def prometheus_metrics():
    """Prometheus exposition of in-process counters. Admin-gated in production."""
    snap = metrics.snapshot()
    lines = [
        "# HELP aeomirror_api_requests_total Total HTTP requests handled.",
        "# TYPE aeomirror_api_requests_total counter",
        f"aeomirror_api_requests_total {snap['api_requests']}",
        "# HELP aeomirror_scans_total Successful scan responses.",
        "# TYPE aeomirror_scans_total counter",
        f"aeomirror_scans_total {snap['total_scans']}",
        "# HELP aeomirror_cache_hits_total Scan cache hits.",
        "# TYPE aeomirror_cache_hits_total counter",
        f"aeomirror_cache_hits_total {snap['cache_hits']}",
        "# HELP aeomirror_cache_misses_total Scan cache misses.",
        "# TYPE aeomirror_cache_misses_total counter",
        f"aeomirror_cache_misses_total {snap['cache_misses']}",
        "# HELP aeomirror_rate_limited_total Requests rejected by the rate limiter.",
        "# TYPE aeomirror_rate_limited_total counter",
        f"aeomirror_rate_limited_total {snap['rate_limited_429']}",
        "# HELP aeomirror_request_latency_ms Average request latency (ms).",
        "# TYPE aeomirror_request_latency_ms gauge",
        f"aeomirror_request_latency_ms {snap['avg_request_latency_ms']}",
        "# HELP aeomirror_scan_latency_ms Average scan latency (ms).",
        "# TYPE aeomirror_scan_latency_ms gauge",
        f"aeomirror_scan_latency_ms {snap['avg_scan_latency_ms']}",
    ]
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/debug/metrics", dependencies=[Depends(require_admin)])
def debug_metrics(db: Session = Depends(get_db)):
    """Admin-only operational metrics + durable scan counter. No secrets."""
    from app.db.models import RubricVersion, Scan
    total = db.query(func.count(Scan.id)).scalar() or 0
    start_today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today = (db.query(func.count(Scan.id))
             .filter(Scan.created_at >= start_today).scalar() or 0)
    active = (db.query(RubricVersion.version)
              .filter(RubricVersion.is_active.is_(True))
              .order_by(RubricVersion.effective_from.desc()).first())
    return {
        "metrics": metrics.snapshot(),
        "scans": {"today": today, "total": total},
        "active_rubric_version": active[0] if active else None,
    }
