"""Transient vs permanent Google API failures, shared by GA4 and Search Console.

- Transient (network error, timeout, 429, 5xx, a 403 whose reason is a rate limit):
  the connection stays ACTIVE. Its sync is retried by the scheduler with exponential
  backoff plus jitter (honouring Retry-After); `sync_failures` counts consecutive
  failures and `last_sync_error_code` says why the last attempt failed.
- Permanent (the refresh token was revoked or can't be decrypted, the property is no
  longer accessible, the token endpoint rejects our request): the connection is set to
  ERROR and is never retried until a user reconnects or re-selects the property.

Read-only dashboard requests never change a connection's status on a transient error.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from app.db.models import GA4_STATUS_ERROR

BACKOFF_BASE_SECONDS = 15 * 60
BACKOFF_CAP_SECONDS = 24 * 3600
_RATE_LIMIT_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded", "dailylimitexceeded"}


class GoogleTransientError(Exception):
    """Retry later. `code` is a safe, user-facing reason code; `retry_after` seconds."""

    def __init__(self, code: str, retry_after: float | None = None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


class GooglePermanentError(Exception):
    """Don't retry until a user acts (reconnect / re-select)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SyncDeferred(RuntimeError):
    """A sync failed transiently and its retry has been scheduled on the connection."""


class SyncStopped(RuntimeError):
    """A sync can't run until a user acts; the connection records why."""


def retry_after_seconds(resp) -> float | None:
    """Retry-After as seconds (delta-seconds or an HTTP date), capped at a day."""
    raw = (getattr(resp, "headers", None) or {}).get("retry-after")
    if not raw:
        return None
    try:
        return min(max(float(raw), 0.0), BACKOFF_CAP_SECONDS)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
        delta = (when.replace(tzinfo=None) - datetime.utcnow()).total_seconds()
        return min(max(delta, 0.0), BACKOFF_CAP_SECONDS)
    except (TypeError, ValueError):
        return None


def is_rate_limited_403(resp) -> bool:
    """Google APIs sometimes answer quota exhaustion with 403 rather than 429."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(err, dict):
        return False
    if str(err.get("status", "")).upper() == "RESOURCE_EXHAUSTED":
        return True
    reasons = {str(e.get("reason", "")).lower() for e in (err.get("errors") or []) if isinstance(e, dict)}
    reasons |= {str(d.get("reason", "")).lower() for d in (err.get("details") or []) if isinstance(d, dict)}
    return bool(reasons & _RATE_LIMIT_REASONS)


def backoff_seconds(failures: int, retry_after: float | None = None, *, rand=random.random) -> float:
    """15 min, 30 min, 1 h, 2 h … capped at 24 h, ±20 % jitter, never sooner than Retry-After."""
    n = max(1, failures)
    base = min(BACKOFF_BASE_SECONDS * (2 ** (n - 1)), BACKOFF_CAP_SECONDS)
    delay = base * (0.8 + 0.4 * rand())
    if retry_after:
        delay = max(delay, retry_after)
    return min(delay, BACKOFF_CAP_SECONDS * 1.2)


def record_transient(db: Session, connection, code: str, retry_after: float | None = None,
                     now: datetime | None = None) -> datetime:
    """Keep the connection active and schedule its next attempt with backoff."""
    now = now or datetime.utcnow()
    connection.sync_failures = (connection.sync_failures or 0) + 1
    connection.last_sync_status = "error"
    connection.last_sync_error_code = code
    connection.next_sync_at = now + timedelta(seconds=backoff_seconds(connection.sync_failures, retry_after))
    connection.updated_at = now
    db.commit()
    return connection.next_sync_at


def record_permanent(db: Session, connection, code: str, now: datetime | None = None) -> None:
    """Stop syncing until a user reconnects or re-selects the property."""
    connection.status = GA4_STATUS_ERROR
    connection.last_sync_status = "error"
    connection.last_sync_error_code = code
    connection.updated_at = now or datetime.utcnow()
    db.commit()


def classify_token_failure(resp) -> Exception:
    """The token endpoint's answer to a refresh: what kind of failure it is."""
    if resp.status_code == 429:
        return GoogleTransientError("quota_exceeded", retry_after_seconds(resp))
    if resp.status_code >= 500:
        return GoogleTransientError("google_unavailable", retry_after_seconds(resp))
    try:
        error = str((resp.json() or {}).get("error", "")).lower()
    except Exception:  # noqa: BLE001
        error = ""
    if error == "invalid_grant":
        return GooglePermanentError("auth_revoked")      # revoked, expired or password-changed grant
    return GooglePermanentError("auth_failed")          # our client credentials/request were rejected
