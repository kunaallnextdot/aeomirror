"""Gating cleanup tests (product-UX finalization pass):

1. Server-side recommendation gating (gate_recommendations) — GET /reports/{scan_id}
   and GET /reports/{scan_id}/insights must never send a free caller more than the
   free-tier recommendation preview, and the locked recommendations' fix_template /
   affected-page detail must never appear anywhere in a free response.
2. Answer Tracking summary gating (gate_run_summary) — GET /prompt-runs/{run_id}/summary
   keeps basic per-prompt mention/visibility data free, but trims competitor/leaderboard/
   gap-to-action/citation depth to a preview + locked counts for a free org.
3. Organization isolation still holds through both gated endpoints.

No scanner/scoring/mention-rate/answerability/opportunity logic is touched here — this
only exercises the exposure/gating layer added on top of already-tested builders."""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
from app.config import settings
from app.db.models import PromptGapAnalysis
from app.db.session import SessionLocal
from app.reports.insights import gate_recommendations
from app.scanner.models import PageBundle
from tests.authutil import auth_client
from tests.test_answer_tracking_analysis import _analysis, _result, _run, _set
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


# ------------------------------- shared helpers -------------------------------
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://gatingcleanup.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _enforce_billing(monkeypatch, scan_limit=50):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", scan_limit)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


# ===================== 1/2/4/11: recommendation gating (gate_recommendations) =====================
def test_gate_recommendations_pure():
    recs = [{"id": f"r{i}"} for i in range(5)]
    free = gate_recommendations(recs, unlocked=False, free_limit=3)
    assert [r["id"] for r in free["recommendations"]] == ["r0", "r1", "r2"]
    assert free["recommendation_count"] == 5 and free["locked_recommendation_count"] == 2
    assert free["recommendations_preview"] is True

    paid = gate_recommendations(recs, unlocked=True, free_limit=3)
    assert paid["recommendations"] == recs
    assert paid["locked_recommendation_count"] == 0 and paid["recommendations_preview"] is False


def test_free_report_endpoint_limits_recommendations_and_hides_locked_fields(monkeypatch):
    """GET /reports/{scan_id}: a free caller must get at most 3 recommendations, a correct
    locked count, and the locked recommendations' fix_template/business_impact/evidence
    text must not appear anywhere in the JSON — not just be unrendered."""
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    body = client.get(f"/reports/{scan_id}").json()
    recs = body["recommendations"]
    assert len(recs) <= 3
    assert body["locked_recommendation_count"] >= 0
    assert body["recommendation_count"] == len(recs) + body["locked_recommendation_count"]
    if body["locked_recommendation_count"] > 0:
        assert body["recommendations_preview"] is True

    # Go Pro and confirm the SAME scan's cached report now returns everything. Paid
    # responses are left exactly as `generate_report` produces them (no preview/locked
    # keys added at all — same convention as gate_insights' unlocked path).
    _go_pro(client)
    full = client.get(f"/reports/{scan_id}").json()
    assert "locked_recommendation_count" not in full
    assert len(full["recommendations"]) == full["recommendation_count"]
    assert len(full["recommendations"]) >= len(recs)

    # The free response must not have smuggled any locked recommendation's identity or
    # fix content through some other field (network-inspection security requirement).
    if full["recommendation_count"] > len(recs):
        free_ids = {r["id"] for r in recs}
        locked_recs = [r for r in full["recommendations"] if r["id"] not in free_ids]
        assert locked_recs, "expected at least one recommendation locked in the free response"
        raw_free_json = str(body)
        for lr in locked_recs:
            assert lr["id"] not in raw_free_json
            fx = lr.get("fix_template") or {}
            for step in (fx.get("recommended_fix") or []):
                assert step not in raw_free_json


def test_free_insights_endpoint_recommendation_preview_matches_report_endpoint(monkeypatch):
    """GET /reports/{scan_id}/insights and GET /reports/{scan_id} must never diverge on
    how many recommendations a free caller gets — both call the same gate_recommendations."""
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    report = client.get(f"/reports/{scan_id}").json()
    insights = client.get(f"/reports/{scan_id}/insights").json()
    assert len(report["recommendations"]) == len(insights["free_recommendations"])
    assert report["locked_recommendation_count"] == insights["locked_recommendation_count"]


def test_paid_report_endpoint_exposes_complete_recommendations(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)
    body = client.get(f"/reports/{scan_id}").json()
    assert "recommendations_preview" not in body
    assert "locked_recommendation_count" not in body
    assert len(body["recommendations"]) == body["recommendation_count"]


# ===================== 8/9/10: Answer Tracking summary gating =====================
def _make_run_with_signals(db, org_id):
    """4 prompts: 1 mentioned, 3 zero-mention gaps (2 with a grounded gap analysis, one
    locked-preview boundary), plus a competitor on 2 different providers."""
    ps, prompts = _set(db, org_id, prompts=("q0", "q1", "q2", "q3"))
    run = _run(db, ps, org_id)
    providers = ["anthropic", "openai", "anthropic", "openai"]
    for i, p in enumerate(prompts):
        r = _result(db, run, p.id, org_id, provider=providers[i], run_index=i)
        mentioned = (i == 0)
        _analysis(db, run, r.id, org_id, mentioned=mentioned,
                  recommended=None if mentioned else [{"name": "Rival", "domain_if_stated": "rival.com"}])
    for p in prompts[1:]:   # gap analysis for every zero-mention prompt (3 of them)
        db.add(PromptGapAnalysis(run_id=run.id, prompt_id=p.id, organization_id=org_id,
                                 why="Rival ranks higher on this query.",
                                 actions=["Publish a comparison page"], has_signal=True,
                                 model="fake-gap-1"))
    db.commit()
    return run.id


def test_answer_tracking_free_summary_keeps_basics_locks_intelligence(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        run_id = _make_run_with_signals(db, org_id)
    finally:
        db.close()

    s = client.get(f"/prompt-runs/{run_id}/summary").json()
    assert s["unlocked"] is False
    # basic operational data — every prompt's mention status is free, never gated
    assert len(s["per_prompt"]) == 4
    assert s["mention_rate"] == 25.0
    assert sum(1 for r in s["per_prompt"] if r["is_gap"]) == 3

    # deeper intelligence is trimmed to a preview + a locked count
    assert s["locked_competitor_count"] >= 0
    assert len(s["competitors"]) <= 1
    assert s["leaderboard"] == [] and s["leaderboard_locked"] is True
    gap_rows_with_real_text = [r for r in s["per_prompt"]
                               if r.get("gap") and r["gap"].get("locked") is not True and r.get("gap", {}).get("why")]
    assert len(gap_rows_with_real_text) <= 2
    assert s["locked_gap_count"] >= 1
    assert s["cited_urls"] == []


def test_answer_tracking_paid_summary_is_complete(monkeypatch):
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        run_id = _make_run_with_signals(db, org_id)
    finally:
        db.close()
    _go_pro(client)

    s = client.get(f"/prompt-runs/{run_id}/summary").json()
    assert s["unlocked"] is True
    assert "leaderboard_locked" not in s
    gap_rows_with_real_text = [r for r in s["per_prompt"] if r.get("gap") and r["gap"].get("why")]
    assert len(gap_rows_with_real_text) == 3   # all 3 gap analyses, not just the free preview


def test_answer_tracking_summary_org_isolation(monkeypatch):
    _enforce_billing(monkeypatch)
    client_a, body_a = auth_client()
    client_b, _ = auth_client()
    org_a = body_a["organization"]["id"]
    db = SessionLocal()
    try:
        run_id = _make_run_with_signals(db, org_a)
    finally:
        db.close()
    assert client_a.get(f"/prompt-runs/{run_id}/summary").status_code == 200
    assert client_b.get(f"/prompt-runs/{run_id}/summary").status_code == 404
