"""Google Analytics 4 integration endpoints (P0: AI referral traffic). All
org-scoped via AuthContext.org_id EXCEPT /callback, which Google redirects the
browser to directly and so can carry no Authorization header — it instead recovers
the initiating org/user from the server-held OAuth `state` row (see
services/integrations/google_analytics.py::consume_oauth_state). Every other
endpoint requires both the existing RBAC permission AND the existing Pro
entitlement gate, matching Content Insights' established pattern
(routes_dashboard.py's create_content_insight).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_context, require_permission
from app.billing import entitlements
from app.config import settings
from app.db.models import AI_SOURCE_LABELS, GA4_STATUS_ACTIVE, Ga4TrafficSnapshot
from app.db.session import get_db
from app.monitoring import scheduler
from app.schemas.integrations import SelectGa4PropertyRequest
from app.services.integrations import google_analytics as ga4

log = logging.getLogger("app.integrations.google_analytics.routes")
router = APIRouter(prefix="/integrations/google", tags=["integrations"])

# Non-sensitive error codes the frontend maps to a human message. Never a raw
# exception message or provider error body.
_SAFE_ERROR_CODES = {
    "access_denied", "invalid_state", "missing_code", "token_exchange_failed",
    "missing_refresh_token", "auth_revoked", "properties_unavailable",
    "property_not_accessible", "credential_unavailable", "quota_exceeded",
    "property_inaccessible", "integration_disabled",
}


def _require_pro(db: Session, ctx: AuthContext) -> None:
    if not entitlements.is_pro(db, ctx.org_id):
        raise HTTPException(status_code=402, detail="Google Analytics traffic is a Pro feature.")


def _require_enabled() -> None:
    if not settings.ga4_integration_enabled:
        raise HTTPException(status_code=503,
            detail="Google Analytics integration is not configured on this server.")


def _owned_connection_or_404(db: Session, ctx: AuthContext):
    connection = ga4.get_connection(db, ctx.org_id)
    if not connection:
        raise HTTPException(status_code=404, detail="No Google Analytics connection found.")
    return connection


# ------------------------------- connect -------------------------------

@router.get("/connect")
def connect(ctx: AuthContext = Depends(require_permission("org:update")),
           db: Session = Depends(get_db)):
    _require_enabled()
    _require_pro(db, ctx)
    state_row, code_challenge = ga4.create_oauth_state(db, org_id=ctx.org_id, user_id=ctx.user.id)
    # Diagnostic only, nothing sensitive: redirect_uri is a public URL Google itself
    # requires to match EXACTLY what's registered in Cloud Console (and is already
    # visible in the browser's address bar during the OAuth flow), and client_id is
    # OAuth2's public identifier, never confidential — only truncated here out of
    # extra caution. The client_secret, state, code_verifier, and any token are
    # NEVER logged, here or anywhere in this module. This exists to let a
    # redirect_uri mismatch (a common cause of a Google-side failure mid-flow,
    # e.g. a trailing-space/newline paste artifact in the Render env var) be
    # confirmed from logs rather than guessed at.
    log.info("ga4 connect: org=%s redirect_uri=%r client_id_prefix=%r",
             ctx.org_id, settings.google_oauth_redirect_uri,
             settings.google_oauth_client_id[:12])
    return {"authorize_url": ga4.build_authorize_url(state_row.state, code_challenge)}


@router.get("/callback")
async def callback(code: str | None = Query(default=None),
                   state: str | None = Query(default=None),
                   error: str | None = Query(default=None),
                   db: Session = Depends(get_db)):
    """PUBLIC — hit by the browser's real navigation after Google's redirect, so it
    cannot require Bearer auth. Ownership comes entirely from the `state` row
    created by /connect; a missing/expired/already-consumed/unrecognized state is
    rejected outright, never trusted as implicit proof of anything."""
    target = f"{settings.app_base_url.rstrip('/')}/app/analytics/ai-traffic"
    if not settings.ga4_integration_enabled:
        return RedirectResponse(url=f"{target}?ga4=error&reason=integration_disabled", status_code=302)

    result = await ga4.handle_oauth_callback(db, code=code, state=state, error=error)
    if result["status"] != "connected":
        reason = result.get("error_code", "unknown")
        if reason not in _SAFE_ERROR_CODES:
            reason = "unknown"   # never reflect an unrecognized value into the redirect
        # Safe: only the classified error_code (e.g. "invalid_state",
        # "token_exchange_failed") — never the raw `code`/`state` query params or
        # Google's response body.
        log.warning("ga4 callback failed: reason=%s", reason)
        return RedirectResponse(url=f"{target}?ga4=error&reason={reason}", status_code=302)
    return RedirectResponse(url=f"{target}?ga4=connected", status_code=302)


# ------------------------------- status -------------------------------

@router.get("/status")
def status(ctx: AuthContext = Depends(require_permission("report:view")),
          db: Session = Depends(get_db)):
    # Checked BEFORE the Pro gate, deliberately: if the platform operator hasn't
    # configured Google OAuth at all, upgrading plans wouldn't fix anything, so a
    # non-Pro org must see the honest "not configured" reason too, not a misleading
    # "upgrade required" that promises a fix that doesn't exist. This is the only
    # safe-to-disclose server-configuration fact exposed here — no secret values,
    # just whether the feature is set up at all (every authenticated user may see it).
    if not settings.ga4_integration_enabled:
        return {"status": "not_configured"}
    if not entitlements.is_pro(db, ctx.org_id):
        return {"status": "not_available", "reason": "upgrade_required"}
    connection = ga4.get_connection(db, ctx.org_id)
    if not connection:
        return {"status": "not_connected"}
    return {
        "status": connection.status,
        "ga4_property_id": connection.ga4_property_id,
        "ga4_property_name": connection.ga4_property_name,
        "connected_at": connection.connected_at,
        "last_synced_at": connection.last_synced_at,
        "last_sync_status": connection.last_sync_status,
        "last_sync_error_code": connection.last_sync_error_code,
    }


# ------------------------------- properties -------------------------------

@router.get("/properties")
async def properties(ctx: AuthContext = Depends(require_permission("report:view")),
                     db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    access_token = await ga4.get_valid_access_token(db, connection)
    if not access_token:
        raise HTTPException(status_code=502,
            detail="Could not reach your Google Analytics account. Try reconnecting.")
    props = await ga4.list_ga4_properties(access_token)
    if props is None:
        raise HTTPException(status_code=502,
            detail="Could not load Google Analytics properties right now.")
    return {"properties": props}


@router.post("/select-property")
async def select_property(body: SelectGa4PropertyRequest,
                          ctx: AuthContext = Depends(require_permission("org:update")),
                          db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    result = await ga4.select_property(db, connection, body.property_id)
    if result["status"] != "ok":
        code = result.get("error_code")
        if code == "property_not_accessible":
            raise HTTPException(status_code=403, detail="That property isn't accessible to the connected account.")
        if code == "auth_revoked":
            raise HTTPException(status_code=401, detail="Your Google Analytics connection needs to be reconnected.")
        raise HTTPException(status_code=502, detail="Could not verify that property right now.")
    return {"status": "ok", "ga4_property_id": connection.ga4_property_id,
            "ga4_property_name": connection.ga4_property_name}


# ------------------------------- sync -------------------------------

@router.post("/sync")
def trigger_sync(ctx: AuthContext = Depends(require_permission("org:update")),
                 db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    if connection.status != GA4_STATUS_ACTIVE or not connection.ga4_property_id:
        raise HTTPException(status_code=422,
            detail="Select a Google Analytics property before syncing.")
    if scheduler.has_active_ga4_sync_job(db, connection.id):
        return {"status": "already_running"}
    job = scheduler.enqueue_manual_ga4_sync(db, connection)
    return {"status": "queued", "job_id": job.id}


# ------------------------------- traffic -------------------------------

_MAX_RANGE_DAYS = 90


@router.get("/traffic")
def traffic(start_date: str | None = Query(default=None),
           end_date: str | None = Query(default=None),
           ctx: AuthContext = Depends(require_permission("report:view")),
           db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)

    end = date.today()
    start = end - timedelta(days=30)
    if end_date:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="end_date must be YYYY-MM-DD.")
    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="start_date must be YYYY-MM-DD.")
    if start > end:
        raise HTTPException(status_code=422, detail="start_date must be before end_date.")
    if (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(status_code=422,
            detail=f"Date range cannot exceed {_MAX_RANGE_DAYS} days.")

    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    rows = (db.query(Ga4TrafficSnapshot)
            .filter(Ga4TrafficSnapshot.organization_id == ctx.org_id,
                    Ga4TrafficSnapshot.connection_id == connection.id,
                    Ga4TrafficSnapshot.date >= start_key,
                    Ga4TrafficSnapshot.date <= end_key)
            .all())

    by_source: dict[str, int] = {label: 0 for label in AI_SOURCE_LABELS}
    daily: dict[str, dict[str, int]] = {}
    for r in rows:
        by_source[r.ai_source_label] = by_source.get(r.ai_source_label, 0) + r.sessions
        day = daily.setdefault(r.date, {label: 0 for label in AI_SOURCE_LABELS})
        day[r.ai_source_label] = day.get(r.ai_source_label, 0) + r.sessions

    return {
        "synced": connection.last_synced_at is not None,
        "last_synced_at": connection.last_synced_at,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "by_source": by_source,
        "daily": [{"date": d, "by_source": v} for d, v in sorted(daily.items())],
    }


# ------------------------------- disconnect -------------------------------

@router.delete("")
async def disconnect(ctx: AuthContext = Depends(require_permission("org:update")),
                     db: Session = Depends(get_db)):
    connection = _owned_connection_or_404(db, ctx)
    await ga4.disconnect(db, connection)
    return {"status": "disconnected"}
