"""Google OAuth 2.0 (authorization-code + PKCE) and the Google Analytics Admin/Data
REST APIs — plain httpx against Google's documented endpoints, not the official
google-api-python-client/google-auth SDKs. This mirrors this codebase's existing
convention for third-party APIs (app/billing/providers/stripe_provider.py also uses
raw httpx, not the stripe-python SDK): OAuth 2.0 and these specific REST endpoints
are a stable, versioned, well-documented standard, so a dedicated SDK would add
several new heavy dependencies for little real benefit here.

Every external call is isolated in its own small async function so tests can
monkeypatch it directly (the same pattern already used for answer-tracking
providers — see tests/test_answer_tracking.py's _patch_providers) instead of
mocking httpx at the transport layer. Nothing here ever logs a token, authorization
code, or raw Google error body — only exception types / HTTP status codes
(app/core/ai.py's established convention).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, GA4_STATUS_ERROR, GA4_STATUS_PENDING_PROPERTY,
    GA4_STATUS_REVOKED, JOB_PENDING, JOB_RUNNING, Ga4TrafficSnapshot,
    GoogleAnalyticsConnection, OAuthState, ScheduledJob,
)
from app.services.integrations.classification import classify_source

log = logging.getLogger("app.integrations.google_analytics")

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
_DATA_API_TEMPLATE = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"


class GoogleApiError(Exception):
    """A GA4 API call failed in a way the caller must react to specifically (as
    opposed to a generic transport/5xx failure, which just returns None)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# --------------------------- PKCE + OAuth state ---------------------------

def _generate_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) per RFC 7636 (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def create_oauth_state(db: Session, *, org_id: str, user_id: str) -> tuple[OAuthState, str]:
    """Persists a short-lived, single-use state bound to the authenticated caller's
    org+user. Returns (row, code_challenge) — the challenge is embedded in the
    authorize URL; the verifier stays server-side until /callback."""
    verifier, challenge = _generate_pkce_pair()
    now = datetime.utcnow()
    row = OAuthState(
        organization_id=org_id, user_id=user_id, provider="google",
        state=secrets.token_urlsafe(32), code_verifier=verifier, consumed=False,
        created_at=now, expires_at=now + timedelta(seconds=settings.oauth_state_ttl_seconds),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, challenge


def consume_oauth_state(db: Session, state: str) -> OAuthState | None:
    """Single-use: returns the row only if it exists, isn't expired, and hasn't
    already been consumed (replay protection) — then immediately marks it consumed."""
    if not state:
        return None
    row = db.query(OAuthState).filter(OAuthState.state == state).first()
    if not row or row.consumed or row.expires_at < datetime.utcnow():
        return None
    row.consumed = True
    db.commit()
    return row


def build_authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": GA4_SCOPE,
        "access_type": "offline",
        "prompt": "consent",   # ensures a refresh_token is issued even on a reconnect
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


# --------------------------- raw Google HTTP calls ---------------------------
# Each isolated in its own function so tests can monkeypatch it directly.

async def _token_request(payload: dict) -> dict | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.post(_TOKEN_URL, data=payload)
    except (httpx.TimeoutException, httpx.TransportError):
        log.warning("google token request failed: transport error")
        return None
    if resp.status_code >= 400:
        log.warning("google token request failed: http %s", resp.status_code)
        return None
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 — a malformed body is a hard failure, no retry
        return None


async def exchange_code_for_tokens(code: str, code_verifier: str) -> dict | None:
    return await _token_request({
        "code": code, "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "grant_type": "authorization_code", "code_verifier": code_verifier,
    })


async def refresh_access_token(refresh_token: str) -> dict | None:
    return await _token_request({
        "refresh_token": refresh_token, "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "grant_type": "refresh_token",
    })


async def revoke_token(token: str) -> bool:
    """Best-effort remote revocation. The caller MUST invalidate the local credential
    regardless of the return value (Google being unreachable is not a reason to keep
    a usable credential around)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.post(_REVOKE_URL, params={"token": token})
    except (httpx.TimeoutException, httpx.TransportError):
        return False
    return resp.status_code < 400


async def list_ga4_properties(access_token: str) -> list[dict] | None:
    """[{"property_id", "property_name", "account_name"}], paginated. None on any
    failure (never raises) — only property id/name/account name are kept; nothing
    else from Google's response is retained."""
    import httpx
    properties: list[dict] = []
    headers = {"Authorization": f"Bearer {access_token}"}
    page_token = None
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            while True:
                params = {"pageSize": 200}
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(_ADMIN_API, headers=headers, params=params)
                if resp.status_code >= 400:
                    log.warning("ga4 properties list failed: http %s", resp.status_code)
                    return None
                body = resp.json()
                for summary in body.get("accountSummaries", []) or []:
                    account_name = summary.get("displayName", "")
                    for prop in summary.get("propertySummaries", []) or []:
                        properties.append({
                            "property_id": (prop.get("property") or "").removeprefix("properties/"),
                            "property_name": prop.get("displayName", ""),
                            "account_name": account_name,
                        })
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
    except (httpx.TimeoutException, httpx.TransportError):
        log.warning("ga4 properties list failed: transport error")
        return None
    return properties


def _parse_run_report(payload: dict) -> list[dict]:
    rows_out: list[dict] = []
    for row in payload.get("rows", []) or []:
        dims = [d.get("value", "") for d in row.get("dimensionValues", [])]
        mets = [m.get("value", "0") for m in row.get("metricValues", [])]
        if len(dims) < 4 or not mets:
            continue
        date_str, channel_grouping, source, medium = dims[:4]
        try:
            sessions = int(mets[0])
        except (TypeError, ValueError):
            sessions = 0
        rows_out.append({
            "date": date_str, "channel_grouping": channel_grouping or "(not set)",
            "source": source or "(not set)", "medium": medium or "(not set)",
            "sessions": sessions,
        })
    return rows_out


async def fetch_daily_traffic(access_token: str, property_id: str,
                               start_date: str, end_date: str) -> list[dict] | None:
    """Raises GoogleApiError(status_code=403|429) for failures the sync job must
    react to specifically (inaccessible property / quota); returns None for any
    other failure (transport, malformed body) that should just fail this sync
    attempt without a specific error code."""
    import httpx
    url = _DATA_API_TEMPLATE.format(property_id=property_id)
    body = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"},
                        {"name": "sessionSource"}, {"name": "sessionMedium"}],
        "metrics": [{"name": "sessions"}],
        "limit": 100000,
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
    except (httpx.TimeoutException, httpx.TransportError):
        log.warning("ga4 runReport failed: transport error")
        return None
    if resp.status_code == 403:
        raise GoogleApiError("property not accessible", status_code=403)
    if resp.status_code == 429:
        raise GoogleApiError("quota exceeded", status_code=429)
    if resp.status_code >= 400:
        log.warning("ga4 runReport failed: http %s", resp.status_code)
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return _parse_run_report(payload)


# --------------------------- orchestration ---------------------------

def get_connection(db: Session, org_id: str) -> GoogleAnalyticsConnection | None:
    return (db.query(GoogleAnalyticsConnection)
            .filter(GoogleAnalyticsConnection.organization_id == org_id).first())


def _mark_error(db: Session, connection: GoogleAnalyticsConnection, code: str) -> None:
    connection.status = GA4_STATUS_ERROR
    connection.last_sync_status = "error"
    connection.last_sync_error_code = code
    connection.updated_at = datetime.utcnow()
    db.commit()


async def get_valid_access_token(db: Session, connection: GoogleAnalyticsConnection) -> str | None:
    """Decrypt the stored refresh token and exchange it for a fresh, short-lived
    access token. On any auth failure, marks the connection status=error with a
    safe error code and returns None — never raises, never returns a stale token."""
    plaintext_refresh = token_crypto.decrypt_token(connection.encrypted_refresh_token)
    if not plaintext_refresh:
        _mark_error(db, connection, "credential_unavailable")
        return None
    tokens = await refresh_access_token(plaintext_refresh)
    if not tokens or not tokens.get("access_token"):
        _mark_error(db, connection, "auth_revoked")
        return None
    return tokens["access_token"]


async def handle_oauth_callback(db: Session, *, code: str | None, state: str | None,
                                 error: str | None) -> dict:
    """Validates `state`, exchanges `code`, and persists/updates the ONE connection
    row for that org. Returns {"status": "connected"|"error", "error_code"?,
    "organization_id"?}. Never raises — every failure path is a recognized,
    non-sensitive error_code the route can map to a safe redirect."""
    if error:
        return {"status": "error", "error_code": "access_denied"}
    oauth_state = consume_oauth_state(db, state or "")
    if not oauth_state:
        return {"status": "error", "error_code": "invalid_state"}
    if not code:
        return {"status": "error", "error_code": "missing_code"}

    tokens = await exchange_code_for_tokens(code, oauth_state.code_verifier)
    access_token = (tokens or {}).get("access_token")
    if not tokens or not access_token:
        return {"status": "error", "error_code": "token_exchange_failed"}
    refresh_token = tokens.get("refresh_token")

    connection = get_connection(db, oauth_state.organization_id)
    now = datetime.utcnow()
    if connection is None:
        # First-ever connect for this org MUST receive a refresh token (offline
        # access was requested); without one there is nothing persistable.
        if not refresh_token:
            return {"status": "error", "error_code": "missing_refresh_token"}
        connection = GoogleAnalyticsConnection(
            organization_id=oauth_state.organization_id,
            connected_by_user_id=oauth_state.user_id,
            status=GA4_STATUS_PENDING_PROPERTY, connected_at=now,
        )
        db.add(connection)

    # Never overwrite a previously-stored, still-valid refresh token with an empty
    # response (Google only reissues one in some reconnect flows, not every time).
    if refresh_token:
        connection.encrypted_refresh_token = token_crypto.encrypt_token(refresh_token)
    elif not connection.encrypted_refresh_token:
        return {"status": "error", "error_code": "missing_refresh_token"}

    connection.connected_by_user_id = oauth_state.user_id
    connection.status = (GA4_STATUS_ACTIVE if connection.ga4_property_id
                          else GA4_STATUS_PENDING_PROPERTY)
    connection.last_sync_error_code = None
    connection.updated_at = now
    db.commit()
    db.refresh(connection)
    return {"status": "connected", "organization_id": oauth_state.organization_id}


async def select_property(db: Session, connection: GoogleAnalyticsConnection,
                           property_id: str) -> dict:
    """Validates `property_id` against the connected account's OWN accessible
    property list before persisting it — never trusts a client-supplied name, and
    never accepts an arbitrary resource id that happens to look like one."""
    access_token = await get_valid_access_token(db, connection)
    if not access_token:
        return {"status": "error", "error_code": "auth_revoked"}
    properties = await list_ga4_properties(access_token)
    if properties is None:
        return {"status": "error", "error_code": "properties_unavailable"}
    match = next((p for p in properties if p["property_id"] == property_id), None)
    if not match:
        return {"status": "error", "error_code": "property_not_accessible"}

    if connection.ga4_property_id and connection.ga4_property_id != property_id:
        # Switching properties invalidates the prior sync cursor — the new property
        # needs its own initial lookback, not an incremental fetch picking up where
        # the OLD property's history left off.
        connection.last_synced_at = None

    connection.ga4_property_id = match["property_id"]
    connection.ga4_property_name = match["property_name"]
    connection.status = GA4_STATUS_ACTIVE
    connection.next_sync_at = datetime.utcnow()   # sync ASAP after selecting
    connection.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


async def disconnect(db: Session, connection: GoogleAnalyticsConnection) -> None:
    """Revokes with Google (best-effort) and ALWAYS invalidates the local credential
    regardless of whether the remote revoke succeeded — Google being unreachable is
    never a reason to keep a usable stored refresh token."""
    plaintext = token_crypto.decrypt_token(connection.encrypted_refresh_token)
    if plaintext:
        await revoke_token(plaintext)
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


async def run_sync(db: Session, connection: GoogleAnalyticsConnection) -> dict:
    """Fetch + classify + idempotently upsert one connection's trailing window of
    daily traffic. Raises on a failure the job queue should retry (see
    app/monitoring/runner.py's ga4_sync dispatch); returns a summary dict on
    success. A bounded lookback (not every historical day) keeps each sync's GA4
    quota cost and DB write volume small and predictable."""
    if connection.status != GA4_STATUS_ACTIVE or not connection.ga4_property_id:
        raise RuntimeError("connection is not ready to sync (no property selected)")

    access_token = await get_valid_access_token(db, connection)
    if not access_token:
        raise RuntimeError("could not obtain a valid access token")

    lookback = (settings.ga4_initial_sync_lookback_days if connection.last_synced_at is None
                else settings.ga4_sync_lookback_days)
    end = date.today()
    start = end - timedelta(days=lookback)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    try:
        rows = await fetch_daily_traffic(access_token, connection.ga4_property_id, start_str, end_str)
    except GoogleApiError as exc:
        _mark_error(db, connection,
                    "property_inaccessible" if exc.status_code == 403 else "quota_exceeded")
        raise
    if rows is None:
        raise RuntimeError("ga4 runReport returned no usable data")

    # One bulk SELECT for the whole window instead of a per-row query (avoids N+1).
    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    existing = (db.query(Ga4TrafficSnapshot)
                .filter(Ga4TrafficSnapshot.connection_id == connection.id,
                        Ga4TrafficSnapshot.date >= start_key,
                        Ga4TrafficSnapshot.date <= end_key)
                .all())
    existing_by_key = {
        (r.date, r.channel_grouping, r.source, r.medium): r for r in existing
    }

    now = datetime.utcnow()
    for row in rows:
        key = (row["date"], row["channel_grouping"], row["source"], row["medium"])
        label = classify_source(row["source"], row["medium"])
        found = existing_by_key.get(key)
        if found:
            found.sessions = row["sessions"]
            found.ai_source_label = label
            found.updated_at = now
        else:
            db.add(Ga4TrafficSnapshot(
                organization_id=connection.organization_id, connection_id=connection.id,
                date=row["date"], channel_grouping=row["channel_grouping"],
                source=row["source"], medium=row["medium"], ai_source_label=label,
                sessions=row["sessions"], created_at=now, updated_at=now,
            ))

    connection.last_synced_at = now
    connection.last_sync_status = "success"
    connection.last_sync_error_code = None
    connection.status = GA4_STATUS_ACTIVE
    connection.next_sync_at = now + timedelta(hours=settings.ga4_sync_interval_hours)
    connection.updated_at = now
    db.commit()
    return {"rows": len(rows)}
