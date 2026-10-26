"""First-party website analytics (AI Business Impact) — GA4-independent tracking.

Two trust boundaries:
- `/t/tracker.js` and `/t/collect` are PUBLIC (no auth): the tracker is embedded in a
  customer's own HTML and runs in every visitor's browser, so `site_id` (a Monitor's
  id) is a public identifier, never a secret — matches the existing GA4 router's
  public `/callback` route for the same reason (see routes_integrations.py's module
  docstring). Rate-limited per (site_id, hashed IP); validated, size-capped, and
  idempotent (event_id / external_id dedupe — see services/analytics/ingest.py).
- `/monitors/{id}/tracking*` management endpoints require the existing Bearer-JWT org
  auth (RBAC + org ownership, same as every other monitor-scoped route).
- `/monitors/{id}/tracking/confirm` is a THIRD boundary: authenticated with a
  per-site, hashed "tracking API key" (never the org's user credentials) — meant to
  be called server-to-server, by the CUSTOMER's own backend, after a real
  purchase/booking actually succeeds. A client-side event alone is NEVER treated as
  proof of a transaction; only this path can mark a conversion `verified`.
"""
from __future__ import annotations

import logging
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_permission
from app.api.routes_monitors import _owned_monitor
from app.api.routes_scan import _client_ip, _ip_hash
from app.config import settings
from app.core.cache import RateLimiter
from app.core.security import hash_token
from app.db.models import AnalyticsSession, Monitor
from app.db.session import get_db
from app.schemas.analytics import ConfirmConversionRequest, TrackBatch
from app.services.analytics import ingest

log = logging.getLogger("app.analytics.tracking")
router = APIRouter(tags=["analytics"])

tracking_limiter = RateLimiter(
    settings.tracking_max_events_per_window, settings.tracking_rate_window_seconds,
    prefix="aeomirror:rate-limit:tracking:",
)

_TRACKER_JS_PATH = Path(__file__).resolve().parent.parent / "static" / "tracker.js"
_TRACKER_JS = _TRACKER_JS_PATH.read_text()


def _host(url: str | None) -> str | None:
    """Lowercased hostname with a leading 'www.' stripped, or None. Same www-folding
    convention as app.scanner.bulk.normalize_dedup_key, so "example.com" and
    "www.example.com" are always the same host for this comparison."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _origin_allowed(request: Request, monitor: Monitor) -> bool:
    """Best-effort check that this beacon actually came from a page served on the
    site this `site_id` belongs to, using the browser-set Origin/Referer headers —
    NOT the event body's own `url`/`referrer` fields, which are just JS-supplied
    claims an attacker's own page could set to anything. Origin/Referer are set by
    the browser itself and can't be overridden by page JS, even cross-origin.

    Fails OPEN (returns True) when neither header is present: some privacy-hardened
    browsers/extensions strip both, and the tracker's own first rule is to never
    break a legitimate install over an edge case it can't actually verify. This is
    not a confidentiality boundary (site_id is already public, embedded in HTML) —
    it only raises the cost of casually pointing an unrelated page at someone else's
    site_id to pollute their session counts."""
    request_host = _host(request.headers.get("origin")) or _host(request.headers.get("referer"))
    if not request_host:
        return True
    monitor_host = _host(monitor.normalized_url) or _host(monitor.url)
    if not monitor_host:
        return True
    return request_host == monitor_host or request_host.endswith("." + monitor_host)

# A public, write-only beacon endpoint — any origin may POST to it by design (that's
# the whole point of an embeddable tracker). The tracker itself avoids a CORS
# preflight entirely (sendBeacon / fetch with a "simple" Content-Type), but these
# headers are set defensively for any client that does preflight — though note that
# Starlette's global CORSMiddleware (app/main.py, the app's own strict, explicit-
# origins-only policy for authenticated routes) intercepts and short-circuits an
# OPTIONS preflight for a non-allowed origin BEFORE routing reaches this file at all,
# since it has no concept of a per-path exception. This is exactly why tracker.js is
# deliberately built to NEVER trigger a preflight (sendBeacon, and a text/plain
# fetch() fallback — both are CORS "simple requests" by spec, the standard technique
# every embeddable analytics script uses for this same reason). A custom integration
# posting here directly should do the same (avoid a JSON Content-Type) rather than
# relying on this endpoint's own headers to satisfy a preflighted request.
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


# ------------------------------- public: tracker.js -------------------------------

@router.get("/t/tracker.js")
def tracker_script():
    return Response(content=_TRACKER_JS, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})


# ------------------------------- public: collector -------------------------------

@router.options("/t/collect")
def collect_preflight():
    return Response(status_code=204, headers=_CORS_HEADERS)


@router.post("/t/collect")
def collect(body: TrackBatch, request: Request, db: Session = Depends(get_db)):
    ip_hash = _ip_hash(_client_ip(request))
    allowed, _remaining, retry_after = tracking_limiter.check(f"{body.site_id}:{ip_hash}")
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many events.",
                            headers={**_CORS_HEADERS, "Retry-After": str(retry_after)})

    monitor = db.get(Monitor, body.site_id)
    if not monitor:
        # The site id is public (embedded in HTML) — unlike an auth boundary, telling
        # an installer "that id doesn't exist" is a legitimate, expected debugging
        # signal, not a confidentiality leak.
        raise HTTPException(status_code=404, detail="Unknown site id.", headers=_CORS_HEADERS)
    if not _origin_allowed(request, monitor):
        # A real Origin/Referer was present and didn't match this site_id's own
        # domain — most likely a page pointed at someone else's site_id. Rejected
        # outright rather than silently dropped, so a misconfigured install (e.g. a
        # genuinely different subdomain) is an obvious, debuggable 403, not missing data.
        raise HTTPException(status_code=403, detail="Origin not authorized for this site id.",
                            headers=_CORS_HEADERS)

    processed = 0
    for ev in body.events:
        if ev.type == "pageview":
            session = ingest.get_or_create_session(
                db, monitor=monitor, session_id=ev.session_id, visitor_id=ev.visitor_id,
                landing_url=ev.url, referrer=ev.referrer, utm_source=ev.utm_source,
                utm_medium=ev.utm_medium, utm_campaign=ev.utm_campaign,
            )
            ingest.record_pageview(db, monitor=monitor, session=session,
                                   event_id=ev.event_id, url=ev.url)
            processed += 1
        elif ev.type == "conversion":
            if not ev.event_type or not ev.external_id:
                continue   # malformed conversion row — skip it, never guess required fields
            session = (db.query(AnalyticsSession)
                       .filter(AnalyticsSession.monitor_id == monitor.id,
                               AnalyticsSession.session_id == ev.session_id).first())
            # Client-reported only — NEVER verified=True from this public path.
            ingest.record_conversion(
                db, monitor=monitor, session=session, event_type=ev.event_type,
                event_name=ev.event_name, external_id=ev.external_id,
                value_cents=ev.value_cents, currency=ev.currency, verified=False,
            )
            processed += 1
    db.commit()
    return Response(status_code=204, headers=_CORS_HEADERS)


# ------------------------------- authenticated: setup -------------------------------

def _snippet(site_id: str) -> str:
    host = settings.api_base_url.rstrip("/")
    return f'<script defer src="{host}/t/tracker.js" data-site-id="{site_id}"></script>'


@router.get("/monitors/{monitor_id}/tracking")
def tracking_status(monitor_id: str,
                    ctx: AuthContext = Depends(require_permission("report:view")),
                    db: Session = Depends(get_db)):
    """Setup status is based on ACTUAL received events, never assumed from a snippet
    being copied — installed=True only once a real pageview has been recorded."""
    monitor = _owned_monitor(db, ctx, monitor_id)
    first_seen = (db.query(func.min(AnalyticsSession.started_at))
                 .filter(AnalyticsSession.monitor_id == monitor.id).scalar())
    last_seen = (db.query(func.max(AnalyticsSession.last_seen_at))
                .filter(AnalyticsSession.monitor_id == monitor.id).scalar())
    return {
        "site_id": monitor.id,
        "snippet": _snippet(monitor.id),
        "tracker_url": f"{settings.api_base_url.rstrip('/')}/t/tracker.js",
        "collect_url": f"{settings.api_base_url.rstrip('/')}/t/collect",
        "installed": first_seen is not None,
        "installed_since": first_seen,
        "last_event_at": last_seen,
        "has_tracking_api_key": bool(monitor.tracking_api_key_hash),
    }


@router.post("/monitors/{monitor_id}/tracking/api-key")
def generate_tracking_api_key(monitor_id: str,
                              ctx: AuthContext = Depends(require_permission("org:update")),
                              db: Session = Depends(get_db)):
    """Generates (or rotates) the per-site server-to-server key used by the CUSTOMER'S
    OWN backend to confirm real purchases/bookings. Shown in plaintext exactly once —
    only its hash is ever persisted, same convention as Session.refresh_token_hash."""
    monitor = _owned_monitor(db, ctx, monitor_id)
    raw_key = secrets.token_urlsafe(32)
    monitor.tracking_api_key_hash = hash_token(raw_key)
    db.commit()
    return {"api_key": raw_key, "note": "Store this now — it will not be shown again."}


# ------------------------------- server-to-server: confirm -------------------------------

def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def _verify_tracking_key(db: Session, monitor_id: str, authorization: str | None) -> Monitor:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing tracking API key.")
    monitor = db.get(Monitor, monitor_id)
    if not monitor or not monitor.tracking_api_key_hash:
        raise HTTPException(status_code=404, detail="Tracking is not configured for this site.")
    if hash_token(token) != monitor.tracking_api_key_hash:
        raise HTTPException(status_code=401, detail="Invalid tracking API key.")
    return monitor


@router.post("/monitors/{monitor_id}/tracking/confirm")
def confirm_conversion(monitor_id: str, body: ConfirmConversionRequest,
                       authorization: str | None = Header(default=None),
                       db: Session = Depends(get_db)):
    """The ONLY path that can mark a conversion verified=True. Deduplicated on
    (monitor, event_type, external_id) — confirming the same external_id twice
    updates the same row, never creates a duplicate."""
    monitor = _verify_tracking_key(db, monitor_id, authorization)
    session = None
    if body.session_id:
        session = (db.query(AnalyticsSession)
                  .filter(AnalyticsSession.monitor_id == monitor.id,
                          AnalyticsSession.session_id == body.session_id).first())
    conv = ingest.record_conversion(
        db, monitor=monitor, session=session, event_type=body.event_type,
        event_name=body.event_name, external_id=body.external_id,
        value_cents=body.value_cents, currency=body.currency, verified=True,
    )
    db.commit()
    return {"ok": True, "conversion_id": conv.id, "verified": conv.verified}
