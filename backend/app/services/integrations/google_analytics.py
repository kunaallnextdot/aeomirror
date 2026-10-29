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
    GA4_STATUS_REVOKED, JOB_PENDING, JOB_RUNNING, Ga4DailyOverview,
    Ga4LandingPageSnapshot, Ga4TrafficSnapshot, GoogleAnalyticsConnection,
    OAuthState, ScheduledJob,
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
        # "consent" ensures a refresh_token is issued even on a reconnect. "select_account"
        # is deliberately ALSO included: without it, Google often silently reuses the
        # browser's last-authenticated Google session instead of showing the account
        # chooser — the single most common cause of "connected, but 0 GA4 properties
        # accessible" (the wrong Google account, with no GA4 access, never got a chance
        # to be swapped out). Space-delimited multi-value prompt is per Google's own
        # documented OAuth 2.0 spec, not a workaround.
        "prompt": "consent select_account",
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


def _google_error_reason(resp) -> str:
    """Google's own structured reason for a rejected Data/Admin API request, e.g.
    "INVALID_ARGUMENT: Field dateRange is not a valid dimension." These messages
    describe the request's fields, never credentials — only the structured
    status + a truncated message are kept, never the raw body. Without this a 400
    logged as a bare "http 400", which is what made a real invalid-dimension bug
    in /overview impossible to diagnose from production logs. Not used for the
    OAuth token endpoint, whose error bodies stay unlogged."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:  # noqa: BLE001 — a non-JSON error body just means no reason
        return "no structured reason"
    return f"{err.get('status', 'UNKNOWN')}: {str(err.get('message', ''))[:300]}"


# Metric list shared by every runReport call below. Order matters — every parser
# in this module reads metricValues by this exact index. All five are strict counts
# (never a deduplicated-unique metric), so every value here is safe to sum across
# days, channels, or landing pages — see Ga4TrafficSnapshot's docstring.
_COUNT_METRICS = [
    {"name": "sessions"}, {"name": "newUsers"}, {"name": "engagedSessions"},
    {"name": "screenPageViews"}, {"name": "userEngagementDuration"}, {"name": "keyEvents"},
]


def _parse_count_metrics(mets: list[str]) -> dict:
    """mets must align with _COUNT_METRICS's order. Never raises — a malformed or
    missing value for any metric becomes 0/0.0 rather than failing the whole row."""
    def _int(i: int) -> int:
        try:
            return int(mets[i])
        except (IndexError, TypeError, ValueError):
            return 0

    def _float(i: int) -> float:
        try:
            return float(mets[i])
        except (IndexError, TypeError, ValueError):
            return 0.0

    return {
        "sessions": _int(0), "new_users": _int(1), "engaged_sessions": _int(2),
        "views": _int(3), "engagement_duration_sec": _float(4), "key_events": _int(5),
    }


def _parse_run_report(payload: dict) -> list[dict]:
    """date + sessionDefaultChannelGroup + sessionSource + sessionMedium grain."""
    rows_out: list[dict] = []
    for row in payload.get("rows", []) or []:
        dims = [d.get("value", "") for d in row.get("dimensionValues", [])]
        mets = [m.get("value", "0") for m in row.get("metricValues", [])]
        if len(dims) < 4 or not mets:
            continue
        date_str, channel_grouping, source, medium = dims[:4]
        rows_out.append({
            "date": date_str, "channel_grouping": channel_grouping or "(not set)",
            "source": source or "(not set)", "medium": medium or "(not set)",
            **_parse_count_metrics(mets),
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
        "metrics": _COUNT_METRICS,
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
        log.warning("ga4 runReport failed: http %s (%s)", resp.status_code, _google_error_reason(resp))
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return _parse_run_report(payload)


def _parse_daily_overview_report(payload: dict) -> list[dict]:
    """date-only grain, WITH totalUsers (safe here: one row == one day == exactly
    what GA4 reports for that single day, never summed with another row)."""
    rows_out: list[dict] = []
    for row in payload.get("rows", []) or []:
        dims = [d.get("value", "") for d in row.get("dimensionValues", [])]
        mets = [m.get("value", "0") for m in row.get("metricValues", [])]
        if not dims or not mets:
            continue
        try:
            total_users = int(mets[0])
        except (TypeError, ValueError):
            total_users = 0
        rows_out.append({
            "date": dims[0],
            "total_users": total_users,
            **_parse_count_metrics(mets[1:]),
        })
    return rows_out


async def fetch_daily_overview(access_token: str, property_id: str,
                                start_date: str, end_date: str) -> list[dict] | None:
    """Same failure contract as fetch_daily_traffic. totalUsers is requested FIRST,
    ahead of _COUNT_METRICS, specifically so this is the one report in the module
    that carries a per-day unique-user count — see Ga4DailyOverview's docstring for
    why it is never summed across days."""
    import httpx
    url = _DATA_API_TEMPLATE.format(property_id=property_id)
    body = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": "date"}],
        "metrics": [{"name": "totalUsers"}, *_COUNT_METRICS],
        "limit": 100000,
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
    except (httpx.TimeoutException, httpx.TransportError):
        log.warning("ga4 daily-overview report failed: transport error")
        return None
    if resp.status_code == 403:
        raise GoogleApiError("property not accessible", status_code=403)
    if resp.status_code == 429:
        raise GoogleApiError("quota exceeded", status_code=429)
    if resp.status_code >= 400:
        log.warning("ga4 daily-overview report failed: http %s (%s)", resp.status_code, _google_error_reason(resp))
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return _parse_daily_overview_report(payload)


def _parse_landing_page_report(payload: dict) -> list[dict]:
    rows_out: list[dict] = []
    for row in payload.get("rows", []) or []:
        dims = [d.get("value", "") for d in row.get("dimensionValues", [])]
        mets = [m.get("value", "0") for m in row.get("metricValues", [])]
        if len(dims) < 2 or not mets:
            continue
        date_str, landing_page = dims[:2]
        rows_out.append({
            "date": date_str, "landing_page": landing_page or "(not set)",
            **_parse_count_metrics(mets),
        })
    return rows_out


async def fetch_landing_pages(access_token: str, property_id: str,
                               start_date: str, end_date: str) -> list[dict] | None:
    """Same failure contract as fetch_daily_traffic. No totalUsers here either —
    see Ga4LandingPageSnapshot's docstring."""
    import httpx
    url = _DATA_API_TEMPLATE.format(property_id=property_id)
    body = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": "date"}, {"name": "landingPage"}],
        "metrics": _COUNT_METRICS,
        "limit": 100000,
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
    except (httpx.TimeoutException, httpx.TransportError):
        log.warning("ga4 landing-page report failed: transport error")
        return None
    if resp.status_code == 403:
        raise GoogleApiError("property not accessible", status_code=403)
    if resp.status_code == 429:
        raise GoogleApiError("quota exceeded", status_code=429)
    if resp.status_code >= 400:
        log.warning("ga4 landing-page report failed: http %s (%s)", resp.status_code, _google_error_reason(resp))
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return _parse_landing_page_report(payload)


def _parse_overview_comparison_report(payload: dict) -> dict:
    # GA4 adds the dateRange column to the response itself (it is not a requestable
    # dimension), so find it by header name rather than assuming a position.
    headers = [h.get("name") for h in payload.get("dimensionHeaders", []) or []]
    range_idx = headers.index("dateRange") if "dateRange" in headers else 0
    out: dict = {"current": None, "previous": None}
    for row in payload.get("rows", []) or []:
        dims = [d.get("value", "") for d in row.get("dimensionValues", [])]
        mets = [m.get("value", "0") for m in row.get("metricValues", [])]
        if len(dims) <= range_idx or not mets:
            continue
        label = dims[range_idx] if dims[range_idx] in ("current", "previous") else None
        if label is None:
            continue
        try:
            total_users = int(mets[0])
        except (TypeError, ValueError):
            total_users = 0
        out[label] = {"total_users": total_users, **_parse_count_metrics(mets[1:])}

    zero = {"total_users": 0, **_parse_count_metrics([])}
    out["current"] = out["current"] or dict(zero)
    out["previous"] = out["previous"] or dict(zero)
    return out


async def fetch_overview_with_comparison(
    access_token: str, property_id: str, *,
    current_start: str, current_end: str, previous_start: str, previous_end: str,
) -> dict | None:
    """ONE live runReport call, using GA4's own two-dateRanges feature, to get
    correctly-scoped totalUsers/newUsers/etc. for the current AND the immediately
    preceding period in a single request. This is the ONLY place the dashboard
    queries GA4 live rather than reading a stored snapshot — deliberately, because
    totalUsers for an arbitrary multi-day range cannot be reconstructed by summing
    stored per-day rows (see Ga4TrafficSnapshot's docstring) and must be computed by
    GA4 itself for the exact requested range. Returns
    {"current": {...}, "previous": {...}} (each a _parse_count_metrics-shaped dict
    plus total_users), or None on any failure. Raises GoogleApiError(403|429) like
    the other fetchers."""
    import httpx
    url = _DATA_API_TEMPLATE.format(property_id=property_id)
    body = {
        "dateRanges": [
            {"startDate": current_start, "endDate": current_end, "name": "current"},
            {"startDate": previous_start, "endDate": previous_end, "name": "previous"},
        ],
        "metrics": [{"name": "totalUsers"}, *_COUNT_METRICS],
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
    except (httpx.TimeoutException, httpx.TransportError):
        log.warning("ga4 overview-comparison report failed: transport error")
        return None
    if resp.status_code == 403:
        raise GoogleApiError("property not accessible", status_code=403)
    if resp.status_code == 429:
        raise GoogleApiError("quota exceeded", status_code=429)
    if resp.status_code >= 400:
        log.warning("ga4 overview-comparison report failed: http %s (%s)", resp.status_code, _google_error_reason(resp))
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return _parse_overview_comparison_report(payload)


async def fetch_key_events_configured(access_token: str, property_id: str) -> bool | None:
    """Whether the property has ANY key event configured. GA4's `keyEvents` metric
    silently returns 0 for every row when none are configured at all — with no way
    to tell that apart from "zero conversions happened" from the Data API alone —
    so the Admin API's own keyEvents list is the only reliable signal. Returns None
    (never raises) on any failure, meaning "unknown", not "false"."""
    import httpx
    url = f"https://analyticsadmin.googleapis.com/v1beta/properties/{property_id}/keyEvents"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.ga4_api_timeout_seconds) as client:
            resp = await client.get(url, headers=headers, params={"pageSize": 1})
    except (httpx.TimeoutException, httpx.TransportError):
        return None
    if resp.status_code >= 400:
        log.warning("ga4 keyEvents list failed: http %s (%s)", resp.status_code, _google_error_reason(resp))
        return None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return bool(body.get("keyEvents"))


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
            found.new_users = row["new_users"]
            found.engaged_sessions = row["engaged_sessions"]
            found.views = row["views"]
            found.engagement_duration_sec = row["engagement_duration_sec"]
            found.key_events = row["key_events"]
            found.ai_source_label = label
            found.updated_at = now
        else:
            db.add(Ga4TrafficSnapshot(
                organization_id=connection.organization_id, connection_id=connection.id,
                date=row["date"], channel_grouping=row["channel_grouping"],
                source=row["source"], medium=row["medium"], ai_source_label=label,
                sessions=row["sessions"], new_users=row["new_users"],
                engaged_sessions=row["engaged_sessions"], views=row["views"],
                engagement_duration_sec=row["engagement_duration_sec"],
                key_events=row["key_events"], created_at=now, updated_at=now,
            ))

    # Daily-overview and landing-page reports are best-effort: a transient failure
    # on either one does not fail the whole sync (the primary by-channel traffic
    # report above already succeeded and is the most important dataset — the AI
    # Traffic breakdown — so we keep that result rather than discarding it over a
    # secondary report's hiccup). Each is retried on the next scheduled sync.
    try:
        overview_rows = await fetch_daily_overview(access_token, connection.ga4_property_id,
                                                     start_str, end_str)
    except GoogleApiError:
        overview_rows = None
    if overview_rows is not None:
        existing_overview = (db.query(Ga4DailyOverview)
                              .filter(Ga4DailyOverview.connection_id == connection.id,
                                      Ga4DailyOverview.date >= start_key,
                                      Ga4DailyOverview.date <= end_key)
                              .all())
        existing_overview_by_date = {r.date: r for r in existing_overview}
        for row in overview_rows:
            found = existing_overview_by_date.get(row["date"])
            if found:
                found.total_users = row["total_users"]
                found.new_users = row["new_users"]
                found.sessions = row["sessions"]
                found.engaged_sessions = row["engaged_sessions"]
                found.views = row["views"]
                found.engagement_duration_sec = row["engagement_duration_sec"]
                found.key_events = row["key_events"]
                found.updated_at = now
            else:
                db.add(Ga4DailyOverview(
                    organization_id=connection.organization_id, connection_id=connection.id,
                    date=row["date"], total_users=row["total_users"],
                    new_users=row["new_users"], sessions=row["sessions"],
                    engaged_sessions=row["engaged_sessions"], views=row["views"],
                    engagement_duration_sec=row["engagement_duration_sec"],
                    key_events=row["key_events"], created_at=now, updated_at=now,
                ))
    else:
        log.warning("ga4 sync: daily-overview report unavailable this cycle, skipping")

    try:
        landing_rows = await fetch_landing_pages(access_token, connection.ga4_property_id,
                                                  start_str, end_str)
    except GoogleApiError:
        landing_rows = None
    if landing_rows is not None:
        existing_landing = (db.query(Ga4LandingPageSnapshot)
                             .filter(Ga4LandingPageSnapshot.connection_id == connection.id,
                                     Ga4LandingPageSnapshot.date >= start_key,
                                     Ga4LandingPageSnapshot.date <= end_key)
                             .all())
        existing_landing_by_key = {(r.date, r.landing_page): r for r in existing_landing}
        for row in landing_rows:
            key = (row["date"], row["landing_page"])
            found = existing_landing_by_key.get(key)
            if found:
                found.sessions = row["sessions"]
                found.new_users = row["new_users"]
                found.engaged_sessions = row["engaged_sessions"]
                found.views = row["views"]
                found.engagement_duration_sec = row["engagement_duration_sec"]
                found.key_events = row["key_events"]
                found.updated_at = now
            else:
                db.add(Ga4LandingPageSnapshot(
                    organization_id=connection.organization_id, connection_id=connection.id,
                    date=row["date"], landing_page=row["landing_page"],
                    sessions=row["sessions"], new_users=row["new_users"],
                    engaged_sessions=row["engaged_sessions"], views=row["views"],
                    engagement_duration_sec=row["engagement_duration_sec"],
                    key_events=row["key_events"], created_at=now, updated_at=now,
                ))
    else:
        log.warning("ga4 sync: landing-page report unavailable this cycle, skipping")

    # Cheap, infrequent config check — only overwrite a previously-known value when
    # this call actually succeeds, so a transient failure never flips a known
    # True/False back to "unknown".
    key_events_configured = await fetch_key_events_configured(access_token, connection.ga4_property_id)
    if key_events_configured is not None:
        connection.key_events_configured = key_events_configured

    connection.last_synced_at = now
    connection.last_sync_status = "success"
    connection.last_sync_error_code = None
    connection.status = GA4_STATUS_ACTIVE
    connection.next_sync_at = now + timedelta(hours=settings.ga4_sync_interval_hours)
    connection.updated_at = now
    db.commit()
    return {"rows": len(rows)}
