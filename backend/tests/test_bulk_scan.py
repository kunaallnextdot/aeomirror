"""Bulk background scan: a worker-level end-to-end test (MockTransport-style fake
fetch, 5 URLs incl. one failing) asserting progress + aggregate + status transitions,
plus API tests for the Free single-domain rule, one-scan-job metering, and the
one-time bulk-trial enforcement."""
import asyncio

import app.scanner.bulk as bulk_mod
from app.config import settings
from app.db.models import SCAN_COMPLETED, SCAN_FAILED, SCAN_PENDING, SCAN_RUNNING, Scan
from app.db.session import SessionLocal
from app.main import app
from app.monitoring import worker
from app.scanner.models import PageBundle
from fastapi.testclient import TestClient
from tests.authutil import auth_client
from tests.test_monitoring import good_bundle, set_fetch

anon = TestClient(app)

BULK_URLS = [f"https://site.test/{p}" for p in ("a", "b", "c", "d", "bad")]


def _fake_fetch_factory(status_probe=None):
    """Fake bulk fetch: good_bundle for every URL except '/bad' (a 404). Optionally
    records the scan's committed status on each call so a test can prove it ran."""
    async def _fake(url, *, transport=None):
        if status_probe is not None:
            probe = SessionLocal()
            try:
                row = (probe.query(Scan).filter(Scan.url == BULK_URLS[0])
                       .order_by(Scan.created_at.desc()).first())
                if row:
                    status_probe.append(row.status)
            finally:
                probe.close()
        if url.endswith("/bad"):
            return PageBundle(url=url, html="", status_code=404)
        return good_bundle(url)
    return _fake


def test_worker_runs_bulk_scan_pending_running_completed(monkeypatch):
    probe: list[str] = []
    monkeypatch.setattr(bulk_mod, "fetch", _fake_fetch_factory(probe))
    client, _ = auth_client(organization_name="Bulk Org")

    r = client.post("/v1/scan/bulk", json={"urls": BULK_URLS})
    assert r.status_code == 202, r.text
    body = r.json()
    sid = body["scan_id"]
    assert body["status"] == SCAN_PENDING
    assert body["summary"]["accepted"] == 5

    assert client.get(f"/api/scans/{sid}/status").json()["status"] == SCAN_PENDING

    res = asyncio.run(worker.tick())
    assert res["processed"] >= 1

    st = client.get(f"/api/scans/{sid}/status").json()
    assert st["status"] == SCAN_COMPLETED
    assert st["progress"] == {"total": 5, "done": 4, "failed": 1, "current_url": None}
    assert SCAN_RUNNING in probe                      # entered RUNNING mid-fetch

    detail = client.get(f"/api/scans/{sid}").json()
    b = detail["bulk"]
    assert b["page_count"] == 4 and b["requested"] == 5     # /bad excluded
    assert b["avg_score"] is not None
    assert b["best"] and b["worst"]
    assert any("error" in p for p in b["pages"])            # /bad recorded as error
    assert detail["overall_score"] == b["avg_score"]        # headline = average


def test_bulk_requires_signed_in_account():
    r = anon.post("/v1/scan/bulk", json={"urls": ["https://x.example/"]})
    assert r.status_code == 401


def test_bulk_empty_or_invalid_list_is_422():
    client, _ = auth_client()
    r = client.post("/v1/scan/bulk", json={"urls": ["not a url", ""]})
    assert r.status_code == 422


def test_free_bulk_must_be_single_domain(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, _ = auth_client()
    r = client.post("/v1/scan/bulk", json={"urls": ["https://a.invalid/", "https://b.invalid/"]})
    assert r.status_code == 422
    assert "one website at a time" in r.json()["detail"].lower()


def test_free_bulk_consumes_one_scan_job_and_trial_is_one_time(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, _ = auth_client()

    # First bulk (Free): single domain → uses the one-time trial + exactly one scan job.
    r1 = client.post("/v1/scan/bulk", json={"urls": ["https://a.invalid/", "https://a.invalid/x"]})
    assert r1.status_code == 202, r1.text
    usage = client.get("/billing/subscription").json()["usage"]
    assert usage["scans"]["used"] == 1                       # one scan job consumed
    assert usage["bulk_trial"]["available"] is False         # trial consumed

    # Second bulk (Free): trial already used → 402 upgrade.
    r2 = client.post("/v1/scan/bulk", json={"urls": ["https://a.invalid/"]})
    assert r2.status_code == 402
    assert "bulk trial" in r2.json()["detail"].lower()


# ------------------------------- per-page detail gating (Pro) -------------------------------
def _run_free_bulk(monkeypatch, urls):
    """Create + complete a bulk scan for a fresh FREE org (billing enforced). Returns
    (client, scan_id)."""
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setattr(bulk_mod, "fetch", _fake_fetch_factory())
    client, _ = auth_client()
    r = client.post("/v1/scan/bulk", json={"urls": urls})
    assert r.status_code == 202, r.text
    sid = r.json()["scan_id"]
    asyncio.run(worker.tick())
    return client, sid


def test_free_bulk_page_details_are_locked(monkeypatch):
    client, sid = _run_free_bulk(monkeypatch, [f"https://site.test/{p}" for p in ("a", "b", "c")])
    body = client.get(f"/api/scans/{sid}").json()
    b = body["bulk"]
    assert b["details_locked"] is True
    for p in b["pages"]:
        assert "overall_score" in p and "status_label" in p     # scores + labels stay
        assert "sections_summary" not in p                      # detail stripped
    assert b["avg_score"] is not None
    assert body["sections"] == []                               # sample-page detail also stripped


def test_pro_bulk_page_details_are_full(monkeypatch):
    client, sid = _run_free_bulk(monkeypatch, [f"https://site.test/{p}" for p in ("a", "b")])
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})   # upgrade to Pro
    b = client.get(f"/api/scans/{sid}").json()["bulk"]
    assert not b.get("details_locked")
    assert any(p.get("sections_summary") for p in b["pages"] if "error" not in p)


def test_free_single_page_scan_keeps_full_sections(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    set_fetch(monkeypatch, good_bundle)
    client, _ = auth_client()
    sid = client.post("/v1/scan", json={"url": "https://single.example/"}).json()["scan_id"]
    body = client.get(f"/api/scans/{sid}").json()
    assert body["bulk"] is None
    assert len(body["sections"]) == 10          # single-page detail preserved for Free


def test_bulk_scan_of_another_org_is_404(monkeypatch):
    client_a, sid = _run_free_bulk(monkeypatch, ["https://site.test/x", "https://site.test/y"])
    client_b, _ = auth_client()
    assert client_b.get(f"/api/scans/{sid}").status_code == 404   # direct-object access blocked


def test_failed_bulk_scan_surfaces_error_message():
    """A FAILED bulk scan's user-facing error string reaches the client via
    GET /api/scans/{id} (the ScanResponse.error field), not a generic fallback."""
    client, obody = auth_client(organization_name="Failed Bulk Org")
    oid = obody["organization"]["id"]
    msg = "The bulk scan could not be completed. Please try again."
    db = SessionLocal()
    try:
        row = Scan(url="https://failbulk.example/", normalized_url="failbulk.example",
                   ars=0, rubric_version="t", status=SCAN_FAILED,
                   result={"error": msg, "bulk": {"urls": []}}, organization_id=oid)
        db.add(row); db.commit(); db.refresh(row)
        sid = row.id
    finally:
        db.close()
    body = client.get(f"/api/scans/{sid}").json()
    assert body["status"] == SCAN_FAILED
    assert body["error"] == msg
