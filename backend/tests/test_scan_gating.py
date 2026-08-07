"""Free-first-scan gating + scan-job metering.

- A signed-out visitor can run single-page scans with NO login (bounded only by the
  per-IP abuse limiter); the "one free scan then sign up" gate is enforced per browser
  on the client, not per IP (a per-IP cap would wrongly block users behind a shared
  IP / NAT). A full-site scan always needs an account (401).
- Free gets 1 scan job / month (2nd → 402); Pro gets 15 (16th → 402).
- Scans executed automatically by monitors do NOT consume scan-job quota — only
  user-initiated (API path) scans do.
"""
import asyncio

from app.config import settings
from app.main import app
from app.monitoring import worker
from fastapi.testclient import TestClient
from tests.authutil import auth_client
from tests.test_monitoring import good_bundle, set_fetch

anon = TestClient(app)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_anonymous_single_scan_needs_no_login(monkeypatch):
    set_fetch(monkeypatch, good_bundle)
    r = anon.post("/v1/scan", json={"url": "https://anon-free.example/"})
    assert r.status_code == 200
    assert r.json()["scan_id"]


def test_anonymous_scans_not_hard_gated_server_side(monkeypatch):
    # The server does not cap anonymous scans at 1 (that gate is per-browser on the
    # client); a second anonymous scan still succeeds server-side.
    set_fetch(monkeypatch, good_bundle)
    assert anon.post("/v1/scan", json={"url": "https://a1.example/"}).status_code == 200
    assert anon.post("/v1/scan", json={"url": "https://a2.example/"}).status_code == 200


def test_bulk_scan_requires_account():
    r = anon.post("/v1/scan/bulk", json={"urls": ["https://anon-bulk.example/"]})
    assert r.status_code == 401


def test_free_second_scan_is_402(monkeypatch):
    """Free = 1 scan job / month: the first scan works, the second is 402 with the
    upgrade message. Uses the real default limit (no monkeypatched scan_limit)."""
    monkeypatch.setattr(settings, "billing_enforced", True)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()

    assert client.post("/v1/scan", json={"url": "https://free1.example/"}).status_code == 200
    blocked = client.post("/v1/scan", json={"url": "https://free2.example/"})
    assert blocked.status_code == 402
    assert "free scan is used" in blocked.json()["detail"].lower()
    assert "15 scan jobs" in blocked.json()["detail"]


def test_pro_sixteenth_scan_is_402(monkeypatch):
    """Pro = 15 scan jobs / month: 15 succeed, the 16th is 402."""
    monkeypatch.setattr(settings, "billing_enforced", True)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    _go_pro(client)

    for i in range(settings.pro_monthly_scan_jobs):        # 15 → all 200
        r = client.post("/v1/scan", json={"url": f"https://pro-{i}.example/"})
        assert r.status_code == 200, f"scan {i} -> {r.status_code}"
    blocked = client.post("/v1/scan", json={"url": "https://pro-16.example/"})
    assert blocked.status_code == 402
    assert "all 15 scan jobs" in blocked.json()["detail"]


def test_monitor_scans_do_not_consume_scan_job_quota(monkeypatch):
    """A monitor-triggered scan (worker tick) must NOT count against scan-job quota —
    the Free org keeps its 1 scan job even after the monitor has scanned."""
    monkeypatch.setattr(settings, "billing_enforced", True)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()

    # A daily monitor is due immediately; the worker enqueues + runs its scan.
    mid = client.post("/monitors", json={"url": "https://mon.example/", "frequency": "daily"}).json()["id"]
    res = asyncio.run(worker.tick())
    assert res["processed"] >= 1

    # The monitor scored a scan, but scan-job usage is still zero.
    usage = client.get("/billing/subscription").json()["usage"]
    assert usage["scans"]["used"] == 0
    m = client.get(f"/monitors/{mid}").json()["monitor"]
    assert m["latest_score"] is not None            # the monitor really did scan

    # The user's own free scan is still available (and, once used, counts as 1).
    assert client.post("/v1/scan", json={"url": "https://mine.example/"}).status_code == 200
    usage2 = client.get("/billing/subscription").json()["usage"]
    assert usage2["scans"]["used"] == 1
