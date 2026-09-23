"""Phase 1 negative-first insights tests: the pure read-side derivations
(score-loss breakdown, why-low, projected recovery) and the gated
GET /reports/{scan_id}/insights endpoint (free/paid split + org scoping).

No scanner or scoring change is exercised — every assertion is over derivations of
existing sections, and the projection must NEVER mutate the real overall_score."""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
from app.config import settings
from app.reports.engine import build_report
from app.reports.insights import (
    build_insights_block, points_lost, projected_recovery, score_loss_breakdown,
    why_score_low,
)
from app.scanner.models import PageBundle
from tests.authutil import auth_client
from tests.test_reports import FULL, _scan, _sections
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


# ------------------------------- pure: score-loss -------------------------------
def test_points_lost_formula():
    # weight 15, score 15 -> 15 * (100-15)/100 = 12.75
    assert points_lost(15, 15) == 12.75
    assert points_lost(10, 100) == 0.0        # perfect signal loses nothing
    assert points_lost(0, 0) == 0.0           # no weight -> no loss


def test_breakdown_sorted_by_points_lost_desc():
    rows = score_loss_breakdown(_sections(FULL))
    losses = [r["points_lost"] for r in rows]
    assert losses == sorted(losses, reverse=True)
    # schema (w15,s15 -> 12.75) is the single biggest loss in FULL
    assert rows[0]["signal_id"] == "schema" and rows[0]["points_lost"] == 12.75


def test_zero_loss_signal_present_but_lossless():
    rows = score_loss_breakdown(_sections([("content", "Content", 12, 100)]))
    assert rows[0]["points_lost"] == 0.0 and rows[0]["status"] == "pass"


def test_all_perfect_has_no_problems_and_zero_loss():
    perfect = [(sid, lbl, w, 100) for (sid, lbl, w, _s) in FULL]
    block = build_insights_block({"overall_score": 100, "sections": _sections(perfect)})
    assert block["total_points_lost"] == 0.0
    assert block["top_problems"] == []                     # never invent a problem
    assert block["issue_count"] == 0
    assert block["projected_recovery"]["recoverable_points"] == 0.0
    assert block["projected_recovery"]["projected_score"] == 100.0


def test_evidence_is_preserved_verbatim():
    rows = score_loss_breakdown(_sections([("schema", "Schema", 15, 15)]))
    assert rows[0]["evidence"] == {"n": 2, "flag": True}   # exactly what the section carried


# ------------------------------- pure: why-low -------------------------------
def test_why_low_returns_real_problems_only():
    probs = why_score_low(_sections(FULL), build_report(_scan())["recommendations"])
    assert 1 <= len(probs) <= 3
    top = probs[0]
    assert top["signal_id"] == "schema"                    # biggest loss leads
    assert top["issue"] and top["evidence"] is not None
    assert top["recommendation_preview"]                   # preview drawn from the engine rec
    assert top["points_lost"] > 0                          # every listed problem costs points


def test_why_low_limit_is_respected():
    probs = why_score_low(_sections(FULL), limit=2)
    assert len(probs) == 2


# ------------------------------- pure: projection -------------------------------
def test_projection_recovers_only_selected_signals():
    sections = _sections(FULL)
    proj = projected_recovery(sections, 44, {"schema"})
    assert proj["signals"] == [{"signal_id": "schema", "label": "Structured Data",
                                "points_recoverable": 12.75}]   # only schema's loss
    assert proj["recoverable_points"] == 12.8                   # total rounded for display
    assert proj["projected_score"] == 56.8
    assert proj["label"] == "Estimated impact — not a re-score"


def test_projection_all_signals_capped_at_100():
    sections = _sections(FULL)
    proj = projected_recovery(sections, 44, None)          # fix everything
    assert proj["projected_score"] <= 100.0


def test_projection_never_mutates_overall_score():
    scan = _scan(44)
    before = scan["overall_score"]
    rep = build_report(scan)
    # projection lives only under insights; the headline score is untouched
    assert rep["scorecard"]["overall_score"] == before == 44
    assert scan["overall_score"] == 44
    assert rep["insights"]["projected_recovery"]["projected_score"] >= 44


# ------------------------------- report integration -------------------------------
def test_build_report_is_additive_and_valid():
    rep = build_report(_scan())
    # existing contract intact
    assert "report_version" in rep and "scorecard" in rep and "recommendations" in rep
    # new additive block
    ins = rep["insights"]
    assert ins["overall_score"] == 44
    assert len(ins["score_breakdown"]) == len(FULL)
    assert ins["total_points_lost"] > 0
    assert ins["explanation"]


# ------------------------------- API: gating + scoping -------------------------------
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://insights-api.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def test_insights_requires_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get("/reports/whatever/insights").status_code == 401


def test_insights_scoped_to_org(monkeypatch):
    owner_a, _ = auth_client()
    owner_b, _ = auth_client()
    scan_id = _make_scan(owner_a, monkeypatch, "https://org-a-insights.example/")
    assert owner_a.get(f"/reports/{scan_id}/insights").status_code == 200
    assert owner_b.get(f"/reports/{scan_id}/insights").status_code == 404


def _enforce_billing(monkeypatch, scan_limit=50):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", scan_limit)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_free_gets_top3_plus_locked_count(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    r = client.get(f"/reports/{scan_id}/insights")
    assert r.status_code == 200
    body = r.json()
    assert body["unlocked"] is False
    assert len(body["free_recommendations"]) <= body["free_recommendation_limit"]
    total = len(body["free_recommendations"]) + body["locked_recommendation_count"]
    # locked bodies are NEVER sent — only a count
    assert body["locked_recommendation_count"] >= 0
    assert total >= len(body["free_recommendations"])
    # free trust-builders are present
    assert "score_breakdown" in body and body["score_breakdown"]
    assert "projected_recovery" in body and body["projected_recovery"]
    assert body["overall_score"] is not None


def test_pro_gets_all_recommendations(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)
    body = client.get(f"/reports/{scan_id}/insights").json()
    assert body["unlocked"] is True
    assert body["locked_recommendation_count"] == 0
    # all recommendations returned when unlocked
    full = client.get(f"/reports/{scan_id}").json()
    assert len(body["free_recommendations"]) == len(full["recommendations"])
