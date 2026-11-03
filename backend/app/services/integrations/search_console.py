"""Google Search Console (read-only): OAuth, property discovery, and page-level search
performance sync.

A separate Google grant from GA4, requesting ONLY `webmasters.readonly`, stored in its
own SearchConsoleConnection with its own encrypted refresh token. It reuses the GA4
module's OAuth plumbing (PKCE + single-use state rows, token exchange/refresh/revoke,
token encryption) and the same registered redirect URI — the shared callback routes
on the state row's provider. The GA4 scope, token and connection are never touched.

Search Console reports Google Search clicks/impressions/position per page. It is not
AI-assistant data and is never mixed into AI traffic. The API returns the top rows it
chooses to return (Google omits anonymized queries and caps rows per request/day), so
a sync never claims to hold every page: `truncated` records when our own page cap
stopped the sync, and the UI states Google's own limits.

Every external call is its own small function so tests can monkeypatch it; nothing
here logs a token, code, or Google response body.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode, urlsplit

from sqlalchemy.orm import Session

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, GA4_STATUS_ERROR, GA4_STATUS_PENDING_PROPERTY, GA4_STATUS_REVOKED,
    JOB_PENDING, JOB_RUNNING, GscPageSnapshot, ScheduledJob, SearchConsoleConnection,
)
from app.services.integrations import google_analytics as ga4
from app.services.integrations.google_analytics import GoogleApiError

log = logging.getLogger("app.integrations.search_console")

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
OAUTH_PROVIDER = "google_search_console"
_API = "https://www.googleapis.com/webmasters/v3"
ROW_LIMIT = 25000            # the API's maximum rows per request
MAX_PAGES_PER_SYNC = 8       # 200k page rows per sync, then `truncated`
WINDOW_DAYS = 28
# Search Console data for the most recent ~2 days is still being finalized; the window
# ends before it so a sync doesn't store numbers that are about to change.
DATA_LAG_DAYS = 3


# --------------------------- OAuth ---------------------------

def build_authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": GSC_SCOPE,
        "access_type": "offline",
        "prompt": "consent select_account",
        # Not "true": this grant must carry only webmasters.readonly, never fold in a
        # scope the same Google account granted to the GA4 connection.
        "include_granted_scopes": "false",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{ga4._AUTH_URL}?{urlencode(params)}"


def get_connection(db: Session, org_id: str) -> SearchConsoleConnection | None:
    return (db.query(SearchConsoleConnection)
            .filter(SearchConsoleConnection.organization_id == org_id).first())


async def handle_oauth_callback(db: Session, *, code: str | None, state: str | None,
                                error: str | None) -> dict:
    """Same contract as ga4.handle_oauth_callback: never raises, returns a safe code."""
    if error:
        return {"status": "error", "error_code": "access_denied"}
    oauth_state = ga4.consume_oauth_state(db, state or "", provider=OAUTH_PROVIDER)
    if not oauth_state:
        return {"status": "error", "error_code": "invalid_state"}
    if not code:
        return {"status": "error", "error_code": "missing_code"}
    tokens = await ga4.exchange_code_for_tokens(code, oauth_state.code_verifier)
    if not tokens or not tokens.get("access_token"):
        return {"status": "error", "error_code": "token_exchange_failed"}
    # Google's consent screen lets a user untick a scope; without it nothing works.
    if GSC_SCOPE not in (tokens.get("scope") or "").split():
        return {"status": "error", "error_code": "scope_not_granted"}

    connection = get_connection(db, oauth_state.organization_id)
    refresh_token = tokens.get("refresh_token")
    now = datetime.utcnow()
    if connection is None:
        if not refresh_token:
            return {"status": "error", "error_code": "missing_refresh_token"}
        connection = SearchConsoleConnection(
            organization_id=oauth_state.organization_id, connected_by_user_id=oauth_state.user_id,
            status=GA4_STATUS_PENDING_PROPERTY, connected_at=now, created_at=now)
        db.add(connection)
    if refresh_token:
        connection.encrypted_refresh_token = token_crypto.encrypt_token(refresh_token)
    elif not connection.encrypted_refresh_token:
        return {"status": "error", "error_code": "missing_refresh_token"}
    connection.connected_by_user_id = oauth_state.user_id
    connection.status = GA4_STATUS_ACTIVE if connection.site_url else GA4_STATUS_PENDING_PROPERTY
    connection.last_sync_error_code = None
    connection.updated_at = now
    db.commit()
    return {"status": "connected", "organization_id": oauth_state.organization_id}


def _mark_error(db: Session, connection: SearchConsoleConnection, code: str) -> None:
    connection.status = GA4_STATUS_ERROR
    connection.last_sync_status = "error"
    connection.last_sync_error_code = code
    connection.updated_at = datetime.utcnow()
    db.commit()


async def get_valid_access_token(db: Session, connection: SearchConsoleConnection) -> str | None:
    """Same contract as the GA4 twin: permanent failures mark the connection and return
    None; transient ones raise GoogleTransientError and leave it untouched."""
    from app.services.integrations.google_errors import GooglePermanentError
    plaintext = token_crypto.decrypt_token(connection.encrypted_refresh_token)
    if not plaintext:
        _mark_error(db, connection, "credential_unavailable")
        return None
    try:
        tokens = await ga4.refresh_access_token(plaintext)
    except GooglePermanentError as exc:
        _mark_error(db, connection, exc.code)
        return None
    if not tokens or not tokens.get("access_token"):
        _mark_error(db, connection, "auth_revoked")
        return None
    return tokens["access_token"]


# --------------------------- raw API calls ---------------------------

def _raise_for_quota_or_access(resp) -> None:
    """429, or a 403 whose reason is a rate limit (Search Console often reports quota
    that way) = retry later; any other 403 = this account can no longer read the
    property. A 401 right after a successful token refresh is treated as transient."""
    from app.services.integrations.google_errors import is_rate_limited_403, retry_after_seconds
    if resp.status_code == 429 or (resp.status_code == 403 and is_rate_limited_403(resp)):
        raise GoogleApiError("search console quota exceeded", status_code=429,
                             retry_after=retry_after_seconds(resp))
    if resp.status_code == 403:
        raise GoogleApiError("search console access denied", status_code=403)


async def list_sites(access_token: str) -> list[dict] | None:
    """Verified properties only (permissionLevel != siteUnverifiedUser). None = the
    call failed (never an empty list standing in for a failure)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.get(f"{_API}/sites", headers={"Authorization": f"Bearer {access_token}"})
    except (httpx.TimeoutException, httpx.TransportError):
        return None
    if resp.status_code >= 400:
        log.warning("search console sites list failed: http %s", resp.status_code)
        return None
    try:
        entries = resp.json().get("siteEntry") or []
    except Exception:  # noqa: BLE001
        return None
    return [{"site_url": e["siteUrl"], "permission_level": e.get("permissionLevel")}
            for e in entries
            if e.get("siteUrl") and e.get("permissionLevel") != "siteUnverifiedUser"]


async def query_pages(access_token: str, site_url: str, start_date: str, end_date: str,
                      start_row: int) -> list[dict] | None:
    """One page of Search Analytics rows by page. Raises GoogleApiError on 401/403/429;
    returns None on any other failure."""
    import httpx
    url = f"{_API}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    body = {"startDate": start_date, "endDate": end_date, "dimensions": ["page"], "type": "web",
            "aggregationType": "byPage", "rowLimit": ROW_LIMIT, "startRow": start_row}
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {access_token}"})
    except (httpx.TimeoutException, httpx.TransportError):
        return None
    _raise_for_quota_or_access(resp)
    if resp.status_code >= 400:
        log.warning("search console query failed: http %s", resp.status_code)
        return None
    try:
        rows = resp.json().get("rows") or []
    except Exception:  # noqa: BLE001
        return None
    out = []
    for r in rows:
        keys = r.get("keys") or []
        if not keys or not keys[0]:
            continue
        out.append({"page": keys[0], "clicks": int(r.get("clicks") or 0),
                    "impressions": int(r.get("impressions") or 0),
                    "position": float(r["position"]) if r.get("position") is not None else None})
    return out


# --------------------------- property selection ---------------------------

def site_covers(site_url: str | None, url: str | None) -> bool:
    """Whether a Search Console property covers `url`: a domain property
    (sc-domain:example.com) covers the domain and its subdomains on any protocol; a
    URL-prefix property covers URLs that start with its prefix (scheme + host
    compared case-insensitively). Deterministic — no fuzzy matching."""
    if not site_url or not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if site_url.startswith("sc-domain:"):
        domain = site_url.split(":", 1)[1].strip().lower().rstrip(".")
        return bool(domain) and (host == domain or host.endswith("." + domain))
    try:
        prefix = urlsplit(site_url)
    except ValueError:
        return False
    if (prefix.scheme or "").lower() != (parts.scheme or "").lower() or (prefix.hostname or "").lower() != host:
        return False
    if (prefix.port or None) != (parts.port or None):
        return False
    return (parts.path or "/").startswith(prefix.path or "/")


async def select_site(db: Session, connection: SearchConsoleConnection, site_url: str) -> dict:
    """Accept only a property the connected account itself can access (verified)."""
    from app.services.integrations.google_errors import GoogleTransientError
    try:
        access_token = await get_valid_access_token(db, connection)
    except GoogleTransientError:
        return {"status": "error", "error_code": "properties_unavailable"}
    if not access_token:
        return {"status": "error", "error_code": "auth_revoked"}
    sites = await list_sites(access_token)
    if sites is None:
        return {"status": "error", "error_code": "properties_unavailable"}
    match = next((s for s in sites if s["site_url"] == site_url), None)
    if not match:
        return {"status": "error", "error_code": "property_not_accessible"}
    if connection.site_url != match["site_url"]:
        # Another property's pages must never be shown as this one's.
        (db.query(GscPageSnapshot).filter(GscPageSnapshot.connection_id == connection.id)
         .delete(synchronize_session=False))
        connection.last_synced_at = None
        connection.window_start = connection.window_end = None
        connection.rows_fetched = None
        connection.truncated = None
    connection.site_url = match["site_url"]
    connection.permission_level = match["permission_level"]
    connection.status = GA4_STATUS_ACTIVE
    connection.next_sync_at = datetime.utcnow()
    connection.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


async def disconnect(db: Session, connection: SearchConsoleConnection) -> None:
    """Revoke with Google (best-effort) and always drop the local credential."""
    plaintext = token_crypto.decrypt_token(connection.encrypted_refresh_token)
    if plaintext:
        await ga4.revoke_token(plaintext)
    connection.encrypted_refresh_token = None
    connection.status = GA4_STATUS_REVOKED
    connection.next_sync_at = None
    connection.updated_at = datetime.utcnow()
    (db.query(ScheduledJob)
     .filter(ScheduledJob.connection_id == connection.id,
             ScheduledJob.status.in_((JOB_PENDING, JOB_RUNNING)))
     .update({ScheduledJob.status: "failed", ScheduledJob.error: "connection disconnected"},
             synchronize_session=False))
    db.commit()


# --------------------------- sync ---------------------------

def sync_window(today: date | None = None) -> tuple[date, date]:
    end = (today or date.today()) - timedelta(days=DATA_LAG_DAYS)
    return end - timedelta(days=WINDOW_DAYS - 1), end


async def run_sync(db: Session, connection: SearchConsoleConnection) -> dict:
    """Fetch the window's page rows (paginated, capped) and replace the stored set in
    one transaction — a re-run yields exactly Search Console's current numbers. Raises
    on failures the job queue should retry; nothing is replaced unless every page of
    the fetch succeeded."""
    from app.services.analytics.aggregation import page_key

    from app.services.integrations.google_errors import (
        GoogleTransientError, SyncDeferred, SyncStopped, record_transient,
    )
    if connection.status != GA4_STATUS_ACTIVE or not connection.site_url:
        raise SyncStopped("search console connection is not ready to sync")
    try:
        access_token = await get_valid_access_token(db, connection)
    except GoogleTransientError as exc:
        record_transient(db, connection, exc.code, exc.retry_after)
        raise SyncDeferred(exc.code)
    if not access_token:
        raise SyncStopped("could not obtain a valid access token")

    start, end = sync_window()
    start_s, end_s = start.isoformat(), end.isoformat()
    rows: list[dict] = []
    truncated = False
    for page_no in range(MAX_PAGES_PER_SYNC):
        try:
            batch = await query_pages(access_token, connection.site_url, start_s, end_s, page_no * ROW_LIMIT)
        except GoogleApiError as exc:
            if exc.status_code == 403:
                _mark_error(db, connection, "property_inaccessible")
                raise SyncStopped("property_inaccessible")
            record_transient(db, connection, "quota_exceeded", exc.retry_after)
            raise SyncDeferred("quota_exceeded")
        if batch is None:   # network error, timeout, 5xx or an unusable answer: retry later
            record_transient(db, connection, "google_unavailable")
            raise SyncDeferred("google_unavailable")
        rows.extend(batch)
        if len(batch) < ROW_LIMIT:
            break
    else:
        truncated = True

    now = datetime.utcnow()
    (db.query(GscPageSnapshot).filter(GscPageSnapshot.connection_id == connection.id)
     .delete(synchronize_session=False))
    seen: set[str] = set()
    for r in rows:
        if r["page"] in seen:   # the API shouldn't repeat a page; never double count if it does
            continue
        seen.add(r["page"])
        db.add(GscPageSnapshot(organization_id=connection.organization_id, connection_id=connection.id,
                               page=r["page"][:2000], page_key=page_key(r["page"])[0],
                               clicks=r["clicks"], impressions=r["impressions"],
                               position=r["position"], created_at=now))
    connection.window_start, connection.window_end = start_s, end_s
    connection.rows_fetched = len(seen)
    connection.truncated = truncated
    connection.last_synced_at = now
    connection.last_sync_status = "success"
    connection.last_sync_error_code = None
    connection.sync_failures = 0
    connection.next_sync_at = now + timedelta(hours=settings.ga4_sync_interval_hours)
    connection.updated_at = now
    db.commit()
    return {"rows": len(seen), "truncated": truncated, "window": [start_s, end_s]}
