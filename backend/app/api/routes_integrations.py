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
from app.db.models import (
    AI_SOURCE_LABELS, GA4_STATUS_ACTIVE, Ga4DailyOverview, Ga4LandingPageSnapshot,
    Ga4TrafficSnapshot, Monitor, Scan,
)
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


def _parse_date_range(start_date: str | None, end_date: str | None,
                       *, default_days: int = 30) -> tuple:
    """Shared by every date-ranged GA4 endpoint below. Returns (start, end) as
    date objects, already validated. Raises HTTPException(422) on anything
    malformed — never silently clamps to a "close enough" range."""
    end = date.today()
    start = end - timedelta(days=default_days)
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
    return start, end


def _preceding_period(start: date, end: date) -> tuple:
    """The immediately-preceding period of equal length, for a previous-period
    comparison — e.g. [Jan 8, Jan 14] -> [Jan 1, Jan 7]."""
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return prev_start, prev_end


@router.get("/traffic")
def traffic(start_date: str | None = Query(default=None),
           end_date: str | None = Query(default=None),
           ctx: AuthContext = Depends(require_permission("report:view")),
           db: Session = Depends(get_db)):
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    start, end = _parse_date_range(start_date, end_date)

    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    rows = (db.query(Ga4TrafficSnapshot)
            .filter(Ga4TrafficSnapshot.organization_id == ctx.org_id,
                    Ga4TrafficSnapshot.connection_id == connection.id,
                    Ga4TrafficSnapshot.date >= start_key,
                    Ga4TrafficSnapshot.date <= end_key)
            .all())

    by_source: dict[str, int] = {label: 0 for label in AI_SOURCE_LABELS}
    daily: dict[str, dict[str, int]] = {}
    # Richer per-source metrics, additive to the response below the original
    # by_source field (kept byte-for-byte as before — existing tests/clients rely
    # on its exact shape). All additive counts, safe to sum (see
    # Ga4TrafficSnapshot's docstring).
    detail: dict[str, dict] = {
        label: {"sessions": 0, "new_users": 0, "engaged_sessions": 0, "views": 0, "key_events": 0}
        for label in AI_SOURCE_LABELS
    }
    for r in rows:
        by_source[r.ai_source_label] = by_source.get(r.ai_source_label, 0) + r.sessions
        day = daily.setdefault(r.date, {label: 0 for label in AI_SOURCE_LABELS})
        day[r.ai_source_label] = day.get(r.ai_source_label, 0) + r.sessions
        d = detail[r.ai_source_label]
        d["sessions"] += r.sessions
        d["new_users"] += r.new_users
        d["engaged_sessions"] += r.engaged_sessions
        d["views"] += r.views
        d["key_events"] += r.key_events
    for d in detail.values():
        d["engagement_rate"] = (d["engaged_sessions"] / d["sessions"]) if d["sessions"] else None
        d["conversion_rate"] = (d["key_events"] / d["sessions"]) if d["sessions"] else None

    return {
        "synced": connection.last_synced_at is not None,
        "last_synced_at": connection.last_synced_at,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "by_source": by_source,
        "by_source_detail": detail,
        "daily": [{"date": d, "by_source": v} for d, v in sorted(daily.items())],
    }


# ------------------------------- overview -------------------------------

@router.get("/overview")
async def overview(start_date: str | None = Query(default=None),
                   end_date: str | None = Query(default=None),
                   ctx: AuthContext = Depends(require_permission("report:view")),
                   db: Session = Depends(get_db)):
    """The Executive Overview's KPI cards. The ONE endpoint that queries GA4 live
    (one request, GA4's own two-dateRanges feature) rather than reading a stored
    snapshot — totalUsers/newUsers for an arbitrary range cannot be correctly
    reconstructed from summed per-day rows (see Ga4TrafficSnapshot's docstring), so
    this asks GA4 to compute both the current and immediately-preceding period
    itself. A transient failure here degrades honestly (502), never to a fabricated
    zero."""
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    start, end = _parse_date_range(start_date, end_date)
    prev_start, prev_end = _preceding_period(start, end)

    base = {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_date_range": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "synced": connection.last_synced_at is not None,
        "last_synced_at": connection.last_synced_at,
        "key_events_configured": connection.key_events_configured,
    }
    if connection.status != GA4_STATUS_ACTIVE or not connection.ga4_property_id:
        return {**base, "current": None, "previous": None, "unavailable_reason": "not_active"}

    access_token = await ga4.get_valid_access_token(db, connection)
    if not access_token:
        raise HTTPException(status_code=401,
            detail="Your Google Analytics connection needs to be reconnected.")
    try:
        result = await ga4.fetch_overview_with_comparison(
            access_token, connection.ga4_property_id,
            current_start=start.strftime("%Y-%m-%d"), current_end=end.strftime("%Y-%m-%d"),
            previous_start=prev_start.strftime("%Y-%m-%d"), previous_end=prev_end.strftime("%Y-%m-%d"),
        )
    except ga4.GoogleApiError as exc:
        status_code = 429 if exc.status_code == 429 else 502
        raise HTTPException(status_code=status_code,
            detail="Could not load your Google Analytics overview right now.")
    if result is None:
        raise HTTPException(status_code=502,
            detail="Could not load your Google Analytics overview right now.")

    return {**base, "current": _enrich_overview_metrics(result["current"]),
            "previous": _enrich_overview_metrics(result["previous"])}


def _enrich_overview_metrics(m: dict) -> dict:
    """Adds the derived (non-raw-GA4) fields: returning_users, engagement_rate,
    avg_engagement_time_sec, conversion_rate — each explicitly documented as
    derived so a reader never mistakes them for a metric GA4 itself returns."""
    sessions = m["sessions"]
    return {
        **m,
        "returning_users": max(m["total_users"] - m["new_users"], 0),
        "engagement_rate": (m["engaged_sessions"] / sessions) if sessions else None,
        "avg_engagement_time_sec": (m["engagement_duration_sec"] / sessions) if sessions else None,
        "conversion_rate": (m["key_events"] / sessions) if sessions else None,
    }


# ------------------------------- trends -------------------------------

@router.get("/trends")
def trends(start_date: str | None = Query(default=None),
          end_date: str | None = Query(default=None),
          ctx: AuthContext = Depends(require_permission("report:view")),
          db: Session = Depends(get_db)):
    """Day-by-day series for the Traffic Trends chart, read entirely from stored
    snapshots (no live GA4 call — see /overview for why totalUsers/newUsers range
    TOTALS need a live query, which does not apply here: each day's own totalUsers
    is read as GA4 reported it for that single day, never summed with another
    day — see Ga4DailyOverview's docstring)."""
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    start, end = _parse_date_range(start_date, end_date, default_days=28)
    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    overview_rows = (db.query(Ga4DailyOverview)
                     .filter(Ga4DailyOverview.connection_id == connection.id,
                             Ga4DailyOverview.date >= start_key,
                             Ga4DailyOverview.date <= end_key)
                     .all())
    traffic_rows = (db.query(Ga4TrafficSnapshot)
                     .filter(Ga4TrafficSnapshot.connection_id == connection.id,
                             Ga4TrafficSnapshot.date >= start_key,
                             Ga4TrafficSnapshot.date <= end_key)
                     .all())

    ai_by_day: dict[str, dict[str, int]] = {}
    for r in traffic_rows:
        day = ai_by_day.setdefault(r.date, {label: 0 for label in AI_SOURCE_LABELS})
        day[r.ai_source_label] += r.sessions

    daily = []
    for r in sorted(overview_rows, key=lambda x: x.date):
        daily.append({
            "date": r.date,
            "total_users": r.total_users, "new_users": r.new_users,
            "returning_users": max(r.total_users - r.new_users, 0),
            "sessions": r.sessions, "engaged_sessions": r.engaged_sessions,
            "engagement_rate": (r.engaged_sessions / r.sessions) if r.sessions else None,
            "views": r.views, "key_events": r.key_events,
            "ai_sessions": ai_by_day.get(r.date, {label: 0 for label in AI_SOURCE_LABELS}),
        })

    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "synced": connection.last_synced_at is not None,
        "last_synced_at": connection.last_synced_at,
        "daily": daily,
    }


# ------------------------------- channels -------------------------------

@router.get("/channels")
def channels(start_date: str | None = Query(default=None),
            end_date: str | None = Query(default=None),
            q: str | None = Query(default=None),
            sort: str = Query(default="-sessions"),
            page: int = Query(default=1, ge=1),
            page_size: int = Query(default=25, ge=1, le=200),
            ctx: AuthContext = Depends(require_permission("report:view")),
            db: Session = Depends(get_db)):
    """Acquisition-channel table. Deliberately omits a "Users" column — totalUsers
    is not additive across channels or days (see Ga4TrafficSnapshot's docstring);
    showing it here would require a live per-row GA4 query, which does not scale to
    a table with many rows. Every column shown here IS a safe sum."""
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    start, end = _parse_date_range(start_date, end_date)
    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    rows = (db.query(Ga4TrafficSnapshot)
            .filter(Ga4TrafficSnapshot.connection_id == connection.id,
                    Ga4TrafficSnapshot.date >= start_key,
                    Ga4TrafficSnapshot.date <= end_key)
            .all())

    grouped: dict[tuple, dict] = {}
    for r in rows:
        key = (r.channel_grouping, r.source, r.medium)
        g = grouped.setdefault(key, {
            "channel": r.channel_grouping, "source": r.source, "medium": r.medium,
            "ai_source_label": r.ai_source_label,
            "sessions": 0, "new_users": 0, "engaged_sessions": 0, "views": 0, "key_events": 0,
        })
        g["sessions"] += r.sessions
        g["new_users"] += r.new_users
        g["engaged_sessions"] += r.engaged_sessions
        g["views"] += r.views
        g["key_events"] += r.key_events

    items = list(grouped.values())
    for g in items:
        g["engagement_rate"] = (g["engaged_sessions"] / g["sessions"]) if g["sessions"] else None
        g["conversion_rate"] = (g["key_events"] / g["sessions"]) if g["sessions"] else None
    if q:
        needle = q.lower()
        items = [g for g in items if needle in g["channel"].lower()
                 or needle in g["source"].lower() or needle in g["medium"].lower()]

    items = _apply_sort(items, sort, allowed={"sessions", "new_users", "engaged_sessions",
                                               "views", "key_events", "engagement_rate",
                                               "conversion_rate", "channel"})
    total = len(items)
    paged = items[(page - 1) * page_size: page * page_size]

    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "total": total, "page": page, "page_size": page_size,
        "channels": paged,
    }


def _apply_sort(items: list[dict], sort: str, *, allowed: set) -> list[dict]:
    desc = sort.startswith("-")
    field = sort[1:] if desc else sort
    if field not in allowed:
        field, desc = "sessions", True
    return sorted(items, key=lambda x: (x.get(field) is None, x.get(field)), reverse=desc)


# ------------------------------- landing pages -------------------------------

@router.get("/landing-pages")
def landing_pages(start_date: str | None = Query(default=None),
                  end_date: str | None = Query(default=None),
                  q: str | None = Query(default=None),
                  sort: str = Query(default="-sessions"),
                  page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=25, ge=1, le=200),
                  ctx: AuthContext = Depends(require_permission("report:view")),
                  db: Session = Depends(get_db)):
    """Landing-page performance table, plus a previous-period delta (valid here —
    every metric is additive, see Ga4LandingPageSnapshot's docstring) and a
    best-effort cross-reference to the org's own AEOMirror scan/audit record for
    that page, kept as a SEPARATE field (never merged into the analytics numbers)."""
    _require_pro(db, ctx)
    connection = _owned_connection_or_404(db, ctx)
    start, end = _parse_date_range(start_date, end_date)
    prev_start, prev_end = _preceding_period(start, end)
    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    prev_start_key, prev_end_key = prev_start.strftime("%Y%m%d"), prev_end.strftime("%Y%m%d")

    def _grouped(lo: str, hi: str) -> dict[str, dict]:
        rows = (db.query(Ga4LandingPageSnapshot)
                .filter(Ga4LandingPageSnapshot.connection_id == connection.id,
                        Ga4LandingPageSnapshot.date >= lo,
                        Ga4LandingPageSnapshot.date <= hi)
                .all())
        out: dict[str, dict] = {}
        for r in rows:
            g = out.setdefault(r.landing_page, {
                "landing_page": r.landing_page, "sessions": 0, "new_users": 0,
                "engaged_sessions": 0, "views": 0, "key_events": 0,
            })
            g["sessions"] += r.sessions
            g["new_users"] += r.new_users
            g["engaged_sessions"] += r.engaged_sessions
            g["views"] += r.views
            g["key_events"] += r.key_events
        return out

    current = _grouped(start_key, end_key)
    previous = _grouped(prev_start_key, prev_end_key)

    items = []
    for page_path, g in current.items():
        g["engagement_rate"] = (g["engaged_sessions"] / g["sessions"]) if g["sessions"] else None
        g["conversion_rate"] = (g["key_events"] / g["sessions"]) if g["sessions"] else None
        prev_sessions = previous.get(page_path, {}).get("sessions")
        g["sessions_change"] = (g["sessions"] - prev_sessions) if prev_sessions is not None else None
        g["scan"] = _match_scan_for_landing_page(db, ctx.org_id, page_path)
        items.append(g)

    if q:
        needle = q.lower()
        items = [g for g in items if needle in g["landing_page"].lower()]
    items = _apply_sort(items, sort, allowed={"sessions", "new_users", "engaged_sessions",
                                               "views", "key_events", "engagement_rate",
                                               "conversion_rate", "sessions_change", "landing_page"})
    total = len(items)
    paged = items[(page - 1) * page_size: page * page_size]

    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_date_range": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "total": total, "page": page, "page_size": page_size,
        "pages": paged,
    }


def _match_scan_for_landing_page(db: Session, org_id: str, landing_page: str) -> dict | None:
    """Best-effort only: tries to connect a GA4 landing path to an existing
    AEOMirror scan for the SAME organization, so the dashboard can link out to real
    audit evidence for that page. Only attempted when the org has exactly one
    monitored site — with more than one, which site a GA4 property belongs to is
    genuinely ambiguous, and guessing wrong would misattribute audit data to the
    wrong page, so this honestly returns None instead."""
    monitors = db.query(Monitor).filter(Monitor.organization_id == org_id).limit(2).all()
    if len(monitors) != 1:
        return None
    monitor = monitors[0]
    base = monitor.url.rstrip("/")
    candidate = base + landing_page if landing_page.startswith("/") else f"{base}/{landing_page}"
    scan = (db.query(Scan)
            .filter(Scan.organization_id == org_id,
                    (Scan.url == candidate) | (Scan.normalized_url == candidate))
            .order_by(Scan.created_at.desc())
            .first())
    if not scan:
        return None
    return {"scan_id": scan.id, "score": scan.ars}


# ------------------------------- disconnect -------------------------------

@router.delete("")
async def disconnect(ctx: AuthContext = Depends(require_permission("org:update")),
                     db: Session = Depends(get_db)):
    connection = _owned_connection_or_404(db, ctx)
    await ga4.disconnect(db, connection)
    return {"status": "disconnected"}
