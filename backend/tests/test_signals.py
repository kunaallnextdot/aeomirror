"""Phase 3 AI-visibility signal tests: per-signal shape, aggregate weighting,
API response shape, persistence roundtrip, and backward-compat with pre-Phase-3
stored scans."""
from fastapi.testclient import TestClient

import app.api.routes_scan as rs
from app.db.models import Scan
from app.db.session import SessionLocal
from app.main import app
from app.scanner.models import PageBundle
from app.scanner.signals.aggregate import SIGNALS, run_signals
from app.scanner.signals.base import SignalContext, SignalResult
import pytest

from tests.authutil import authenticate
from tests.test_scanner import BAD_HTML, BAD_ROBOTS, GOOD_HTML, GOOD_ROBOTS

# Scanning requires an account; billing off in tests so scans never gate on quota.
client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _auth_client():
    authenticate(client)

GOOD_SITEMAP = '<?xml version="1.0"?><urlset><url><loc>https://good.com/</loc></url></urlset>'
GOOD_HEADERS = {"content-encoding": "gzip", "cache-control": "max-age=3600",
                "last-modified": "Wed, 01 Jul 2026 00:00:00 GMT"}


def good_page():
    return PageBundle(url="https://good.com/g", html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True, sitemap_xml=GOOD_SITEMAP,
                      headers=GOOD_HEADERS)


def bad_page():
    return PageBundle(url="https://bad.com/", html=BAD_HTML, robots_txt=BAD_ROBOTS,
                      llms_txt_present=False, sitemap_present=False)


async def _fake_fetch_good(url):
    return good_page()


def test_every_signal_returns_uniform_shape():
    ctx = SignalContext(good_page())
    for module in SIGNALS:
        r = module.analyze(ctx)
        assert isinstance(r, SignalResult)
        assert r.id and r.label
        assert 0 <= r.score <= 100
        assert r.status in ("pass", "warn", "fail")
        assert r.weight > 0
        assert isinstance(r.issues, list) and isinstance(r.recommendations, list)
        assert isinstance(r.evidence, dict)


def test_aggregate_weights_sum_to_100_and_overall_in_range():
    rep = run_signals(good_page())
    assert rep["scanner_version"]
    assert len(rep["sections"]) == 10
    assert sum(s["weight"] for s in rep["sections"]) == 100
    assert 0 <= rep["overall_score"] <= 100


def test_good_page_scores_higher_than_bad_page():
    good = run_signals(good_page())["overall_score"]
    bad = run_signals(bad_page())["overall_score"]
    assert good > bad


def test_bad_page_flags_blocked_ai_crawlers():
    rep = run_signals(bad_page())
    robots = next(s for s in rep["sections"] if s["id"] == "robots")
    assert robots["status"] in ("warn", "fail")
    assert robots["issues"]  # something to report


def test_scan_response_includes_signal_report(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch_good)
    d = client.post("/v1/scan", json={"url": "https://signals-example.com/"}).json()
    assert d["scanner_version"]
    assert isinstance(d["overall_score"], int)
    assert d["scanned_at"] and isinstance(d["duration_ms"], int)
    assert len(d["sections"]) == 10
    section = d["sections"][0]
    assert set(section) >= {"id", "label", "score", "status", "weight",
                            "issues", "recommendations", "evidence"}
    # legacy fields still present (no breaking change)
    assert "ars" in d and "families" in d and "crawlers" in d


def test_get_scan_returns_sections(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch_good)
    created = client.post("/v1/scan", json={"url": "https://signals-roundtrip.com/"}).json()
    # Org-owned scan → read via the authenticated detail endpoint.
    got = client.get(f"/api/scans/{created['scan_id']}").json()
    assert len(got["sections"]) == 10
    assert got["overall_score"] == created["overall_score"]


def test_pre_phase3_scan_without_sections_still_loads():
    # Simulate a scan stored before Phase 3 (result has no sections key).
    db = SessionLocal()
    try:
        row = Scan(url="https://legacy.com/", normalized_url="legacy.com",
                   ars=50, rubric_version="2026.07.1",
                   result={"families": [], "top_issues": [], "crawlers": []})
        db.add(row)
        db.commit()
        db.refresh(row)
        sid = row.id
    finally:
        db.close()
    got = client.get(f"/v1/scan/{sid}")
    assert got.status_code == 200
    body = got.json()
    assert body["sections"] == [] and body["overall_score"] is None
