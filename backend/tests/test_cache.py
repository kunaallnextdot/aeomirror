"""Cache tests: in-memory behavior, fail-open on Redis error, and the API-level
guarantee that identical URLs are served from cache without refetching — and that
a Redis outage never fabricates scanner results."""
from fastapi.testclient import TestClient

import app.api.routes_scan as rs
import app.core.cache as cache_mod
from app.core.cache import TTLCache
from app.main import app
from app.scanner.models import PageBundle
import pytest

from tests.authutil import authenticate
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

# Scanning requires an account now; billing is off in tests so scans never gate.
client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _auth_client():
    authenticate(client)


def _good_fetch_factory(counter):
    async def fake_fetch(url):
        counter["n"] += 1
        return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                          llms_txt_present=True, sitemap_present=True)
    return fake_fetch


def test_inmemory_set_get_roundtrip():
    c = TTLCache(ttl=100)
    c.set("key", {"scan_id": "x", "ars": 50})
    assert c.get("key") == {"scan_id": "x", "ars": 50}


def test_inmemory_expired_entry_is_unavailable():
    c = TTLCache(ttl=-1)  # already expired the moment it is set
    c.set("key", {"a": 1})
    assert c.get("key") is None


def test_missing_key_returns_none():
    assert TTLCache(ttl=100).get("absent") is None


class _BrokenRedis:
    def get(self, *a, **k):
        raise RuntimeError("redis down")

    def set(self, *a, **k):
        raise RuntimeError("redis down")


def test_cache_fails_open_on_redis_error(monkeypatch):
    monkeypatch.setattr(cache_mod, "get_redis", lambda: _BrokenRedis())
    c = TTLCache(ttl=100)
    c.set("key", {"a": 1})        # must not raise
    assert c.get("key") is None   # fail open — never a fabricated value


def test_identical_url_served_from_cache_no_refetch(monkeypatch):
    counter = {"n": 0}
    monkeypatch.setattr(rs, "fetch", _good_fetch_factory(counter))
    url = "https://cache-dedupe-example.com/page"
    r1 = client.post("/v1/scan", json={"url": url}).json()
    r2 = client.post("/v1/scan", json={"url": url}).json()
    # The 2nd request is a cache hit: it did NOT refetch, but a signed-in caller still
    # gets their own org-owned row (a distinct id) with identical scored data.
    assert counter["n"] == 1
    assert r1["scan_id"] != r2["scan_id"]
    assert r1["ars"] == r2["ars"] and r1["domain"] == r2["domain"]


def test_cache_hit_scan_keeps_rubric_version_id(monkeypatch):
    """A cache-hit scan row must keep its rubric linkage (rubric_version_id) — matching
    the fresh scan's value, never NULL."""
    from app.db.models import Scan
    from app.db.session import SessionLocal
    counter = {"n": 0}
    monkeypatch.setattr(rs, "fetch", _good_fetch_factory(counter))
    url = "https://cache-rubric-example.com/page"
    r1 = client.post("/v1/scan", json={"url": url}).json()
    r2 = client.post("/v1/scan", json={"url": url}).json()
    assert counter["n"] == 1                       # 2nd request was a cache hit
    assert r1["scan_id"] != r2["scan_id"]
    db = SessionLocal()
    try:
        row1 = db.get(Scan, r1["scan_id"])
        row2 = db.get(Scan, r2["scan_id"])
        assert row1.rubric_version_id is not None
        assert row2.rubric_version_id is not None    # was NULL before the fix
        assert row2.rubric_version_id == row1.rubric_version_id
    finally:
        db.close()


def test_scan_returns_real_data_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(cache_mod, "get_redis", lambda: _BrokenRedis())
    counter = {"n": 0}
    monkeypatch.setattr(rs, "fetch", _good_fetch_factory(counter))
    r = client.post("/v1/scan", json={"url": "https://redis-down-example.com/"})
    assert r.status_code == 200
    d = r.json()
    assert d["ars"] >= 90                        # real score from the scorer
    assert d["domain"] == "redis-down-example.com"
    assert d.get("_mock", False) is False        # never fabricated
