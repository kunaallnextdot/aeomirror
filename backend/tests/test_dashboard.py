"""Phase 4 dashboard endpoint tests: list/detail/delete/rerun/summary. Phase 5
scopes scans to the caller's organization, so the shared client is authenticated
(Owner) and its scans accrue to one org."""
import pytest

import app.api.routes_scan as rs
from app.scanner.models import PageBundle
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

# Authenticated Owner client, created once the DB schema exists (see conftest).
# Not built at import time — that would run before the session schema fixture.
client = None


@pytest.fixture(scope="module", autouse=True)
def _owner_client():
    global client
    client, _ = auth_client(organization_name="Dashboard Test Org")
    yield


async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _scan(url):
    return client.post("/v1/scan", json={"url": url}).json()


def test_list_scans_returns_requester_scans_newest_first(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    a = _scan("https://dash-a.example/")
    b = _scan("https://dash-b.example/")
    rows = client.get("/api/scans").json()
    ids = [r["id"] for r in rows]
    assert a["scan_id"] in ids and b["scan_id"] in ids
    # b created after a -> appears earlier (newest first)
    assert ids.index(b["scan_id"]) < ids.index(a["scan_id"])
    item = next(r for r in rows if r["id"] == b["scan_id"])
    assert set(item) >= {"id", "url", "domain", "scan_time", "overall_score", "ars",
                         "duration_ms", "status", "scanner_version", "signal_scores"}
    assert item["signal_scores"]  # per-signal scores included


def test_list_scans_search_and_sort(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    _scan("https://uniquesearch-xyz.example/")
    hits = client.get("/api/scans", params={"q": "uniquesearch-xyz"}).json()
    assert hits and all("uniquesearch-xyz" in r["url"] for r in hits)
    asc = client.get("/api/scans", params={"sort": "oldest"}).json()
    times = [r["scan_time"] for r in asc if r["scan_time"]]
    assert times == sorted(times)


def test_get_scan_detail(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    created = _scan("https://dash-detail.example/")
    got = client.get(f"/api/scans/{created['scan_id']}").json()
    assert got["scan_id"] == created["scan_id"]
    assert len(got["sections"]) == 10


def test_delete_scan(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    created = _scan("https://dash-delete.example/")
    sid = created["scan_id"]
    d = client.delete(f"/api/scans/{sid}")
    assert d.status_code == 200 and d.json()["ok"] is True
    assert sid not in [r["id"] for r in client.get("/api/scans").json()]
    assert client.delete(f"/api/scans/{sid}").status_code == 404  # already gone


def test_rerun_creates_new_scan(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    created = _scan("https://dash-rerun.example/")
    re = client.post(f"/api/scans/{created['scan_id']}/rerun")
    assert re.status_code == 200
    body = re.json()
    assert body["scan_id"] != created["scan_id"]      # fresh row
    assert len(body["sections"]) == 10


def test_rerun_missing_scan_404():
    assert client.post("/api/scans/nope/rerun").status_code == 404


def test_dashboard_summary(monkeypatch):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    _scan("https://dash-summary.example/")
    d = client.get("/api/dashboard").json()
    assert d["total_scans"] >= 1
    assert d["average_score"] is not None
    assert d["highest_score"] >= d["lowest_score"]
    assert d["latest_scan"] and "overall_score" in d["latest_scan"]
    assert len(d["score_distribution"]) == 5
    assert isinstance(d["score_trend"], list)
    assert isinstance(d["top_issue_categories"], list)
