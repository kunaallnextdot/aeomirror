"""Cache + rate limiter.

Both are Redis-backed when REDIS_URL is set, so cache dedupe and rate limits are
shared across workers/instances. When REDIS_URL is unset — or Redis is transiently
unavailable — both degrade gracefully to a per-process in-memory implementation
(fail-open): the scanner keeps working and NEVER fabricates results. Redis errors
are logged by exception type only; connection strings/credentials are never logged.

Serialization is JSON only (no pickle). Keys are namespaced:
  cache:        aeomirror:scan-cache:<sha256(normalized-url)>
  rate limit:   aeomirror:rate-limit:free-scan:<client-id>
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from threading import Lock

from app.config import settings

logger = logging.getLogger("aeomirror.cache")

_redis_client = None
_redis_init = False


def get_redis():
    """Return a shared Redis client, or None if REDIS_URL is not configured.
    Never raises; connection problems surface when a command is issued."""
    global _redis_client, _redis_init
    if not settings.redis_url:
        return None
    if not _redis_init:
        _redis_init = True
        try:
            import redis  # local import so the dep is optional in dev/test
            _redis_client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as e:  # pragma: no cover - construction rarely fails
            logger.warning("Redis client init failed (%s); using in-memory fallback",
                           type(e).__name__)
            _redis_client = None
    return _redis_client


def redis_configured() -> bool:
    return bool(settings.redis_url)


def redis_healthy() -> bool:
    """True only if Redis is configured AND reachable. Never raises."""
    c = get_redis()
    if c is None:
        return False
    try:
        return bool(c.ping())
    except Exception as e:
        logger.warning("Redis ping failed: %s", type(e).__name__)
        return False


class TTLCache:
    """TTL cache. Redis-backed when available, else per-process in-memory.
    Fails open: on any Redis error, reads return None and writes are skipped so
    the caller recomputes rather than serving stale/incorrect data."""

    PREFIX = "aeomirror:scan-cache:"

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = Lock()

    def _rkey(self, key: str) -> str:
        return self.PREFIX + hashlib.sha256(key.encode()).hexdigest()

    def get(self, key: str):
        c = get_redis()
        if c is not None:
            try:
                raw = c.get(self._rkey(key))
                return json.loads(raw) if raw else None
            except Exception as e:
                logger.warning("cache get failed, failing open: %s", type(e).__name__)
                return None
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires, value = item
            if time.time() > expires:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: object):
        c = get_redis()
        if c is not None:
            try:
                c.set(self._rkey(key), json.dumps(value), ex=self.ttl)
            except Exception as e:
                logger.warning("cache set failed, failing open: %s", type(e).__name__)
            return
        with self._lock:
            self._store[key] = (time.time() + self.ttl, value)


class RateLimiter:
    """Fixed-window per-key limiter. Returns (allowed, remaining, retry_after).

    Redis path uses an atomic INCR+EXPIRE Lua script so the count and its window
    are set as one operation (no non-atomic GET/SET). When Redis is unavailable it
    falls back to a per-process in-memory window so some protection remains."""

    PREFIX = "aeomirror:rate-limit:free-scan:"
    # KEYS[1]=counter key, ARGV[1]=window seconds. Returns the new count.
    _INCR_LUA = (
        "local c = redis.call('INCR', KEYS[1]) "
        "if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end "
        "return c"
    )

    def __init__(self, limit: int, window: int, prefix: str | None = None):
        self.limit = limit
        self.window = window
        if prefix:
            self.PREFIX = prefix
        self._hits: dict[str, list[float]] = {}
        self._lock = Lock()

    def check(self, key: str) -> tuple[bool, int, int]:
        c = get_redis()
        if c is not None:
            try:
                rkey = self.PREFIX + key
                count = int(c.eval(self._INCR_LUA, 1, rkey, self.window))
                allowed = count <= self.limit
                remaining = max(0, self.limit - count)
                retry_after = 0
                if not allowed:
                    ttl = c.ttl(rkey)
                    retry_after = ttl if isinstance(ttl, int) and ttl > 0 else self.window
                return allowed, remaining, retry_after
            except Exception as e:
                logger.warning("rate limit check failed, using in-memory fallback: %s",
                               type(e).__name__)
                # fall through to in-memory
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            allowed = len(hits) < self.limit
            if allowed:
                hits.append(now)
            self._hits[key] = hits
            remaining = max(0, self.limit - len(hits))
            retry_after = 0 if allowed else int(self.window - (now - min(hits))) if hits else self.window
            return allowed, remaining, max(0, retry_after)


scan_cache = TTLCache(settings.cache_ttl_seconds)
rate_limiter = RateLimiter(
    settings.free_scans_per_window, settings.rate_limit_window_seconds
)
# Phase 5: login-abuse limiter (per email+IP), separate namespace + budget.
login_limiter = RateLimiter(
    settings.login_max_attempts, settings.login_window_seconds,
    prefix="aeomirror:rate-limit:login:",
)
# Contact-form limiter (per IP), separate namespace + budget — anti-spam.
contact_limiter = RateLimiter(
    settings.contact_max_per_window, settings.contact_rate_window_seconds,
    prefix="aeomirror:rate-limit:contact:",
)
# Public report-share read limiter (per IP), separate namespace — anti-scraping on the
# unauthenticated /public/reports/{token} path (token entropy already blocks guessing).
public_report_limiter = RateLimiter(
    settings.public_report_max_per_window, settings.public_report_rate_window_seconds,
    prefix="aeomirror:rate-limit:public-report:",
)
