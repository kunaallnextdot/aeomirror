"""Phase 3 — AI Visibility layer: pure read-side builders (visibility summary,
answerability, content gaps, competitor intelligence, opportunity finder, trend) and the
gated GET /prompt-runs/{run_id}/visibility endpoint.

No scanner/scoring change, no LLM. Builders are tested against synthetic run-summary dicts
(the exact shape aggregation.run_summary emits); the endpoint is tested against real runs
built with the existing Answer Tracking test helpers."""
import app.billing.plans as plans_mod
from app.config import settings
from app.reports.engine import build_report
from app.services.answer_tracking import visibility as V
from tests.authutil import auth_client
from tests.test_answer_tracking_analysis import _analysis, _result, _run, _set
from tests.test_reports import _scan


# ------------------------------- synthetic run summary -------------------------------
def _summary(**over):
    base = {
        "run_id": "r1", "prompt_set_id": "ps1",
        "analyzed_count": 10, "sample_count": 11, "excluded_extraction_failures": 1,
        "mention_rate": 40.0, "mentions": 4,
        "per_provider": [
            {"provider": "openai", "samples": 5, "mentions": 3, "mention_rate": 60.0},
            {"provider": "anthropic", "samples": 5, "mentions": 1, "mention_rate": 20.0},
        ],
        "per_prompt": [
            {"prompt_id": "p1", "text": "best crm?", "samples": 3, "mentions": 3,
             "mention_rate": 100.0, "is_gap": False, "recommended_entities": ["You"], "gap": None},
            {"prompt_id": "p2", "text": "top tools?", "samples": 3, "mentions": 1,
             "mention_rate": 33.3, "is_gap": False, "recommended_entities": ["Rival"], "gap": None},
            {"prompt_id": "p3", "text": "alternatives?", "samples": 4, "mentions": 0,
             "mention_rate": 0.0, "is_gap": True, "recommended_entities": ["Rival", "Other"],
             "gap": {"why": "The site blocks GPTBot.", "actions": ["Allow GPTBot", "Add schema"],
                     "has_signal": True}},
            {"prompt_id": "p4", "text": "who is x?", "samples": 2, "mentions": 0,
             "mention_rate": 0.0, "is_gap": True, "recommended_entities": [],
             "gap": {"why": None, "actions": [], "has_signal": False}},
        ],
        "citation_count": 0, "citation_search_samples": 4, "citation_excluded_no_search": 0,
        "citation_diagnosis": {"status": "searched_not_cited", "per_provider": []},
        "competitors": [{"name": "Rival", "mentions": 3, "mention_rate": 30.0,
                         "domain": "rival.com", "tracked": True}],
        "average_position": 2.0,
        "leaderboard": [
            {"name": "Rival", "appearances": 5, "prompt_coverage": 2, "appearance_rate": 50.0,
             "average_position": 1.5, "head_to_head": 1, "is_you": False, "domain": "rival.com",
             "prompt_ids": ["p2", "p3"], "rank": 1, "prev_rank": 2, "rank_delta": 1},
            {"name": "You", "appearances": 4, "prompt_coverage": 2, "appearance_rate": 40.0,
             "average_position": 2.0, "head_to_head": 0, "is_you": True, "domain": "you.com",
             "prompt_ids": ["p1", "p2"], "rank": 2, "prev_rank": 1, "rank_delta": -1},
        ],
        "leaderboard_excluded": 0, "leaderboard_min_appearances": 2,
    }
    base.update(over)
    return base


# ============================== visibility summary ==============================
def test_visibility_summary_metrics_distinct():
    v = V.build_visibility_summary(_summary())
    assert v["mention_rate"] == 40.0 and v["missed_rate"] == 60.0
    assert v["mentions"] == 4 and v["missed"] == 6
    assert v["citation_count"] == 0                    # brand citation (distinct metric)
    assert v["competitor_mentions"] == 3               # competitor mention (distinct metric)
    assert len(v["per_provider"]) == 2


def test_visibility_summary_empty_run():
    v = V.build_visibility_summary({"analyzed_count": 0, "mentions": 0, "mention_rate": None,
                                    "competitors": []})
    assert v["mention_rate"] is None and v["missed"] == 0 and v["competitor_mentions"] == 0


def test_provider_aggregation_passthrough():
    v = V.build_visibility_summary(_summary())
    openai = next(p for p in v["per_provider"] if p["provider"] == "openai")
    assert openai["mention_rate"] == 60.0 and openai["samples"] == 5


# ============================== answerability ==============================
def test_answerability_thresholds():
    a = V.build_answerability(_summary())
    assert [p["prompt_id"] for p in a["visible"]] == ["p1"]                 # 100% >= 60
    assert [p["prompt_id"] for p in a["partially_visible"]] == ["p2"]       # 33.3%
    assert {p["prompt_id"] for p in a["not_visible"]} == {"p3", "p4"}       # 0%
    assert a["counts"] == {"visible": 1, "partially_visible": 1, "not_visible": 2, "unknown": 0}
    assert a["primary_metric"] == "mention_rate"


def test_answerability_unknown_when_no_samples():
    s = _summary(per_prompt=[{"prompt_id": "p", "text": "q", "samples": 0, "mentions": 0,
                              "mention_rate": None, "is_gap": False, "recommended_entities": [],
                              "gap": None}])
    a = V.build_answerability(s)
    assert a["counts"]["unknown"] == 1 and a["counts"]["not_visible"] == 0


def test_answerability_deterministic():
    assert V.build_answerability(_summary()) == V.build_answerability(_summary())


# ============================== content gaps ==============================
def test_content_gap_grounded_preserved():
    gaps = V.build_content_gaps(_summary())
    p3 = next(g for g in gaps if g["prompt_id"] == "p3")
    assert p3["why"] == "The site blocks GPTBot."
    assert p3["actions"] == ["Allow GPTBot", "Add schema"]     # preserved verbatim
    assert p3["has_signal"] is True and p3["insufficient_evidence"] is False


def test_content_gap_no_grounded_evidence():
    gaps = V.build_content_gaps(_summary())
    p4 = next(g for g in gaps if g["prompt_id"] == "p4")
    assert p4["why"] is None and p4["actions"] == []
    assert p4["insufficient_evidence"] is True                 # never invents a reason


def test_content_gap_only_zero_mention_prompts():
    ids = {g["prompt_id"] for g in V.build_content_gaps(_summary())}
    assert ids == {"p3", "p4"}                                 # p1/p2 are not gaps


# ============================== competitor intelligence ==============================
def test_competitor_intelligence_brand_and_board():
    ci = V.build_competitor_intelligence(_summary())
    assert ci["brand"]["rank"] == 2 and ci["brand"]["mention_rate"] == 40.0
    assert [c["name"] for c in ci["competitors"]] == ["Rival"]
    assert ci["competitors"][0]["tracked"] is True             # from configured competitor_domains
    assert ci["competitors"][0]["rank_delta"] == 1


def test_competitor_head_to_head_evidence_language():
    ci = V.build_competitor_intelligence(_summary())
    h2h = ci["head_to_head"]
    assert len(h2h) == 1 and h2h[0]["competitor"] == "Rival" and h2h[0]["count"] == 1
    assert "while your brand was absent" in h2h[0]["statement"]
    assert "won" not in h2h[0]["statement"].lower()            # never claims a winner
    # only the prompt where the brand was ACTUALLY absent (p3), not p2 (brand mentioned there)
    assert [p["prompt_id"] for p in h2h[0]["prompts"]] == ["p3"]


def test_competitor_missing_data():
    ci = V.build_competitor_intelligence({"leaderboard": [], "per_prompt": [], "competitors": []})
    assert ci["brand"] is None and ci["competitors"] == [] and ci["head_to_head"] == []


# ============================== opportunities ==============================
def test_opportunity_answer_gap():
    op = V.build_opportunities(_summary())
    ag = next(o for o in op["items"] if o["id"] == "answer_gap:p3")
    assert ag["type"] == "answer_gap" and ag["impact"] == 100.0 and ag["priority"] == "Critical"
    assert ag["recommended_action"] == "Allow GPTBot"          # from grounded gap action
    assert ag["affected_prompts"] == [{"prompt_id": "p3", "prompt": "alternatives?"}]
    assert ag["affected_urls"] == []                           # never invented


def test_opportunity_competitor_gap():
    op = V.build_opportunities(_summary())
    cg = next(o for o in op["items"] if o["id"] == "competitor_gap:rival")
    assert cg["type"] == "competitor_gap" and cg["evidence"]["head_to_head"] == 1
    # 1 head-to-head prompt / 4 total prompts => impact 25.0
    assert cg["impact"] == 25.0
    assert [p["prompt_id"] for p in cg["affected_prompts"]] == ["p3"]


def test_opportunity_citation_gap_only_when_searched_not_cited():
    op = V.build_opportunities(_summary())
    assert any(o["id"] == "citation_gap:run" for o in op["items"])
    # a different diagnosis => no citation opportunity (dimension omitted, not fabricated)
    s2 = _summary(citation_diagnosis={"status": "search_disabled", "per_provider": []})
    assert not any(o["id"] == "citation_gap:run" for o in V.build_opportunities(s2)["items"])


def test_opportunity_score_loss_from_scan_report():
    report = build_report(_scan())                             # schema w15 s15 -> points_lost 12.75
    op = V.build_opportunities(_summary(), scan_report=report)
    sl = next(o for o in op["items"] if o["id"] == "score_loss:schema")
    assert sl["type"] == "score_loss" and sl["source"] == "scanner"
    assert sl["impact"] == 85.0                                # 12.75 / 15 * 100
    assert sl["affected_urls"] == [report["url"]]              # the audited page (real)
    assert sl["related_recommendation_id"] == "schema"


def test_opportunity_missing_dimension_omitted():
    op = V.build_opportunities(_summary())                     # no scan_report
    assert not any(o["type"] == "score_loss" for o in op["items"])   # dimension simply absent


def test_opportunity_ranking_and_stable_ids_and_dedup():
    report = build_report(_scan())
    a = V.build_opportunities(_summary(), scan_report=report)
    b = V.build_opportunities(_summary(), scan_report=report)
    assert a == b                                              # deterministic
    ids = [o["id"] for o in a["items"]]
    assert len(ids) == len(set(ids))                          # no duplicates / stable ids
    ranks = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    seq = [(ranks[o["priority"]], -o["impact"]) for o in a["items"]]
    assert seq == sorted(seq)                                 # Critical->Low, then impact desc
    assert a["impact_label"] == "Opportunity Impact"


def test_opportunity_no_invented_prompts():
    op = V.build_opportunities(_summary())
    for o in op["items"]:
        for p in o["affected_prompts"]:
            assert p["prompt_id"] in {"p1", "p2", "p3", "p4"}    # only real prompts


# ============================== trend ==============================
def test_trend_improving_declining_stable():
    def tr(cur, prev, **f):
        return {"runs": [{"mention_rate": prev}, {"mention_rate": cur, **f}]}
    assert V.build_trend_signal(tr(50, 40))["direction"] == "improving"
    assert V.build_trend_signal(tr(30, 40))["direction"] == "declining"
    assert V.build_trend_signal(tr(41, 40))["direction"] == "stable"       # within ±2


def test_trend_flags_config_change_and_insufficient_history():
    assert V.build_trend_signal({"runs": [{"mention_rate": 40}]})["direction"] == "insufficient_history"
    t = V.build_trend_signal({"runs": [{"mention_rate": 40}, {"mention_rate": 80, "model_changed": True}]})
    assert t["config_changed"] is True                         # doesn't claim causation, just flags


# ============================== extraction failures ==============================
def test_extraction_failures_reported_not_counted_as_negative():
    v = V.build_visibility_summary(_summary(excluded_extraction_failures=3))
    assert v["excluded_extraction_failures"] == 3              # surfaced, not treated as a miss


# ============================== endpoint: gating + scoping ==============================
def _make_run_with_gap(db, org):
    ps, prompts = _set(db, org, prompts=("q1",))
    run = _run(db, ps, org)
    r = _result(db, run, prompts[0].id, org)
    _analysis(db, run, r.id, org, mentioned=False, recommended=[{"name": "Rival"}])
    return run.id


def _enforce_billing(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def test_visibility_requires_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get("/prompt-runs/whatever/visibility").status_code == 401


def test_visibility_org_scoped():
    from app.db.session import SessionLocal
    client_a, abody = auth_client()
    client_b, _ = auth_client()
    org_a = abody["organization"]["id"]
    db = SessionLocal()
    try:
        run_id = _make_run_with_gap(db, org_a)
    finally:
        db.close()
    assert client_a.get(f"/prompt-runs/{run_id}/visibility").status_code == 200
    assert client_b.get(f"/prompt-runs/{run_id}/visibility").status_code == 404


def test_visibility_free_is_limited(monkeypatch):
    from app.db.session import SessionLocal
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        run_id = _make_run_with_gap(db, org)
    finally:
        db.close()
    v = client.get(f"/prompt-runs/{run_id}/visibility").json()
    assert v["unlocked"] is False
    assert v["entitlements"] == {"ai_visibility": False, "competitor_intelligence": False,
                                 "opportunity_finder": False}
    assert v["opportunities"].get("preview") is True
    assert v["competitor_intelligence"].get("head_to_head_locked") is True
    assert v["visibility"]["mention_rate"] is not None         # overview always shown


def test_visibility_pro_is_complete(monkeypatch):
    from app.db.session import SessionLocal
    _enforce_billing(monkeypatch)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        run_id = _make_run_with_gap(db, org)
    finally:
        db.close()
    _go_pro(client)
    v = client.get(f"/prompt-runs/{run_id}/visibility").json()
    assert v["unlocked"] is True
    assert v["entitlements"]["ai_visibility"] is True
    assert "preview" not in v["opportunities"]                 # full opportunity finder
    assert v["competitor_intelligence"].get("head_to_head_locked") is not True


# ============================== answerability verdict (Feature 2) ==============================
def test_answerability_verdict_partial():
    a = V.build_answerability(_summary())          # 2 of 4 prompts mention -> coverage .5
    assert a["verdict"] == "partially_visible"
    assert a["analyzed_prompts"] == 4 and a["prompts_with_mention"] == 2
    assert "2 of 4" in a["evidence"]


def test_answerability_verdict_visible():
    pp = [{"prompt_id": f"p{i}", "text": "q", "samples": 2, "mentions": 2,
           "mention_rate": 100.0, "is_gap": False, "recommended_entities": [], "gap": None}
          for i in range(4)]
    a = V.build_answerability(_summary(per_prompt=pp))
    assert a["verdict"] == "visible" and a["verdict_label"] == "Visible"


def test_answerability_verdict_not_visible():
    pp = [{"prompt_id": f"p{i}", "text": "q", "samples": 2, "mentions": 0,
           "mention_rate": 0.0, "is_gap": True, "recommended_entities": [], "gap": None}
          for i in range(4)]
    a = V.build_answerability(_summary(per_prompt=pp))
    assert a["verdict"] == "not_visible"


def test_answerability_verdict_insufficient_data():
    pp = [{"prompt_id": "p1", "text": "q", "samples": 1, "mentions": 1,
           "mention_rate": 100.0, "is_gap": False, "recommended_entities": [], "gap": None}]
    a = V.build_answerability(_summary(per_prompt=pp))     # < ANSWERABILITY_MIN_PROMPTS
    assert a["verdict"] == "insufficient_data"
    assert a["verdict_label"] == "Insufficient data"


# ============================== report AI-visibility endpoint (Feature 7) ==============================
def _wire_site(db, org_id, *, url="https://aivis.example/", normalized="aivis.example",
               mentioned=True):
    from app.db.models import (
        EXTRACTION_COMPLETE, RUN_COMPLETED, Monitor, PromptResult, PromptResultAnalysis,
        PromptRun, PromptSet, Scan, TrackedPrompt,
    )
    scan = Scan(url=url, normalized_url=normalized, ars=50, rubric_version="1.0",
                result={"overall_score": 50, "sections": []}, organization_id=org_id)
    db.add(scan); db.commit(); db.refresh(scan)
    m = Monitor(organization_id=org_id, url=url, normalized_url=normalized,
                frequency="weekly", status="active", latest_scan_id=scan.id,
                brand_name="Acme", brand_domain=normalized)
    db.add(m); db.commit(); db.refresh(m)
    ps = PromptSet(organization_id=org_id, name="S", monitor_id=m.id,
                   brand_name="Acme", brand_domain=normalized)
    db.add(ps); db.commit(); db.refresh(ps)
    p = TrackedPrompt(prompt_set_id=ps.id, organization_id=org_id, text="best crm?")
    db.add(p); db.commit(); db.refresh(p)
    run = PromptRun(organization_id=org_id, prompt_set_id=ps.id, status=RUN_COMPLETED,
                    extraction_status=EXTRACTION_COMPLETE, total_calls=0, estimated_cost_usd=0.0)
    db.add(run); db.commit(); db.refresh(run)
    r = PromptResult(run_id=run.id, prompt_id=p.id, organization_id=org_id, provider="openai",
                     model="m", run_index=0, raw_response="Acme is great", search_enabled=True)
    db.add(r); db.commit(); db.refresh(r)
    db.add(PromptResultAnalysis(result_id=r.id, run_id=run.id, organization_id=org_id,
                                brand_mentioned=mentioned, extraction_failed=False,
                                extraction_model="e1"))
    db.commit()
    return scan.id


def test_report_ai_visibility_available_and_org_scoped():
    from app.db.session import SessionLocal
    client_a, abody = auth_client()
    client_b, _ = auth_client()
    org_a = abody["organization"]["id"]
    db = SessionLocal()
    try:
        scan_id = _wire_site(db, org_a)
    finally:
        db.close()
    body = client_a.get(f"/reports/{scan_id}/ai-visibility").json()
    assert body["available"] is True
    assert body["visibility"]["mention_rate"] == 100.0
    # another org cannot read this scan's AI visibility (404, never leak existence)
    assert client_b.get(f"/reports/{scan_id}/ai-visibility").status_code == 404


def test_report_ai_visibility_no_monitor():
    # a scan with no matching monitor -> clean empty state, not fabricated data
    from app.db.models import Scan
    from app.db.session import SessionLocal
    client, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan = Scan(url="https://no-monitor.example/", normalized_url="no-monitor.example",
                    ars=50, rubric_version="1.0", result={"overall_score": 50, "sections": []},
                    organization_id=org_id)
        db.add(scan); db.commit(); db.refresh(scan)
        scan_id = scan.id
    finally:
        db.close()
    resp = client.get(f"/reports/{scan_id}/ai-visibility").json()
    assert resp["available"] is False and resp["reason"] == "no_monitor"


def test_report_ai_visibility_requires_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get("/reports/whatever/ai-visibility").status_code == 401
