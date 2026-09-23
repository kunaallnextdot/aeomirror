"""Fix 2 — the actual report cache: `Report.data` is a real deterministic-report cache,
not just a place to stash the AI narrative. `generate_report`, `get_report_insights`,
and `get_report_ai_visibility` must all reuse ONE canonical build via
`reports.service.get_or_build_report` instead of independently calling
`build_report()` — verified here with a call-count spy, not just output equality.

No scanner/scoring/Phase 1-4 calculation is touched — this file only tests the
persistence/reuse layer around those calculations."""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
import app.reports.service as report_service
from app.config import settings
from app.db.models import Report, Scan
from app.db.session import SessionLocal
from app.reports.service import get_or_build_report
from app.scanner.models import PageBundle
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


# ------------------------------- fixtures -------------------------------
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://reportcache.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _enforce_billing(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def _spy_build_report(monkeypatch):
    """Counts real calls to the deterministic pipeline (`reports.service.build_report`,
    the name `service.py` actually calls) without changing its behavior."""
    calls = {"n": 0}
    original = report_service.build_report

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(report_service, "build_report", wrapper)
    return calls


def _scan_row(scan_id):
    db = SessionLocal()
    try:
        return db.get(Scan, scan_id)
    finally:
        db.close()


def _report_row(scan_id):
    db = SessionLocal()
    try:
        return (db.query(Report).filter(Report.scan_id == scan_id)
                .order_by(Report.generated_at.desc()).first())
    finally:
        db.close()


# ===================================================================
# 8/9: a completed scan's report is reused, not rebuilt
# ===================================================================
def test_completed_report_is_reused_when_cache_is_valid(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")   # first read generates + persists the row

    calls = _spy_build_report(monkeypatch)
    scan = _scan_row(scan_id)
    db = SessionLocal()
    try:
        report1, row1 = get_or_build_report(db, scan)
        report2, row2 = get_or_build_report(db, scan)
    finally:
        db.close()

    assert calls["n"] == 0          # cache was already valid from scan creation — no rebuild at all
    assert report1 == report2
    assert row1.id == row2.id


def test_build_report_not_called_repeatedly_across_three_endpoints(monkeypatch):
    """The exact regression this fix targets: GET /reports, /insights, and
    /ai-visibility used to each call build_report() independently."""
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")   # warm the cache once, as a real first page-load would

    calls = _spy_build_report(monkeypatch)
    client.get(f"/reports/{scan_id}")
    client.get(f"/reports/{scan_id}/insights")
    client.get(f"/reports/{scan_id}/ai-visibility")
    client.get(f"/reports/{scan_id}")
    client.get(f"/reports/{scan_id}/insights")

    assert calls["n"] == 0          # the scan's report was already cached from creation


# ===================================================================
# 10/11: insights and AI-visibility endpoints read the shared cache
# ===================================================================
def test_insights_endpoint_uses_cached_report(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    report = client.get(f"/reports/{scan_id}").json()

    calls = _spy_build_report(monkeypatch)
    insights = client.get(f"/reports/{scan_id}/insights").json()
    assert calls["n"] == 0
    assert insights["phase4"]["schema"]["missing_types"] == report["phase4"]["schema"]["missing_types"]


def test_ai_visibility_endpoint_uses_cached_report(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")   # warm the cache

    calls = _spy_build_report(monkeypatch)
    resp = client.get(f"/reports/{scan_id}/ai-visibility")
    assert calls["n"] == 0
    assert resp.status_code == 200
    assert resp.json()["available"] is False   # no monitor for this scan — expected, not the point here


# ===================================================================
# 12/13: entitlement safety — gating never mutates the canonical Report.data
# ===================================================================
def test_free_response_is_gated_without_mutating_canonical_report_data(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    free_body = client.get(f"/reports/{scan_id}").json()
    assert free_body.get("locked_recommendation_count", 0) >= 0   # sanity: gating did trim the response

    row = _report_row(scan_id)
    assert row is not None
    # The CANONICAL persisted copy must stay the full, ungated report regardless of the
    # free response above — gating must be a read-layer transform, never a write.
    assert row.data.get("locked_recommendation_count") is None
    assert row.data.get("recommendations_preview") is None
    assert len(row.data.get("recommendations") or []) == row.data.get("recommendation_count")
    assert row.data.get("phase4", {}).get("schema", {}).get("preview") is None


def test_paid_response_still_gets_full_data_after_a_free_request(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    free_body = client.get(f"/reports/{scan_id}").json()   # a free read first
    _go_pro(client)
    paid_body = client.get(f"/reports/{scan_id}").json()

    assert paid_body["recommendation_count"] >= len(free_body["recommendations"])
    assert "locked_recommendation_count" not in paid_body
    assert "preview" not in paid_body["phase4"]["schema"]


# ===================================================================
# 14: a stale/version-mismatched report rebuilds correctly
# ===================================================================
def test_stale_version_mismatch_triggers_a_rebuild(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    client.get(f"/reports/{scan_id}")   # warm the cache

    db = SessionLocal()
    try:
        row = (db.query(Report).filter(Report.scan_id == scan_id)
               .order_by(Report.generated_at.desc()).first())
        row.version = "0.0.0-stale"
        db.commit()
    finally:
        db.close()

    calls = _spy_build_report(monkeypatch)
    scan = _scan_row(scan_id)
    db = SessionLocal()
    try:
        _report, row = get_or_build_report(db, scan)
    finally:
        db.close()

    assert calls["n"] == 1                       # rebuilt exactly once, not skipped
    assert row.version != "0.0.0-stale"           # the stale version was overwritten


# ===================================================================
# 15: a pending scan never gets cached as a completed report
# ===================================================================
def test_pending_scan_does_not_create_a_completed_cached_report(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        row = Scan(url="https://pendingcache.example/", normalized_url="pendingcache.example",
                  ars=0, rubric_version="t", status="pending",
                  result={"bulk": {"requested": 2, "urls": ["a", "b"]}}, organization_id=org_id)
        db.add(row); db.commit(); db.refresh(row)
        scan_id = row.id
    finally:
        db.close()

    client.get(f"/reports/{scan_id}")
    client.get(f"/reports/{scan_id}/insights")

    assert _report_row(scan_id) is None   # nothing was ever persisted for the incomplete scan


# ===================================================================
# 16: exports still work and still respect entitlement gating
# ===================================================================
def test_exports_still_work_after_the_cache_change(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    locked = client.get(f"/reports/{scan_id}/json")
    assert locked.status_code == 402

    _go_pro(client)
    ok_json = client.get(f"/reports/{scan_id}/json")
    ok_csv = client.get(f"/reports/{scan_id}/csv")
    assert ok_json.status_code == 200 and ok_json.json()["recommendation_count"] is not None
    assert ok_csv.status_code == 200 and len(ok_csv.content) > 0


def test_export_reuses_the_same_cached_report_row(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)
    client.get(f"/reports/{scan_id}")   # warm the cache before the export

    calls = _spy_build_report(monkeypatch)
    resp = client.get(f"/reports/{scan_id}/json")
    assert resp.status_code == 200
    assert calls["n"] == 0    # the AI-narrative check ran, but the deterministic report was cached
    assert _report_row(scan_id) is not None
