"""Fix Verification V2 — persisted verification history.

Two layers: the pure comparison engine (app/services/verification.py — a 1:1 port
of the existing frontend verification.js, tested here for the same state-machine
matrix that module's own tests cover) and the persistence/API layer built around it
(POST/GET /api/verifications) — organization isolation, scan ownership, quota reuse,
and reload persistence. No scanner/scoring change anywhere in this file."""
import app.billing.plans as plans_mod
from app.config import settings
from app.db.models import Scan, Verification
from app.db.session import SessionLocal
from app.scanner.models import PageBundle
from app.services.verification import verification_status, verify_signal
from sqlalchemy.orm.attributes import flag_modified
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS

import app.api.routes_scan as rs


# ===================================================================
# Pure engine — same state-machine table verification.js's own tests assert
# ===================================================================
def test_fail_to_pass_no_remaining_is_verified():
    assert verification_status(status_before="fail", status_after="pass",
                               resolved_count=1, remaining_count=0, new_count=0) == "verified"


def test_warn_to_pass_no_remaining_is_verified():
    assert verification_status(status_before="warn", status_after="pass",
                               resolved_count=1, remaining_count=0, new_count=0) == "verified"


def test_fail_to_warn_is_partially_improved_never_verified():
    assert verification_status(status_before="fail", status_after="warn",
                               resolved_count=1, remaining_count=1, new_count=0) == "partially_improved"


def test_warn_to_warn_with_improvement_is_partially_improved():
    assert verification_status(status_before="warn", status_after="warn",
                               resolved_count=1, remaining_count=1, new_count=0) == "partially_improved"


def test_fail_to_fail_same_issue_is_unchanged():
    assert verification_status(status_before="fail", status_after="fail",
                               resolved_count=0, remaining_count=1, new_count=0) == "unchanged"


def test_warn_to_fail_is_regressed():
    assert verification_status(status_before="warn", status_after="fail",
                               resolved_count=0, remaining_count=1, new_count=0) == "regressed"


def test_pass_to_fail_is_regressed():
    assert verification_status(status_before="pass", status_after="fail",
                               resolved_count=0, remaining_count=1, new_count=0) == "regressed"


def test_missing_status_is_not_comparable():
    assert verification_status(status_before=None, status_after="pass",
                               resolved_count=0, remaining_count=0, new_count=0) == "not_comparable"


def test_score_increase_alone_never_produces_verified():
    # FAIL -> PASS on the status word, but an issue is still listed as remaining —
    # a higher score/status tier is NEVER enough by itself.
    assert verification_status(status_before="fail", status_after="pass",
                               resolved_count=1, remaining_count=1, new_count=0) == "partially_improved"


def test_verify_signal_preserves_real_issue_and_evidence_text():
    before = {"sections": [{"id": "schema", "label": "Schema", "score": 40, "status": "fail",
                            "issues": ["Organization schema missing", "Breadcrumb schema missing"],
                            "evidence": {"organization_entity": "not_detected"}}]}
    after = {"sections": [{"id": "schema", "label": "Schema", "score": 91, "status": "pass",
                           "issues": ["Breadcrumb schema missing"],
                           "evidence": {"organization_entity": "detected"}}]}
    result = verify_signal("schema", before, after)
    assert result["resolved_issues"] == ["Organization schema missing"]
    assert result["remaining_issues"] == ["Breadcrumb schema missing"]
    assert result["verification_status"] == "partially_improved"   # one issue remains
    assert result["evidence_changes"] == [{"key": "organization_entity", "before": "not_detected", "after": "detected"}]
    assert result["score_delta"] == 51.0


def test_verify_signal_not_comparable_when_scan_not_completed():
    before = {"sections": [{"id": "schema", "score": 40, "status": "fail", "issues": []}]}
    after = {"sections": [{"id": "schema", "score": 90, "status": "pass", "issues": []}]}
    assert verify_signal("schema", before, after, before_status="pending") is None


def test_verify_signal_not_comparable_when_signal_missing():
    before = {"sections": [{"id": "schema", "score": 40, "status": "fail", "issues": []}]}
    after = {"sections": [{"id": "links", "score": 90, "status": "pass", "issues": []}]}
    assert verify_signal("schema", before, after) is None


# ===================================================================
# Persistence / API layer
# ===================================================================
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _set_signal(scan_id, signal_id, *, score, status, issues=None, evidence=None):
    """Directly overwrite one signal's score/status/issues/evidence on an already-
    persisted scan, so before/after can be controlled deterministically — the real
    scanner run (via _make_scan) still produced every OTHER field on the scan row."""
    db = SessionLocal()
    try:
        row = db.get(Scan, scan_id)
        sections = list(row.result.get("sections") or [])
        for s in sections:
            if s.get("id") == signal_id:
                s["score"] = score
                s["status"] = status
                s["issues"] = issues or []
                s["evidence"] = evidence or {}
        row.result = {**row.result, "sections": sections}
        flag_modified(row, "result")   # plain reassignment alone doesn't reliably
                                        # dirty-track this JSON column in this setup
        db.add(row)
        db.commit()
    finally:
        db.close()


def _enforce_billing(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", 50)


def test_create_verification_persists_and_returns_the_computed_result(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    s1 = _make_scan(client, monkeypatch, "https://verify1.example/")
    s2 = _make_scan(client, monkeypatch, "https://verify2.example/")
    _set_signal(s1, "schema", score=40, status="fail", issues=["Organization schema missing"])
    _set_signal(s2, "schema", score=91, status="pass", issues=[])

    res = client.post("/api/verifications", json={
        "baseline_scan_id": s1, "verification_scan_id": s2, "signal_id": "schema"})
    assert res.status_code == 200
    body = res.json()
    assert body["verification_status"] == "verified"
    assert body["score_before"] == 40 and body["score_after"] == 91
    assert body["score_delta"] == 51.0   # the API response must include the derived
                                          # delta, not just the two raw scores
    assert body["resolved_issues"] == ["Organization schema missing"]
    assert body["id"]

    db = SessionLocal()
    try:
        row = db.get(Verification, body["id"])
        assert row is not None and row.verification_status == "verified"
    finally:
        db.close()


def test_verification_reload_persistence_get_after_post(monkeypatch):
    """Simulates a page reload: a fresh GET (no state carried from the POST) still
    returns the same persisted result — no fake local-only state."""
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    s1 = _make_scan(client, monkeypatch, "https://reload1.example/")
    s2 = _make_scan(client, monkeypatch, "https://reload2.example/")
    _set_signal(s1, "schema", score=40, status="fail", issues=["x"])
    _set_signal(s2, "schema", score=91, status="pass", issues=[])
    client.post("/api/verifications", json={
        "baseline_scan_id": s1, "verification_scan_id": s2, "signal_id": "schema"})

    got = client.get(f"/api/verifications?scan_id={s1}")
    assert got.status_code == 200
    items = got.json()["verifications"]
    assert len(items) == 1
    assert items[0]["signal_id"] == "schema" and items[0]["verification_status"] == "verified"


def test_no_verification_record_means_empty_list_not_a_fake_status(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    s1 = _make_scan(client, monkeypatch, "https://none1.example/")
    got = client.get(f"/api/verifications?scan_id={s1}")
    assert got.status_code == 200
    assert got.json()["verifications"] == []


def test_organization_isolation_cannot_verify_against_another_orgs_scan(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, _ = auth_client(organization_name="Org A Verify")
    client_b, _ = auth_client(organization_name="Org B Verify")
    s1 = _make_scan(client_a, monkeypatch, "https://orga1.example/")
    s2 = _make_scan(client_a, monkeypatch, "https://orga2.example/")

    # Org B's client tries to verify using Org A's scans — both must 404, never leak.
    res = client_b.post("/api/verifications", json={
        "baseline_scan_id": s1, "verification_scan_id": s2, "signal_id": "schema"})
    assert res.status_code == 404


def test_baseline_scan_ownership_enforced_independently(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, _ = auth_client(organization_name="Org A Baseline")
    client_b, _ = auth_client(organization_name="Org B Baseline")
    foreign_scan = _make_scan(client_a, monkeypatch, "https://foreignbase.example/")
    own_scan = _make_scan(client_b, monkeypatch, "https://ownverify.example/")

    res = client_b.post("/api/verifications", json={
        "baseline_scan_id": foreign_scan, "verification_scan_id": own_scan, "signal_id": "schema"})
    assert res.status_code == 404


def test_verification_scan_ownership_enforced_independently(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, _ = auth_client(organization_name="Org A Verify2")
    client_b, _ = auth_client(organization_name="Org B Verify2")
    own_scan = _make_scan(client_b, monkeypatch, "https://ownbase.example/")
    foreign_scan = _make_scan(client_a, monkeypatch, "https://foreignverify.example/")

    res = client_b.post("/api/verifications", json={
        "baseline_scan_id": own_scan, "verification_scan_id": foreign_scan, "signal_id": "schema"})
    assert res.status_code == 404


def test_get_verifications_never_leaks_another_orgs_records(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, _ = auth_client(organization_name="Org A List")
    client_b, _ = auth_client(organization_name="Org B List")
    a1 = _make_scan(client_a, monkeypatch, "https://lista1.example/")
    a2 = _make_scan(client_a, monkeypatch, "https://lista2.example/")
    _set_signal(a1, "schema", score=40, status="fail", issues=["x"])
    _set_signal(a2, "schema", score=91, status="pass", issues=[])
    client_a.post("/api/verifications", json={
        "baseline_scan_id": a1, "verification_scan_id": a2, "signal_id": "schema"})

    # Org B cannot even resolve org A's scan_id to list against it.
    res = client_b.get(f"/api/verifications?scan_id={a1}")
    assert res.status_code == 404


def test_verification_preserves_the_existing_compare_quota(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client(organization_name="Quota Verify Co")
    s1 = _make_scan(client, monkeypatch, "https://quota1.example/")
    s2 = _make_scan(client, monkeypatch, "https://quota2.example/")
    payload = {"baseline_scan_id": s1, "verification_scan_id": s2, "signal_id": "schema"}

    assert client.post("/api/verifications", json=payload).status_code == 200   # Free: 1/month
    blocked = client.post("/api/verifications", json=payload)
    assert blocked.status_code == 402
    assert "1 comparison" in blocked.json()["detail"]


def test_not_comparable_scan_pair_returns_422_and_persists_nothing(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client(organization_name="NotComparable Co")
    s1 = _make_scan(client, monkeypatch, "https://nc1.example/")
    s2 = _make_scan(client, monkeypatch, "https://nc2.example/")
    # "not_a_real_signal" exists on neither scan -> not comparable.
    res = client.post("/api/verifications", json={
        "baseline_scan_id": s1, "verification_scan_id": s2, "signal_id": "not_a_real_signal"})
    assert res.status_code == 422
    assert client.get(f"/api/verifications?scan_id={s1}").json()["verifications"] == []
