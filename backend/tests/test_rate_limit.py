"""Rate-limiter tests: in-memory window semantics, the API 429 + Retry-After
response (which is what triggers the frontend email gate), and — against a real
local Redis when available — shared state across multiple limiter instances."""
import os

import pytest
from fastapi.testclient import TestClient

import app.api.routes_scan as rs
import app.core.cache as cache_mod
from app.core.cache import RateLimiter, rate_limiter
from app.main import app
from app.scanner.models import PageBundle
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

client = TestClient(app)


async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True)


def test_within_limit_passes_then_blocks():
    rl = RateLimiter(limit=2, window=100)
    assert rl.check("k")[0] is True
    assert rl.check("k")[0] is True
    allowed, remaining, retry_after = rl.check("k")
    assert allowed is False
    assert remaining == 0
    assert retry_after > 0


def test_window_expiry_resets_limit():
    rl = RateLimiter(limit=1, window=100)
    assert rl.check("k")[0] is True
    assert rl.check("k")[0] is False
    # age the recorded hit beyond the window -> should reset
    rl._hits["k"] = [t - 200 for t in rl._hits["k"]]
    assert rl.check("k")[0] is True


def test_api_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    # tighten the shared limiter for this test and clear its state
    monkeypatch.setattr(rate_limiter, "limit", 2)
    rate_limiter._hits.clear()
    url = "https://ratelimit-example.com/"
    s1 = client.post("/v1/scan", json={"url": url})
    s2 = client.post("/v1/scan", json={"url": url})
    s3 = client.post("/v1/scan", json={"url": url})
    assert s1.status_code == 200 and s2.status_code == 200
    assert s3.status_code == 429
    assert "retry-after" in {k.lower() for k in s3.headers}
    assert int(s3.headers["retry-after"]) > 0
    # the 429 body is meaningful (drives the frontend email gate)
    assert "scan" in s3.json()["detail"].lower()
    rate_limiter._hits.clear()


def _real_redis_or_skip():
    url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")
    redis = pytest.importorskip("redis")
    try:
        c = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
        c.ping()
        return c
    except Exception:
        pytest.skip("no local Redis available for shared-state test")


def test_shared_state_across_instances_via_redis(monkeypatch):
    c = _real_redis_or_skip()
    monkeypatch.setattr(cache_mod, "get_redis", lambda: c)
    key = "sharedstate-test-key"
    c.delete(RateLimiter.PREFIX + key)
    # Two SEPARATE limiter instances == two app workers sharing one Redis.
    a = RateLimiter(limit=3, window=60)
    b = RateLimiter(limit=3, window=60)
    assert a.check(key)[0] is True   # 1
    assert b.check(key)[0] is True   # 2 (other instance)
    assert a.check(key)[0] is True   # 3
    assert b.check(key)[0] is False  # 4 -> shared counter exhausted
    c.delete(RateLimiter.PREFIX + key)
