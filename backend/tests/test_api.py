"""Full API-path test. fetch() is patched (network is unavailable in CI/sandbox);
every other layer is real: SSRF, rate limit, cache, scoring, DB, serialization."""
import pytest
from fastapi.testclient import TestClient

import app.api.routes_scan as rs
from app.main import app
from app.scanner.models import PageBundle
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS, BAD_HTML, BAD_ROBOTS

client = TestClient(app)


async def fake_fetch_good(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True)

async def fake_fetch_bad(url):
    return PageBundle(url=url, html=BAD_HTML, robots_txt=BAD_ROBOTS,
                      llms_txt_present=False, sitemap_present=False)


def test_scan_endpoint_good(monkeypatch):
    monkeypatch.setattr(rs, "fetch", fake_fetch_good)
    r = client.post("/v1/scan", json={"url": "https://brewlab.io/guide"})
    assert r.status_code == 200
    d = r.json()
    assert d["ars"] >= 90
    assert d["domain"] == "brewlab.io"
    assert len(d["crawlers"]) == 4
    assert "scan_id" in d

def test_scan_endpoint_bad(monkeypatch):
    monkeypatch.setattr(rs, "fetch", fake_fetch_bad)
    r = client.post("/v1/scan", json={"url": "https://example-shop.com"})
    assert r.status_code == 200
    d = r.json()
    assert d["ars"] <= 35
    assert any("gptbot" in i["id"] for i in d["top_issues"])

def test_ssrf_blocks_localhost(monkeypatch):
    from app.core import ssrf
    monkeypatch.setattr(ssrf.settings, "allow_private_hosts", False)
    r = client.post("/v1/scan", json={"url": "http://localhost/admin"})
    assert r.status_code == 422

def test_get_scan_by_id(monkeypatch):
    monkeypatch.setattr(rs, "fetch", fake_fetch_good)
    created = client.post("/v1/scan", json={"url": "https://brewlab.io/x"}).json()
    got = client.get(f"/v1/scan/{created['scan_id']}")
    assert got.status_code == 200
    assert got.json()["ars"] == created["ars"]

def test_lead_capture():
    r = client.post("/v1/lead", json={"email": "ayush@nextdot.co.in", "url": "https://x.com"})
    assert r.status_code == 200 and r.json()["ok"] is True

def test_health():
    assert client.get("/health").json()["status"] == "ok"
