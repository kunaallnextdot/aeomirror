"""Health check + stubbed auth and paid endpoints (Steps 5 and 6).

These return clear "not implemented" responses so the API surface is complete
and the frontend can be wired, while making it obvious what the developer must
build. See INTEGRATIONS.md.
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
from app.services.inference.stub import StubFixGenerator, StubPromptSimulator

router = APIRouter(tags=["misc"])


def _db_healthy() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/health")
def health():
    # Liveness. Cache/limiter fail open, so Redis being down does not make the
    # service unhealthy for scanning; it is reported for visibility only.
    return {
        "status": "ok",
        "rubric_version": RUBRIC_VERSION,
        "database": "ok" if _db_healthy() else "unavailable",
        "redis": (
            "ok" if redis_healthy()
            else ("unavailable" if redis_configured() else "not_configured")
        ),
    }


@router.get("/ready")
def ready(response: Response):
    # Readiness gates on the database (required to persist scans). Redis is
    # optional (the scanner fails open), so it is reported but does not block.
    db_ok = _db_healthy()
    if not db_ok:
        response.status_code = 503
    return {
        "status": "ready" if db_ok else "not_ready",
        "database": "ok" if db_ok else "unavailable",
        "redis": (
            "ok" if redis_healthy()
            else ("unavailable" if redis_configured() else "not_configured")
        ),
    }


def require_admin(x_admin_token: str | None = Header(default=None)):
    """Gate the debug endpoints. If ADMIN_TOKEN is set, require a matching header;
    otherwise allow only outside production. Returns 404 to avoid disclosure."""
    token = settings.admin_token
    if token:
        if x_admin_token != token:
            raise HTTPException(status_code=404, detail="Not found.")
        return
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not found.")


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


# ---- Step 5: auth (STUB) ----
@router.post("/v1/auth/signup")
def signup():
    raise HTTPException(status_code=501,
        detail="Auth not implemented. Wire an auth provider (see INTEGRATIONS.md).")


@router.post("/v1/auth/login")
def login():
    raise HTTPException(status_code=501,
        detail="Auth not implemented. Wire an auth provider (see INTEGRATIONS.md).")


# ---- Step 6: paid features (STUB, wired to stub adapters) ----
@router.post("/v1/monitor/prompt")
async def run_prompt(prompt: str, brand_domain: str):
    """PAID. Wired to the stub simulator. Replace with real provider APIs and
    enforce plan quota before calling."""
    sim = StubPromptSimulator()
    results = await sim.run(prompt, brand_domain, ["chatgpt", "claude", "gemini", "perplexity"])
    return {"prompt": prompt, "results": [r.__dict__ for r in results],
            "note": "STUB DATA. Integrate real engine APIs (INTEGRATIONS.md)."}


@router.post("/v1/fixes/generate")
async def generate_fix(asset_type: str, page_url: str):
    """PAID. Wired to the stub generator. Replace with a real LLM call."""
    gen = StubFixGenerator()
    asset = await gen.generate(asset_type, page_url, "")
    return {"asset_type": asset_type, "asset": asset,
            "note": "STUB DATA. Integrate a real LLM (INTEGRATIONS.md)."}
