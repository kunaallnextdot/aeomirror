"""Observability + ops tests: readiness, security headers, request id, the admin
metrics/scan-counter endpoint and its gate, and metric increments."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.routes_scan as rs
from app.api.routes_misc import require_admin
from app.config import settings
from app.core.cache import rate_limiter
from app.main import app
from app.scanner.models import PageBundle
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

client = TestClient(app)


async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True)


def test_ready_endpoint():
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_security_headers_and_request_id():
    r = client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert r.headers.get("X-Request-ID")  # stamped by the logging middleware


def test_debug_metrics_accessible_in_test_env():
    # ENVIRONMENT=test (not production) and no ADMIN_TOKEN -> allowed.
    r = client.get("/debug/metrics")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"metrics", "scans", "active_rubric_version"}
    assert set(body["metrics"]) >= {
        "total_scans", "cache_hits", "cache_misses",
        "rate_limited_429", "avg_scan_latency_ms"}
    assert set(body["scans"]) == {"today", "total"}


def test_admin_gate_blocks_prod_without_token(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_token", None)
    with pytest.raises(HTTPException) as e:
        require_admin(x_admin_token=None)
    assert e.value.status_code == 404


def test_admin_gate_token_required_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_token", "secret")
    require_admin(x_admin_token="secret")  # correct token -> no raise
    with pytest.raises(HTTPException):
        require_admin(x_admin_token="wrong")


def test_scan_increments_metrics(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    before = client.get("/debug/metrics").json()["metrics"]["total_scans"]
    client.post("/v1/scan", json={"url": "https://metrics-example.com/unique-path"})
    after = client.get("/debug/metrics").json()["metrics"]["total_scans"]
    assert after >= before + 1


def test_429_increments_rate_limited_metric(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    monkeypatch.setattr(rate_limiter, "limit", 1)
    rate_limiter._hits.clear()
    before = client.get("/debug/metrics").json()["metrics"]["rate_limited_429"]
    client.post("/v1/scan", json={"url": "https://rl-metric.example/"})
    client.post("/v1/scan", json={"url": "https://rl-metric.example/"})  # -> 429
    after = client.get("/debug/metrics").json()["metrics"]["rate_limited_429"]
    assert after >= before + 1
    rate_limiter._hits.clear()
