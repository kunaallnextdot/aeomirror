"""Lightweight observability: structured logging, request IDs, in-process metrics.

No external monitoring service. Structured logs are emitted as single-line JSON so
they are easy to grep or ship later. Metrics are process-local counters exposed via
the admin debug endpoint. Secrets are never logged.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


# ----------------------------- logging -----------------------------
def configure_logging() -> None:
    """Send app logs to stdout at the configured level. Idempotent."""
    root = logging.getLogger()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)
    # Avoid duplicate handlers on reload.
    if not any(getattr(h, "_aeomirror", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._aeomirror = True  # type: ignore[attr-defined]
        root.addHandler(handler)


def log_json(logger: logging.Logger, level: int, **fields) -> None:
    """Emit one structured JSON log line."""
    try:
        logger.log(level, json.dumps(fields, default=str))
    except Exception:  # logging must never crash a request
        logger.log(level, str(fields))


# ----------------------------- metrics -----------------------------
class Metrics:
    """Thread-safe process-local counters. Reset on restart (durable counts come
    from the database in the debug endpoint)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_scans = 0        # successful scan responses (200)
        self.cache_hits = 0
        self.cache_misses = 0
        self.rate_limited_429 = 0
        self._latency_sum_ms = 0.0
        self._latency_count = 0

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + n)

    def observe_scan_latency(self, ms: float) -> None:
        with self._lock:
            self._latency_sum_ms += ms
            self._latency_count += 1

    def snapshot(self) -> dict:
        with self._lock:
            avg = self._latency_sum_ms / self._latency_count if self._latency_count else 0.0
            return {
                "total_scans": self.total_scans,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "rate_limited_429": self.rate_limited_429,
                "avg_scan_latency_ms": round(avg, 1),
            }


metrics = Metrics()


# ----------------------------- middleware -----------------------------
_req_logger = logging.getLogger("aeomirror.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, and logs a structured line with
    request_id, timestamp, endpoint, latency, and status code."""

    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            # Full stack trace stays server-side; never returned to the client.
            log_json(_req_logger, logging.ERROR, event="request_error",
                     request_id=rid, method=request.method, path=request.url.path,
                     latency_ms=latency_ms, exc=True)
            _req_logger.exception("request_error request_id=%s", rid)
            raise
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        log_json(_req_logger, logging.INFO, event="request", request_id=rid,
                 timestamp=time.time(), method=request.method, path=request.url.path,
                 status=response.status_code, latency_ms=latency_ms)
        response.headers["X-Request-ID"] = rid
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds production-safe response headers. HSTS is emitted only when
    ENABLE_HSTS is set (i.e. the app is served over HTTPS behind a proxy)."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if settings.enable_hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
