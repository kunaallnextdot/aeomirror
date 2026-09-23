"""Phase 2 tests: the deterministic score-impact simulator and the 30-day action plan
(pure builders + gating via GET /reports/{scan_id}/insights).

No scanner/scoring change is exercised — the projection is arithmetic over existing
sections and must NEVER mutate the real overall_score or section.score."""
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
from app.config import settings
from app.reports.engine import build_report
from app.reports.insights import (
    _plan_sort_key, build_action_plan, build_score_impact,
)
from app.scanner.models import PageBundle
from tests.authutil import auth_client
from tests.test_reports import FULL, _scan, _sections
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


def _recs(scan=None):
    return build_report(scan or _scan())["recommendations"]


# ------------------------------- simulator (pure) -------------------------------
def test_perfect_score_zero_recovery():
    perfect = [(sid, lbl, w, 100) for (sid, lbl, w, _s) in FULL]
    recs = build_report({"overall_score": 100, "sections": _sections(perfect)})["recommendations"]
    si = build_score_impact(recs, 100)
    assert si["items"] == [] and si["total_recoverable"] == 0.0
    assert si["projected_score"] == 100.0


def test_one_signal_recovery():
    recs = _recs()
    si = build_score_impact(recs, 44, selected_ids=["schema"])   # w15,s15 -> 12.75
    assert si["selected_recovery"] == 12.8
    assert si["projected_score"] == round(min(100.0, 44 + 12.8), 1)
    assert si["selected_signals"] == ["schema"]


def test_multiple_signals_recovery():
    recs = _recs()
    si = build_score_impact(recs, 44, selected_ids=["schema", "robots"])  # 12.75 + 8.0
    assert si["selected_recovery"] == 20.8
    assert si["remaining_recoverable"] == round(si["total_recoverable"] - 20.8, 1)


def test_duplicate_recs_same_signal_not_double_counted():
    recs = _recs()
    schema = next(r for r in recs if r["id"] == "schema")
    si = build_score_impact([schema, dict(schema)], 44, selected_ids=["schema"])
    assert len(si["items"]) == 1                       # deduped to one signal
    assert si["selected_recovery"] == 12.8             # recovered once, not twice


def test_projection_capped_at_100():
    recs = _recs()
    all_ids = [r["id"] for r in recs]
    si = build_score_impact(recs, 95, selected_ids=all_ids)
    assert si["projected_score"] == 100.0              # clamped


def test_selected_and_remaining_add_up():
    recs = _recs()
    si = build_score_impact(recs, 44, selected_ids=["schema"])
    assert round(si["selected_recovery"] + si["remaining_recoverable"], 1) == si["total_recoverable"]


def test_empty_recommendations():
    si = build_score_impact([], 80)
    assert si["items"] == [] and si["total_recoverable"] == 0.0
    assert si["projected_score"] == 80.0 and si["remaining_recoverable"] == 0.0


def test_simulator_never_mutates_actual_score():
    scan = _scan(44)
    rep = build_report(scan)
    _ = build_score_impact(rep["recommendations"], 44, selected_ids=["schema", "robots"])
    assert scan["overall_score"] == 44                 # input untouched
    assert rep["scorecard"]["overall_score"] == 44     # headline untouched
    # section scores untouched
    assert all(s["score"] == o["score"] for s, o in zip(scan["sections"], _sections(FULL)))


# ------------------------------- action plan (pure) -------------------------------
def test_plan_ordering_is_deterministic():
    recs = _recs()
    a = build_action_plan(recs)
    b = build_action_plan(list(reversed(recs)))
    assert a == b                                      # order-independent, deterministic


def test_plan_sort_key_critical_before_warning():
    crit = {"id": "a", "priority": "Critical", "priority_score": 9, "difficulty": "Moderate"}
    warn = {"id": "b", "priority": "Low", "priority_score": 1, "difficulty": "Moderate"}
    assert sorted([warn, crit], key=_plan_sort_key)[0]["id"] == "a"


def test_plan_sort_key_high_priority_before_low():
    hi = {"id": "h", "priority": "High", "priority_score": 6, "difficulty": "Moderate"}
    lo = {"id": "l", "priority": "Low", "priority_score": 6, "difficulty": "Moderate"}
    assert sorted([lo, hi], key=_plan_sort_key)[0]["id"] == "h"


def test_plan_sort_key_shorter_fix_time_first_when_tied():
    a = {"id": "a", "priority": "High", "priority_score": 5, "difficulty": "Easy",
         "estimated_fix_time": "2–4 hours"}
    b = {"id": "b", "priority": "High", "priority_score": 5, "difficulty": "Easy",
         "estimated_fix_time": "15–30 minutes"}
    assert sorted([a, b], key=_plan_sort_key)[0]["id"] == "b"


def test_plan_quick_wins_go_to_week_1():
    recs = _recs()
    qw = [{"id": "robots"}]                             # force robots into week 1
    plan = build_action_plan(recs, quick_wins=qw)
    assert any(t["id"] == "robots" for t in plan["week_1"])


def test_plan_missing_duration_is_not_invented():
    recs = [{"id": "schema", "issue_title": "Schema", "signal_label": "Schema",
             "category": "Schema", "priority": "Critical", "priority_score": 12,
             "severity": "Critical", "difficulty": "Moderate"}]   # no estimated_fix_time
    plan = build_action_plan(recs)
    task = (plan["week_1"] + plan["week_2"] + plan["week_3"] + plan["week_4"] + plan["backlog"])[0]
    assert task["estimated_fix_time"] is None          # not fabricated


def test_plan_no_invented_tasks_and_backlog_overflow():
    recs = _recs()
    plan = build_action_plan(recs, per_week=1)          # tiny cap forces overflow
    placed = (plan["week_1"] + plan["week_2"] + plan["week_3"] + plan["week_4"] + plan["backlog"])
    assert len(placed) == len(recs) == plan["total_tasks"]   # every rec placed exactly once
    assert len(plan["backlog"]) > 0                          # overflow lands in backlog
    ids_in = {r["id"] for r in recs}
    assert {t["id"] for t in placed} == ids_in               # no invented / dropped tasks


def test_plan_empty_recommendations():
    plan = build_action_plan([])
    assert plan["total_tasks"] == 0
    assert all(plan[w] == [] for w in ("week_1", "week_2", "week_3", "week_4", "backlog"))


# ------------------------------- report integration -------------------------------
def test_report_insights_carry_phase2_blocks():
    ins = build_report(_scan())["insights"]
    assert "score_impact" in ins and ins["score_impact"]["items"]
    assert "action_plan" in ins and ins["action_plan"]["total_tasks"] > 0


# ------------------------------- API gating -------------------------------
async def _fake_fetch(url):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"content-encoding": "gzip", "cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url="https://phase2.example/"):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _enforce_billing(monkeypatch, scan_limit=50):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", scan_limit)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_free_gets_limited_simulator_and_plan(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    body = client.get(f"/reports/{scan_id}/insights").json()
    assert body["unlocked"] is False
    si, ap = body["score_impact"], body["action_plan"]
    assert si["preview"] is True and len(si["items"]) <= 2
    assert si["locked_item_count"] >= 0
    assert ap["preview"] is True and len(ap["preview_tasks"]) <= 3
    assert ap["locked_task_count"] >= 0
    # current_score is still present so the free preview can show the concept
    assert si["current_score"] is not None


def test_paid_gets_complete_simulator_and_plan(monkeypatch):
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)
    _go_pro(client)
    body = client.get(f"/reports/{scan_id}/insights").json()
    assert body["unlocked"] is True
    si, ap = body["score_impact"], body["action_plan"]
    assert "preview" not in si                       # full simulator
    assert ap.get("total_tasks", 0) >= 0
    assert "week_1" in ap                            # full week structure
    # the full simulator exposes every opportunity
    full = client.get(f"/reports/{scan_id}").json()
    assert len(si["items"]) == len(full["insights"]["score_impact"]["items"])


def test_free_report_endpoint_does_not_leak_full_score_impact_or_plan(monkeypatch):
    """Regression: GET /reports/{scan_id} (the endpoint ReportView actually renders) must
    apply the SAME server-side gating as GET /reports/{scan_id}/insights — a free caller's
    embedded `insights.score_impact`/`action_plan` must never carry the locked items over
    the wire, even though the persisted report/cache always holds the full data."""
    _enforce_billing(monkeypatch)
    client, _ = auth_client()
    scan_id = _make_scan(client, monkeypatch)

    body = client.get(f"/reports/{scan_id}").json()
    si, ap = body["insights"]["score_impact"], body["insights"]["action_plan"]
    assert si.get("preview") is True and len(si["items"]) <= 2
    assert ap.get("preview") is True
    # the real week buckets / full item list must be ABSENT, not just unused by the UI
    for wk in ("week_1", "week_2", "week_3", "week_4", "backlog"):
        assert wk not in ap
    full_insights_items = build_report(_scan())["insights"]["score_impact"]["items"]
    assert len(si["items"]) < len(full_insights_items) or len(full_insights_items) <= 2

    _go_pro(client)
    body2 = client.get(f"/reports/{scan_id}").json()
    si2, ap2 = body2["insights"]["score_impact"], body2["insights"]["action_plan"]
    assert "preview" not in si2 and "week_1" in ap2
