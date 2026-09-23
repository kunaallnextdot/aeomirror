"""AI features (Anthropic integration) — server-side.

Feature A: AI-written report narrative (paid only), merged + cached.
Feature B: AI Content Insights (Pro-only, per page), cached per page.

The Anthropic API is NEVER hit: `app.core.ai.complete` is monkeypatched in every test,
and `ANTHROPIC_API_KEY` is set only via monkeypatch so `settings.ai_enabled` flips on
without any real key. Fetch is mocked so no network I/O happens either.
"""
import json

import app.api.routes_dashboard as rd
import app.api.routes_scan as rs
import app.billing.plans as plans_mod
import app.core.ai as ai_mod
import app.reports.ai_writer as ai_writer
from app.config import settings
from app.reports.engine import build_report
from app.reports.pdf import build_pdf
from app.scanner.models import PageBundle
from tests.authutil import auth_client
from tests.test_scanner import GOOD_HTML, GOOD_ROBOTS


# ------------------------------- fakes / helpers -------------------------------
async def _fake_fetch(url, *, transport=None, retries=None):
    return PageBundle(url=url, html=GOOD_HTML, robots_txt=GOOD_ROBOTS,
                      llms_txt_present=True, sitemap_present=True,
                      sitemap_xml="<?xml version='1.0'?><urlset><url><loc>x</loc></url></urlset>",
                      headers={"cache-control": "max-age=60"})


def _make_scan(client, monkeypatch, url):
    monkeypatch.setattr(rs, "fetch", _fake_fetch)
    return client.post("/v1/scan", json={"url": url}).json()["scan_id"]


def _install_ai(monkeypatch, return_value, key="test-anthropic-key"):
    """Turn AI on (key present) and stub the model call, counting invocations."""
    monkeypatch.setattr(settings, "anthropic_api_key", key)
    calls = {"n": 0}

    def fake_complete(system, user_content, max_tokens=None, timeout=None):
        calls["n"] += 1
        return return_value

    monkeypatch.setattr(ai_mod, "complete", fake_complete)
    return calls


def _install_grounded_narrative(monkeypatch, key="test-anthropic-key"):
    """AI on + a WELL-BEHAVED fake model: it reads the FACTS it is handed and returns a
    narrative that only cites real issue ids and the real overall score — so it passes
    write_narrative's grounding validation exactly as a truthful model would."""
    monkeypatch.setattr(settings, "anthropic_api_key", key)
    calls = {"n": 0}

    def fake_complete(system, user_content, max_tokens=None):
        calls["n"] += 1
        facts = json.loads(user_content.split("FACTS (JSON):\n", 1)[1].split("\n\n", 1)[0])
        issues = facts.get("issues") or []
        rid = issues[0]["id"] if issues else None
        overall = facts.get("overall_score")
        insights = ([{"id": rid,
                      "why_it_matters": "AI engines rely on this to read and cite the page.",
                      "priority_rationale": "Highest impact relative to its current score."}]
                    if rid else [])
        return json.dumps({
            "executive_summary": f"This site scored {overall}/100 for AI visibility, with "
                                 "clear gaps to close and some solid foundations in place.",
            "issue_insights": insights,
            "action_plan": ["Add JSON-LD structured data", "Allow AI crawlers in robots.txt",
                            "Write unique meta descriptions", "Fix the heading hierarchy",
                            "Publish an XML sitemap"],
        })

    monkeypatch.setattr(ai_mod, "complete", fake_complete)
    return calls


def _enforce_billing(monkeypatch, scan_limit=50):
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setitem(plans_mod.ENTITLEMENTS[plans_mod.PLAN_FREE], "scan_limit", scan_limit)


def _go_pro(client):
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})


def _verify_email(user_id: str):
    """Mark a user's email verified directly (the Free-tier AI guardrail requires it)."""
    from app.db.models import User
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        u.email_verified = True
        db.commit()
    finally:
        db.close()


_CONTENT = json.dumps({
    "tone": {"assessment": "Professional and direct.", "score_0_100": 72},
    "clarity": {"assessment": "Mostly clear.", "score_0_100": 65},
    "structure": {"assessment": "Good headings.", "score_0_100": 80},
    "suggestions": ["Shorten the intro paragraph", "Add an FAQ section",
                    "Use active voice", "Break the wall of text into lists"],
    "rewrite_example": {"before": "We are the best in the business.",
                        "after": "Teams cut onboarding time 40% with our platform."},
})


# ============================ Feature A: report narrative ============================
def test_ai_narrative_merged_and_cached_when_qualified(monkeypatch):
    """Billing off → every org qualifies (downloads entitlement). The narrative is
    merged under report['ai'] and cached: a second render does NOT call the model."""
    calls = _install_grounded_narrative(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ai-narrative.example/")

    body = client.get(f"/reports/{sid}").json()
    assert "ai" in body and body["ai"]["executive_summary"]
    assert body["ai"]["action_plan"] and isinstance(body["ai"]["issue_insights"], list)
    assert body["ai"]["_meta"]["model"] and body["ai"]["_meta"]["generated_at"]
    assert calls["n"] == 1

    body2 = client.get(f"/reports/{sid}").json()          # served from the cached row
    assert body2["ai"]["executive_summary"] == body["ai"]["executive_summary"]
    assert calls["n"] == 1                                 # model NOT called again


def test_ai_narrative_refresh_forces_regeneration(monkeypatch):
    """?refresh_ai=1 (admin/non-prod) bypasses the cache and calls the model again."""
    calls = _install_grounded_narrative(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ai-refresh.example/")

    client.get(f"/reports/{sid}")
    client.get(f"/reports/{sid}")                          # cached
    assert calls["n"] == 1
    body = client.get(f"/reports/{sid}?refresh_ai=1").json()   # forced regeneration
    assert body["ai"]["executive_summary"]
    assert calls["n"] == 2


def test_ai_narrative_free_tier_is_limited(monkeypatch):
    """A Free org with a verified email now GETS a narrative — summary + insights, but
    NO action plan and tagged tier='free'."""
    _enforce_billing(monkeypatch)
    calls = _install_grounded_narrative(monkeypatch)
    client, body = auth_client()
    _verify_email(body["user"]["id"])
    sid = _make_scan(client, monkeypatch, "https://ai-free-ok.example/")

    ai = client.get(f"/reports/{sid}").json().get("ai")
    assert ai and ai["executive_summary"]
    assert ai["_meta"]["tier"] == "free"
    assert "action_plan" not in ai                        # free tier never gets the action plan
    assert isinstance(ai["issue_insights"], list) and len(ai["issue_insights"]) <= 3
    assert calls["n"] == 1


def test_ai_narrative_unverified_free_is_blocked(monkeypatch):
    """Free tier requires a verified email; otherwise AI is skipped (rule-based, 200)."""
    _enforce_billing(monkeypatch)
    calls = _install_grounded_narrative(monkeypatch)
    client, _ = auth_client()                             # email left unverified
    sid = _make_scan(client, monkeypatch, "https://ai-unverified.example/")

    resp = client.get(f"/reports/{sid}")
    assert resp.status_code == 200
    assert "ai" not in resp.json()
    assert calls["n"] == 0


def test_ai_narrative_free_monthly_cap_blocks(monkeypatch):
    """The per-org monthly free cap blocks the next narrative → rule-based fallback, 200."""
    _enforce_billing(monkeypatch)
    monkeypatch.setattr(settings, "ai_free_monthly_narratives", 1)
    calls = _install_grounded_narrative(monkeypatch)
    client, body = auth_client()
    _verify_email(body["user"]["id"])

    sid1 = _make_scan(client, monkeypatch, "https://ai-cap-1.example/")
    assert "ai" in client.get(f"/reports/{sid1}").json()          # 1st narrative: allowed
    sid2 = _make_scan(client, monkeypatch, "https://ai-cap-2.example/")
    assert "ai" not in client.get(f"/reports/{sid2}").json()      # 2nd: capped → fallback
    assert calls["n"] == 1


def test_ai_narrative_global_daily_cap_blocks(monkeypatch):
    """The global daily ceiling blocks every tier (here a paid caller, billing off)."""
    monkeypatch.setattr(settings, "ai_global_daily_calls", 0)
    calls = _install_grounded_narrative(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ai-global.example/")

    resp = client.get(f"/reports/{sid}")
    assert resp.status_code == 200
    assert "ai" not in resp.json()
    assert calls["n"] == 0


def test_ai_narrative_free_to_paid_upgrade_regenerates(monkeypatch):
    """A cached FREE narrative is regenerated once at the paid tier after an upgrade."""
    _enforce_billing(monkeypatch)
    calls = _install_grounded_narrative(monkeypatch)
    client, body = auth_client()
    _verify_email(body["user"]["id"])
    sid = _make_scan(client, monkeypatch, "https://ai-upgrade.example/")

    free_ai = client.get(f"/reports/{sid}").json()["ai"]
    assert free_ai["_meta"]["tier"] == "free" and "action_plan" not in free_ai
    assert calls["n"] == 1

    _go_pro(client)                                               # now qualifies as paid
    paid_ai = client.get(f"/reports/{sid}").json()["ai"]
    assert paid_ai["_meta"]["tier"] == "paid" and "action_plan" in paid_ai
    assert calls["n"] == 2                                        # regenerated once
    client.get(f"/reports/{sid}")                                 # now cached at paid tier
    assert calls["n"] == 2


def test_ai_narrative_graceful_fallback_when_complete_none(monkeypatch):
    """When the model call fails (complete → None), the report still renders with the
    rule-based text and no AI block."""
    calls = _install_ai(monkeypatch, None)                 # simulate an AI failure
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ai-fail.example/")

    resp = client.get(f"/reports/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert "ai" not in body
    assert body["scorecard"]["summary"]
    assert calls["n"] >= 1                                 # it did attempt the call


def test_ai_narrative_unlocked_by_pro(monkeypatch):
    """Under enforcement, going Pro qualifies the org and the narrative appears."""
    _enforce_billing(monkeypatch)
    calls = _install_grounded_narrative(monkeypatch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ai-pro.example/")
    _go_pro(client)

    body = client.get(f"/reports/{sid}").json()
    assert body.get("ai", {}).get("executive_summary")
    assert calls["n"] == 1


# ============================ Feature B: content insights ============================
def test_content_insights_402_for_free(monkeypatch):
    _enforce_billing(monkeypatch)
    _install_ai(monkeypatch, _CONTENT)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-free.example/")

    r = client.post(f"/api/scans/{sid}/content-insights", json={})
    assert r.status_code == 402
    assert "Pro" in r.json()["detail"]


def test_content_insights_generated_and_cached_per_page(monkeypatch):
    """Billing off → Pro access. First call analyzes + caches; a repeat call returns the
    cached row without re-calling the model. GET lists the cached insight."""
    calls = _install_ai(monkeypatch, _CONTENT)
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-gen.example/")

    r1 = client.post(f"/api/scans/{sid}/content-insights", json={})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["cached"] is False
    assert b1["insights"]["tone"]["score_0_100"] == 72
    assert len(b1["insights"]["suggestions"]) == 4
    assert calls["n"] == 1

    r2 = client.post(f"/api/scans/{sid}/content-insights", json={})
    assert r2.json()["cached"] is True
    assert calls["n"] == 1                                  # served from cache, no new call

    listed = client.get(f"/api/scans/{sid}/content-insights").json()["insights"]
    assert len(listed) == 1 and listed[0]["data"]["tone"]["score_0_100"] == 72


def test_content_insights_structure_fixes_are_real_model_output_never_fabricated(monkeypatch):
    """Phase F: Structure gets its own actionable fix list + implementation skeleton
    from the SAME single model call (no second LLM call) — present when the model
    supplies them, and honestly empty (never invented) when it doesn't."""
    content_with_fixes = json.dumps({
        "tone": {"assessment": "Professional.", "score_0_100": 72},
        "clarity": {"assessment": "Mostly clear.", "score_0_100": 65},
        "structure": {
            "assessment": "Long service list with no introduction.", "score_0_100": 40,
            "fixes": ["Add a 2-3 sentence introduction before the service list",
                      "Group related services under H2 headings"],
            "implementation": "H1: Digital Marketing Services\nIntro: ...\nH2: Our Services\nH3: SEO",
        },
        "suggestions": ["Shorten the intro paragraph"],
        "rewrite_example": {"before": "x", "after": "y"},
    })
    calls = _install_ai(monkeypatch, content_with_fixes)
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-fixes.example/")

    r = client.post(f"/api/scans/{sid}/content-insights", json={})
    assert r.status_code == 200
    structure = r.json()["insights"]["structure"]
    assert structure["fixes"] == ["Add a 2-3 sentence introduction before the service list",
                                  "Group related services under H2 headings"]
    assert structure["implementation"].startswith("H1: Digital Marketing Services")
    assert calls["n"] == 1   # one model call total, no second call for structure


def test_content_insights_structure_fixes_default_empty_when_model_omits_them(monkeypatch):
    """A model response with no structure fixes (e.g. the page's structure is already
    fine) must render honestly empty — never a placeholder/fabricated fix."""
    _install_ai(monkeypatch, _CONTENT)   # the original fixture has no fixes/implementation
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-nofix.example/")

    r = client.post(f"/api/scans/{sid}/content-insights", json={})
    structure = r.json()["insights"]["structure"]
    assert structure["fixes"] == []
    assert structure["implementation"] == ""


def test_content_insights_tone_and_clarity_fixes_from_the_same_single_model_call(monkeypatch):
    """Phase H: Tone and Clarity get the SAME fixes/implementation mechanism as
    Structure, extending the one existing LLM call — never a second call, never
    fabricated when the model has nothing to fix."""
    content_with_all_fixes = json.dumps({
        "tone": {"assessment": "Too formal for the audience.", "score_0_100": 55,
                 "fixes": ["Replace the opening paragraph's abstract wording with a "
                           "direct definition of the service and its intended audience"],
                 "implementation": "Instead of 'We leverage synergistic solutions', say "
                                   "'We help small clinics get found by patients online.'"},
        "clarity": {"assessment": "Sentences are long and jargon-heavy.", "score_0_100": 48,
                   "fixes": ["Break the second paragraph into two shorter sentences"],
                   "implementation": ""},
        "structure": {"assessment": "Fine.", "score_0_100": 90},
        "suggestions": ["Shorten the intro paragraph"],
        "rewrite_example": {"before": "x", "after": "y"},
    })
    calls = _install_ai(monkeypatch, content_with_all_fixes)
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-tone-clarity.example/")

    r = client.post(f"/api/scans/{sid}/content-insights", json={})
    assert r.status_code == 200
    insights = r.json()["insights"]
    assert insights["tone"]["fixes"] == [
        "Replace the opening paragraph's abstract wording with a direct definition "
        "of the service and its intended audience"]
    assert insights["tone"]["implementation"].startswith("Instead of")
    assert insights["clarity"]["fixes"] == ["Break the second paragraph into two shorter sentences"]
    assert insights["clarity"]["implementation"] == ""
    assert calls["n"] == 1   # one model call total — no second call for tone/clarity


def test_content_insights_tone_and_clarity_fixes_default_empty_when_model_omits_them(monkeypatch):
    """The original fixture has no fixes/implementation for tone/clarity either —
    must render honestly empty, never a placeholder."""
    _install_ai(monkeypatch, _CONTENT)
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-tc-nofix.example/")

    r = client.post(f"/api/scans/{sid}/content-insights", json={})
    insights = r.json()["insights"]
    assert insights["tone"]["fixes"] == [] and insights["tone"]["implementation"] == ""
    assert insights["clarity"]["fixes"] == [] and insights["clarity"]["implementation"] == ""


def test_content_insights_malformed_json_returns_503(monkeypatch):
    """A malformed model response yields a clean 503, never a 500."""
    _install_ai(monkeypatch, "this is not json at all, sorry")
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-bad.example/")

    r = client.post(f"/api/scans/{sid}/content-insights", json={})
    assert r.status_code == 503


def test_content_insights_rejects_foreign_page(monkeypatch):
    """A page_url that is not part of the scan is rejected (422), before any fetch."""
    _install_ai(monkeypatch, _CONTENT)
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-scope.example/")

    r = client.post(f"/api/scans/{sid}/content-insights",
                    json={"page_url": "https://someone-else.example/x"})
    assert r.status_code == 422


def test_content_insights_over_budget_returns_504_and_bounds_ai_timeout(monkeypatch):
    """When fetch+AI exceed content_insight_budget_seconds the endpoint returns 504
    (not a hang / 500), and the AI call is invoked with the shorter interactive timeout.

    We can't assert the orphaned threadpool thread STOPS directly — a sync-SDK call in a
    threadpool can't be force-cancelled — so we assert the timeout bound that caps how
    long any orphan can run (content_insight_ai_timeout_seconds per attempt) instead."""
    import time as _time
    monkeypatch.setattr(settings, "anthropic_api_key", "test-anthropic-key")
    monkeypatch.setattr(settings, "content_insight_budget_seconds", 1)
    monkeypatch.setattr(rd, "fetch", _fake_fetch)
    seen = {}

    def slow_complete(system, user_content, max_tokens=None, timeout=None):
        seen["timeout"] = timeout          # recorded before the (budget-exceeding) sleep
        _time.sleep(2)
        return _CONTENT
    monkeypatch.setattr(ai_mod, "complete", slow_complete)

    client, _ = auth_client()
    sid = _make_scan(client, monkeypatch, "https://ci-slow.example/")
    r = client.post(f"/api/scans/{sid}/content-insights", json={})
    assert r.status_code == 504
    assert seen["timeout"] == settings.content_insight_ai_timeout_seconds


# ==================== Feature A: grounding / validation (unit) ====================
# These drive ai_writer.write_narrative directly with a hand-built report so the exact
# FACTS (ids + scores {40, 15, 90}) are known, and stub app.core.ai.complete to return
# specific model responses. No HTTP, no real API.
def _mini_report() -> dict:
    """A real engine report with one failing issue ('schema') and one passing signal."""
    return build_report({
        "scan_id": "mini", "url": "https://mini.example/", "domain": "mini.example",
        "overall_score": 40, "scanner_version": "3.0.0",
        "scanned_at": "2026-01-01T00:00:00Z",
        "sections": [
            {"id": "schema", "label": "Structured Data", "score": 15, "status": "fail",
             "weight": 15, "issues": ["No JSON-LD found."],
             "recommendations": ["Add JSON-LD (Organization, FAQ)."],
             "evidence": {"has_structured_data": False}},
            {"id": "robots", "label": "robots.txt", "score": 90, "status": "pass",
             "weight": 10, "issues": [], "recommendations": [], "evidence": {}},
        ],
    })


def _mini_report_multi() -> dict:
    """A report with FIVE failing issues (ids sig0..sig4) for tier-shape assertions."""
    return build_report({
        "scan_id": "multi", "url": "https://multi.example/", "domain": "multi.example",
        "overall_score": 30, "scanner_version": "3.0.0",
        "scanned_at": "2026-01-01T00:00:00Z",
        "sections": [
            {"id": f"sig{i}", "label": f"Signal {i}", "score": 10 + i, "status": "fail",
             "weight": 10, "issues": [f"issue {i}"], "recommendations": [f"fix {i}"],
             "evidence": {}}
            for i in range(5)
        ],
    })


_MULTI_RESPONSE = json.dumps({   # cites every real id; no score-like text to validate
    "executive_summary": "The site has clear AI-visibility gaps to address across several areas.",
    "issue_insights": [{"id": f"sig{i}", "why_it_matters": f"Reason {i}.",
                        "priority_rationale": f"Rank {i}."} for i in range(5)],
    "action_plan": ["Step one", "Step two", "Step three", "Step four", "Step five"],
})


def _stub_complete(monkeypatch, raw):
    """Turn AI on and make ai.complete return `raw` (write_narrative still uses the REAL
    ai.parse_json + validation)."""
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(ai_mod, "complete", lambda system, user, *, max_tokens=None: raw)


def test_write_narrative_free_caps_top3_no_action_plan(monkeypatch):
    _stub_complete(monkeypatch, _MULTI_RESPONSE)
    out = ai_writer.write_narrative(_mini_report_multi(), tier="free")
    assert len(out["issue_insights"]) == 3     # top-3 only
    assert "action_plan" not in out            # free tier has no action plan


def test_write_narrative_paid_all_insights_and_action_plan(monkeypatch):
    _stub_complete(monkeypatch, _MULTI_RESPONSE)
    out = ai_writer.write_narrative(_mini_report_multi(), tier="paid")
    assert len(out["issue_insights"]) == 5     # every issue
    assert len(out["action_plan"]) == 5


def test_write_narrative_parses_fenced_json(monkeypatch):
    body = json.dumps({
        "executive_summary": "The site scored 40/100 with weak structured data.",
        "issue_insights": [{"id": "schema", "why_it_matters": "Matters.", "priority_rationale": "Top."}],
        "action_plan": ["Add JSON-LD", "Publish a sitemap"],
    })
    _stub_complete(monkeypatch, "```json\n" + body + "\n```")   # fenced response
    out = ai_writer.write_narrative(_mini_report(), tier="paid")
    assert out and out["executive_summary"].startswith("The site scored 40/100")
    assert [i["id"] for i in out["issue_insights"]] == ["schema"]


def test_write_narrative_drops_unknown_issue_id(monkeypatch):
    _stub_complete(monkeypatch, json.dumps({
        "executive_summary": "The site scored 40/100 overall.",
        "issue_insights": [
            {"id": "schema", "why_it_matters": "Real.", "priority_rationale": "Yes."},
            {"id": "ghost_signal", "why_it_matters": "Invented.", "priority_rationale": "No."},
        ],
        "action_plan": ["Add JSON-LD"],
    }))
    out = ai_writer.write_narrative(_mini_report(), tier="paid")
    assert [i["id"] for i in out["issue_insights"]] == ["schema"]   # fabricated id dropped


def test_write_narrative_rejects_unknown_score(monkeypatch):
    # 88/100 is not a real score ({40,15,90}) → summary dropped → required field gone → None.
    _stub_complete(monkeypatch, json.dumps({
        "executive_summary": "Impressively, the site scored 88/100 for AI visibility.",
        "issue_insights": [], "action_plan": [],
    }))
    assert ai_writer.write_narrative(_mini_report(), tier="paid") is None


def test_write_narrative_drops_action_step_with_bad_score(monkeypatch):
    _stub_complete(monkeypatch, json.dumps({
        "executive_summary": "The site scored 40/100 overall.",
        "issue_insights": [],
        "action_plan": ["Raise schema from 15/100", "Chase a fake 99/100 target"],
    }))
    out = ai_writer.write_narrative(_mini_report(), tier="paid")
    assert out["action_plan"] == ["Raise schema from 15/100"]   # the 99/100 step dropped


def test_write_narrative_rejects_malformed_json(monkeypatch):
    _stub_complete(monkeypatch, "sorry, I can't help with that")
    assert ai_writer.write_narrative(_mini_report(), tier="paid") is None


def test_write_narrative_none_when_ai_off(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)   # ai_enabled False
    assert ai_writer.write_narrative(_mini_report(), tier="paid") is None


# ==================== Feature A: PDF renders with and without AI ====================
def test_build_pdf_without_ai():
    assert build_pdf(_mini_report())[:4] == b"%PDF"


def test_build_pdf_with_ai():
    rep = _mini_report()
    rep["ai"] = {
        "executive_summary": "The site scored 40/100 with weak schema.",
        "issue_insights": [{"id": "schema", "why_it_matters": "Matters.", "priority_rationale": "Top."}],
        "action_plan": ["Add JSON-LD", "Publish a sitemap"],
        "_meta": {"model": "claude-haiku-4-5-20251001", "generated_at": "2026-01-01T00:00:00Z"},
    }
    assert build_pdf(rep)[:4] == b"%PDF"
