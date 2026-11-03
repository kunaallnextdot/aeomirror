"""Google Search Console (read-only) endpoints. Org-scoped and Pro-gated exactly like
the GA4 endpoints (routes_integrations.py), whose registered /callback also completes
this grant (it routes on the OAuth state's provider). Requests only the
webmasters.readonly scope; never touches the GA4 connection.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.billing import entitlements
from app.config import settings
from app.db.models import GA4_STATUS_ACTIVE, Monitor
from app.db.session import get_db
from app.monitoring import scheduler
from app.schemas.integrations import SelectSearchConsoleSiteRequest
from app.services.integrations import google_analytics as ga4
from app.services.integrations import search_console as gsc

log = logging.getLogger("app.integrations.search_console.routes")
router = APIRouter(prefix="/integrations/search-console", tags=["integrations"])


def _require_pro(db: Session, ctx: AuthContext) -> None:
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="Search Console data is a Pro feature.")


def _require_enabled() -> None:
    if not settings.ga4_integration_enabled:
        raise HTTPException(status_code=503, detail="Google integrations are not configured on this server.")


def _owned_connection_or_404(db: Session, ctx: AuthContext):
    connection = gsc.get_connection(db, ctx.org_id)
    if not connection:
        raise HTTPException(status_code=404, detail="No Search Console connection found.")
    return connection


@router.get("/status")
def status(ctx: AuthContext = Depends(require_permission("report:view")),
           db: Session = Depends(get_db)):
    if not settings.ga4_integration_enabled:
        return {"status": "not_configured"}
    if not entitlements.is_pro(db, ctx.org_id):
        return {"status": "not_available", "reason": "upgrade_required"}
    c = gsc.get_connection(db, ctx.org_id)
    if not c:
        return {"status": "not_connected"}
    monitors = db.query(Monitor).filter(Monitor.organization_id == ctx.org_id).all()
    return {
        "status": c.status, "site_url": c.site_url, "permission_level": c.permission_level,
        "connected_at": c.connected_at, "last_synced_at": c.last_synced_at,
        "last_sync_status": c.last_sync_status, "last_sync_error_code": c.last_sync_error_code,
        "next_sync_at": c.next_sync_at, "sync_failures": c.sync_failures or 0,
        "window": {"start": c.window_start, "end": c.window_end} if c.window_start else None,
        "rows_fetched": c.rows_fetched, "truncated": c.truncated,
        # Which of the org's websites this property covers (deterministic URL match).
        "covered_monitor_ids": [m.id for m in monitors if gsc.site_covers(c.site_url, m.url)],
    }


@router.get("/connect")
def connect(ctx: AuthContext = Depends(require_permission("org:update")),
            db: Session = Depends(get_db)):
    _require_enabled()
    _require_pro(db, ctx)
    state_row, challenge = ga4.create_oauth_state(db, org_id=ctx.org_id, user_id=ctx.user.id,
                                                  provider=gsc.OAUTH_PROVIDER)
    return {"authorize_url": gsc.build_authorize_url(state_row.state, challenge)}


@router.get("/sites")
async def sites(ctx: AuthContext = Depends(require_permission("report:view")),
                db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    from app.services.integrations.google_errors import GoogleTransientError
    try:
        token = await gsc.get_valid_access_token(db, connection)
    except GoogleTransientError:
        raise HTTPException(status_code=503,
                            detail="Google is temporarily unavailable. Please try again in a few minutes.")
    if not token:
        raise HTTPException(status_code=502, detail="Could not reach your Search Console account. Try reconnecting.")
    found = await gsc.list_sites(token)
    if found is None:
        raise HTTPException(status_code=502, detail="Could not load Search Console properties right now.")
    return {"sites": found}


@router.post("/select-site")
async def select_site(body: SelectSearchConsoleSiteRequest,
                      ctx: AuthContext = Depends(require_permission("org:update")),
                      db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    result = await gsc.select_site(db, connection, body.site_url)
    if result["status"] != "ok":
        code = result.get("error_code")
        if code == "property_not_accessible":
            raise HTTPException(status_code=403, detail="That property isn't verified for the connected account.")
        if code == "auth_revoked":
            raise HTTPException(status_code=401, detail="Your Search Console connection needs to be reconnected.")
        raise HTTPException(status_code=502, detail="Could not verify that property right now.")
    scheduler.enqueue_manual_gsc_sync(db, connection)
    return {"status": "ok", "site_url": connection.site_url}


@router.post("/sync")
def trigger_sync(ctx: AuthContext = Depends(require_permission("org:update")),
                 db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    if connection.status != GA4_STATUS_ACTIVE or not connection.site_url:
        raise HTTPException(status_code=422, detail="Select a Search Console property before syncing.")
    if scheduler.has_active_ga4_sync_job(db, connection.id):
        return {"status": "already_running"}
    job = scheduler.enqueue_manual_gsc_sync(db, connection)
    return {"status": "queued", "job_id": job.id}


@router.delete("")
async def disconnect(ctx: AuthContext = Depends(require_permission("org:update")),
                     db: Session = Depends(get_db)):
    connection = _owned_connection_or_404(db, ctx)
    await gsc.disconnect(db, connection)
    return {"status": "disconnected"}
