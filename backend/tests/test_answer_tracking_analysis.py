"""AI Answer Tracking (Part B): extraction (defensive parse + retry + failure handling),
citation merge (provider vs LLM, None vs []), Share-of-Voice aggregation across all
samples, trend model-change flag, reanalyse (no answer-provider calls), cross-org
isolation, and admin_delete_org cleanup. No real network — the extractor is faked.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.config import settings
from app.db.models import (
    EXTRACTION_COMPLETE, EXTRACTION_PENDING, RUN_COMPLETED,
    Organization, PromptGapAnalysis, PromptResult, PromptResultAnalysis, PromptRun, PromptSet,
    TrackedPrompt, User,
)
from app.db.session import SessionLocal
from app.main import app
from app.services.answer_tracking import aggregation, extraction, gap_analysis
from app.services.answer_tracking import providers as at_providers
from app.services.answer_tracking.providers.base import ProviderResult
from fastapi.testclient import TestClient
from tests.authutil import auth_client

VALID = ('{"brand_mentioned": true, "mention_context": "Acme is great.", '
         '"sentiment": "positive", "brand_urls_cited": ["https://acme.com/llm"], '
         '"competitors_mentioned": [{"name": "Beta", "domain_if_stated": "beta.com"}], '
         '"position": 1}')
FENCED = "```json\n" + VALID + "\n```"


# ------------------------------- fake extractor -------------------------------
class FakeExtractor:
    """Stand-in for the extraction provider. `script` = per-call return strings;
    `always` = same string every call. Counts calls so retries are observable."""
    supports_citations = False

    def __init__(self, name="anthropic", model="fake-extract-1", *, script=None, always=VALID):
        self.name = name
        self.model = model
        self._script = script
        self._always = always
        self.calls = 0

    async def query(self, prompt, *, timeout):
        i = self.calls
        self.calls += 1
        text = self._script[min(i, len(self._script) - 1)] if self._script is not None else self._always
        return ProviderResult(text=text, citations=None, model=self.model, tokens=None, latency_ms=1)


def _patch_extractor(monkeypatch, fake):
    monkeypatch.setattr(at_providers, "extraction_provider", lambda: fake)


# ------------------------------- builders -------------------------------
def _set(db, org, *, brand_name="Acme", brand_domain="acme.com", aliases=None,
         competitors=None, prompts=("What is best?",)):
    ps = PromptSet(organization_id=org, name="S", brand_name=brand_name,
                   brand_domain=brand_domain, brand_aliases=aliases, competitor_domains=competitors)
    db.add(ps); db.commit(); db.refresh(ps)
    made = []
    for t in prompts:
        p = TrackedPrompt(prompt_set_id=ps.id, organization_id=org, text=t)
        db.add(p); made.append(p)
    db.commit()
    for p in made:
        db.refresh(p)
    return ps, made


def _run(db, ps, org, *, when=None):
    run = PromptRun(organization_id=org, prompt_set_id=ps.id, status=RUN_COMPLETED,
                    extraction_status=EXTRACTION_PENDING, total_calls=0, estimated_cost_usd=0.0)
    if when is not None:
        run.created_at = when
    db.add(run); db.commit(); db.refresh(run)
    return run


def _result(db, run, prompt_id, org, *, provider="anthropic", model="ans-m1", run_index=0,
            raw="Acme is a great option.", citations=None, error=None, search_enabled=True):
    r = PromptResult(run_id=run.id, prompt_id=prompt_id, organization_id=org, provider=provider,
                     model=model, run_index=run_index, raw_response=raw, citations=citations,
                     error=error, search_enabled=search_enabled)
    db.add(r); db.commit(); db.refresh(r)
    return r


def _analysis(db, run, result_id, org, *, mentioned=True, failed=False, sentiment="positive",
              urls=None, competitors=None, recommended=None, position=None, model="fake-extract-1"):
    a = PromptResultAnalysis(
        result_id=result_id, run_id=run.id, organization_id=org,
        brand_mentioned=(None if failed else mentioned), extraction_failed=failed,
        sentiment=(None if failed else sentiment), brand_urls_cited=urls,
        competitors_mentioned=competitors, recommended_entities=recommended,
        position=position, extraction_model=model)
    db.add(a); db.commit()
    return a


# =====================================================================
# extraction: parse + retry + failure
# =====================================================================
def test_extraction_parses_fenced_json(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    _patch_extractor(monkeypatch, FakeExtractor(always=FENCED))    # fenced but valid
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        _result(db, run, prompts[0].id, org)
        asyncio.run(extraction.extract_for_run(db, run))
        a = db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id).one()
        assert a.extraction_failed is False and a.brand_mentioned is True
        assert a.sentiment == "positive" and a.position == 1
        assert run.extraction_status == EXTRACTION_COMPLETE
    finally:
        db.close()


def test_extraction_retries_on_malformed_then_succeeds(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    fake = FakeExtractor(script=["this is not json at all", VALID])   # recover on retry
    _patch_extractor(monkeypatch, fake)
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        _result(db, run, prompts[0].id, org)
        asyncio.run(extraction.extract_for_run(db, run))
        a = db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id).one()
        assert fake.calls == 2                       # retried once
        assert a.extraction_failed is False and a.brand_mentioned is True
    finally:
        db.close()


def test_repeated_parse_failure_marks_failed_not_negative(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    fake = FakeExtractor(always="never valid json")   # both attempts fail
    _patch_extractor(monkeypatch, fake)
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        _result(db, run, prompts[0].id, org)
        asyncio.run(extraction.extract_for_run(db, run))
        a = db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id).one()
        assert fake.calls == 2
        assert a.extraction_failed is True
        assert a.brand_mentioned is None              # NOT False — a failure is not a negative
        assert a.raw_output == "never valid json"     # raw kept for debugging/re-analysis
    finally:
        db.close()


# =====================================================================
# citations: provider preferred, LLM only when None; None vs [] distinct
# =====================================================================
def test_provider_citations_preferred_llm_only_when_none(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    _patch_extractor(monkeypatch, FakeExtractor(always=VALID))   # LLM says acme.com/llm
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org, brand_domain="acme.com")
        run = _run(db, ps, org)
        # provider returned structured citations -> prefer them (filtered to brand domain)
        r_present = _result(db, run, prompts[0].id, org, run_index=0,
                            citations=[{"url": "https://acme.com/provider", "title": None},
                                       {"url": "https://other.com/x", "title": None}])
        # provider cannot report citations (None) -> fall back to the LLM's URLs
        r_none = _result(db, run, prompts[0].id, org, run_index=1, citations=None)
        asyncio.run(extraction.extract_for_run(db, run))
        by_result = {a.result_id: a for a in
                     db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id)}
        assert by_result[r_present.id].brand_urls_cited == ["https://acme.com/provider"]  # provider, brand only
        assert by_result[r_none.id].brand_urls_cited == ["https://acme.com/llm"]          # LLM fallback
    finally:
        db.close()


def test_citations_none_vs_empty_distinct(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    _patch_extractor(monkeypatch, FakeExtractor(always=VALID))
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org, brand_domain="acme.com")
        run = _run(db, ps, org)
        r_empty = _result(db, run, prompts[0].id, org, run_index=0, citations=[])   # searched, cited nothing
        r_none = _result(db, run, prompts[0].id, org, run_index=1, citations=None)  # cannot report
        asyncio.run(extraction.extract_for_run(db, run))
        by_result = {a.result_id: a for a in
                     db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id)}
        assert by_result[r_empty.id].brand_urls_cited == []                  # [] preserved (not LLM)
        assert by_result[r_none.id].brand_urls_cited == ["https://acme.com/llm"]   # None -> LLM
    finally:
        db.close()


# =====================================================================
# aggregation: mention rate across samples, failures excluded, competitors
# =====================================================================
def test_mention_rate_across_all_samples_excludes_failures():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        # 3 samples of one prompt: mentioned, mentioned, not; + 1 extraction failure
        r = [_result(db, run, prompts[0].id, org, run_index=i) for i in range(4)]
        _analysis(db, run, r[0].id, org, mentioned=True)
        _analysis(db, run, r[1].id, org, mentioned=True)
        _analysis(db, run, r[2].id, org, mentioned=False)
        _analysis(db, run, r[3].id, org, failed=True)          # excluded from denominator
        s = aggregation.run_summary(db, run)
        assert s["analyzed_count"] == 3 and s["excluded_extraction_failures"] == 1
        assert s["mention_rate"] == 66.7                        # 2 of 3, not a binary yes
        assert s["per_prompt"][0]["mention_rate"] == 66.7
    finally:
        db.close()


def test_competitor_counted_once_per_sample(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    # extractor returns the SAME competitor twice in one answer
    dup = ('{"brand_mentioned": true, "mention_context": null, "sentiment": "neutral", '
           '"brand_urls_cited": [], "competitors_mentioned": '
           '[{"name": "Beta", "domain_if_stated": null}, {"name": "Beta", "domain_if_stated": null}], '
           '"position": null}')
    _patch_extractor(monkeypatch, FakeExtractor(always=dup))
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        _result(db, run, prompts[0].id, org, run_index=0)
        _result(db, run, prompts[0].id, org, run_index=1)
        asyncio.run(extraction.extract_for_run(db, run))
        # each stored analysis lists Beta once (deduped at storage)
        for a in db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id):
            assert len(a.competitors_mentioned) == 1
        s = aggregation.run_summary(db, run)
        beta = next(c for c in s["competitors"] if c["name"] == "Beta")
        assert beta["mentions"] == 2 and beta["mention_rate"] == 100.0   # 2 samples, once each
    finally:
        db.close()


def test_trend_flags_model_change():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        now = datetime(2026, 3, 1, 12, 0, 0)
        run1 = _run(db, ps, org, when=now - timedelta(days=2))
        r1 = _result(db, run1, prompts[0].id, org, model="ans-m1")
        _analysis(db, run1, r1.id, org, mentioned=True, model="e1")
        run2 = _run(db, ps, org, when=now - timedelta(days=1))
        r2 = _result(db, run2, prompts[0].id, org, model="ans-m2")   # answer model changed
        _analysis(db, run2, r2.id, org, mentioned=True, model="e1")
        t = aggregation.set_trend(db, ps, n=10)
        assert [p["model_changed"] for p in t["runs"]] == [False, True]
    finally:
        db.close()


# =====================================================================
# reanalyse: no answer-provider calls; API; cross-org; delete-org
# =====================================================================
def test_reanalyse_reruns_extraction_without_answer_calls(monkeypatch):
    client, body = auth_client()
    org = body["organization"]["id"]
    fake = FakeExtractor(always=VALID)
    _patch_extractor(monkeypatch, fake)
    # spy: the answer providers must NEVER be queried during reanalyse
    calls = {"answer": 0}
    def spy_enabled():
        calls["answer"] += 1
        return []
    monkeypatch.setattr(at_providers, "enabled_providers", spy_enabled)
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        _result(db, run, prompts[0].id, org)
        run_id = run.id
    finally:
        db.close()

    r = client.post(f"/prompt-runs/{run_id}/reanalyse")
    assert r.status_code == 200 and r.json()["analyzed"] == 1
    assert calls["answer"] == 0                     # zero answer-provider calls
    assert fake.calls >= 1                           # extraction did run
    db = SessionLocal()
    try:
        assert db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run_id).count() == 1
    finally:
        db.close()


def test_summary_api_and_cross_org_isolation(monkeypatch):
    client_a, abody = auth_client()
    client_b, _ = auth_client()
    org_a = abody["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org_a)
        run = _run(db, ps, org_a)
        r = _result(db, run, prompts[0].id, org_a)
        _analysis(db, run, r.id, org_a, mentioned=True)
        run_id, set_id = run.id, ps.id
    finally:
        db.close()

    ok = client_a.get(f"/prompt-runs/{run_id}/summary")
    assert ok.status_code == 200 and ok.json()["mention_rate"] == 100.0

    # every new endpoint must 404 for another org (never leak existence)
    assert client_b.get(f"/prompt-runs/{run_id}/summary").status_code == 404
    assert client_b.get(f"/prompt-runs/{run_id}/results").status_code == 404
    assert client_b.get(f"/prompt-sets/{set_id}/trend").status_code == 404
    assert client_b.post(f"/prompt-runs/{run_id}/reanalyse").status_code == 404


def test_admin_delete_org_removes_analysis():
    from app.api.deps import get_admin
    _, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org_id)
        run = _run(db, ps, org_id)
        r = _result(db, run, prompts[0].id, org_id)
        _analysis(db, run, r.id, org_id, mentioned=True)
        owner = db.get(User, db.get(Organization, org_id).owner_id)
    finally:
        db.close()

    admin_client = TestClient(app)
    app.dependency_overrides[get_admin] = lambda: owner
    try:
        assert admin_client.delete(f"/admin/organizations/{org_id}").status_code == 200
    finally:
        app.dependency_overrides.pop(get_admin, None)

    db = SessionLocal()
    try:
        assert db.query(PromptResultAnalysis).filter(
            PromptResultAnalysis.organization_id == org_id).count() == 0
    finally:
        db.close()


# =====================================================================
# FIX1 — AI platforms excluded from competitor Share of Voice
# =====================================================================
def test_ai_platforms_excluded_from_competitors_but_kept_in_raw():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        r = _result(db, run, prompts[0].id, org)
        # extraction surfaced an AI platform AND a real competitor
        _analysis(db, run, r.id, org, mentioned=True, competitors=[
            {"name": "ChatGPT", "domain_if_stated": None},
            {"name": "Beta", "domain_if_stated": "beta.com"}])
        s = aggregation.run_summary(db, run)
        assert [c["name"] for c in s["competitors"]] == ["Beta"]   # ChatGPT excluded from SoV
        # raw extraction row is untouched — still holds both
        a = db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id).one()
        assert {c["name"] for c in a.competitors_mentioned} == {"ChatGPT", "Beta"}
    finally:
        db.close()


def test_excluded_entity_does_not_block_brand_detection():
    """A brand whose name collides with an excluded term is still detected as mentioned —
    the exclusion touches competitor aggregation ONLY."""
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org, brand_name="ChatGPT")   # brand collides with an excluded term
        run = _run(db, ps, org)
        r = _result(db, run, prompts[0].id, org)
        _analysis(db, run, r.id, org, mentioned=True)        # brand_mentioned recorded normally
        s = aggregation.run_summary(db, run)
        assert s["mention_rate"] == 100.0                    # brand detection unaffected
    finally:
        db.close()


# =====================================================================
# FIX2 — provider breakdown reflects STORED results, not current config
# =====================================================================
def test_per_provider_reflects_stored_results_not_config(monkeypatch):
    # current config trims to two providers...
    monkeypatch.setattr(settings, "answer_tracking_providers", "anthropic,openai")
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        # ...but this historical run stored THREE providers — all must still render
        for prov in ("anthropic", "openai", "perplexity"):
            r = _result(db, run, prompts[0].id, org, provider=prov)
            _analysis(db, run, r.id, org, mentioned=True)
        s = aggregation.run_summary(db, run)
        assert sorted(p["provider"] for p in s["per_provider"]) == ["anthropic", "openai", "perplexity"]
    finally:
        db.close()


# =====================================================================
# FIX3 — estimate includes extraction cost; total ~ actual run cost
# =====================================================================
def test_estimate_total_matches_actual_within_tolerance(monkeypatch):
    from app.services.answer_tracking import runner, service
    _, body = auth_client()
    org = body["organization"]["id"]
    monkeypatch.setattr(settings, "answer_tracking_runs_per_prompt", 2)
    monkeypatch.setattr(settings, "answer_tracking_rate_anthropic_usd", 0.01)
    monkeypatch.setattr(settings, "answer_tracking_extraction_provider", "anthropic")
    answerer = FakeExtractor("anthropic", model="ans", always="Acme is a good pick.")
    monkeypatch.setattr(at_providers, "enabled_providers", lambda: [answerer])
    _patch_extractor(monkeypatch, FakeExtractor("anthropic", model="ext", always=VALID))
    db = SessionLocal()
    try:
        ps, _ = _set(db, org, prompts=("q1", "q2"))          # 2 prompts, no monitor -> no adaptive
        est = service.estimate_run(db, ps)
        assert est["answer_cost_usd"] == 0.04                # 2 prompts x 1 provider x 2 runs x $0.01
        assert est["extraction_cost_usd"] == 0.04            # 4 answers x 1 extraction x $0.01
        assert est["estimated_cost_usd"] == 0.08             # total includes extraction (was missing)
        run = service.create_run(db, ps)
        asyncio.run(runner.execute_run(db, run))             # answer phase: $0.04
        asyncio.run(extraction.extract_for_run(db, run))     # + extraction: $0.04
        assert abs(est["estimated_cost_usd"] - run.estimated_cost_usd) <= 0.01   # documented tolerance
    finally:
        db.close()


# =====================================================================
# FIX4 — zero-citation diagnosis distinguishes the three causes
# =====================================================================
def _zero_citation_run(db, org, *, citations, search_enabled):
    ps, prompts = _set(db, org)
    run = _run(db, ps, org)
    for prov in ("anthropic", "openai"):
        r = _result(db, run, prompts[0].id, org, provider=prov,
                    citations=citations, search_enabled=search_enabled)
        _analysis(db, run, r.id, org, mentioned=True, urls=[])   # 0 brand URLs
    return run


def test_citation_diagnosis_no_provider_reports():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        run = _zero_citation_run(db, org, citations=None, search_enabled=True)  # None => cannot report
        s = aggregation.run_summary(db, run)
        assert s["citation_count"] == 0
        assert s["citation_diagnosis"]["status"] == "no_provider_reports_citations"
    finally:
        db.close()


def test_citation_diagnosis_searched_not_cited():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        run = _zero_citation_run(db, org, citations=[], search_enabled=True)    # [] => searched, none
        s = aggregation.run_summary(db, run)
        assert s["citation_diagnosis"]["status"] == "searched_not_cited"
    finally:
        db.close()


def test_citation_diagnosis_search_disabled():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        run = _zero_citation_run(db, org, citations=[], search_enabled=False)   # capable but search off
        s = aggregation.run_summary(db, run)
        assert s["citation_diagnosis"]["status"] == "search_disabled"
    finally:
        db.close()


# =====================================================================
# Part B enhancement — structured verdict, recommendations, competitor
# false-positives, gap-to-action, irrelevant-prompt hint
# =====================================================================
GAP_JSON = ('{"has_signal": true, "why": "The site blocks GPTBot so the model never saw it.", '
            '"actions": ["Allow GPTBot in robots.txt", "Add JSON-LD product schema"]}')


def _patch_gap(monkeypatch, fake):
    monkeypatch.setattr(at_providers, "gap_analysis_provider", lambda: fake)


def test_structured_verdict_from_stored_analysis_no_provider_call(monkeypatch):
    """The results endpoint serves the structured verdict (context, url, position,
    recommended_entities) straight from stored analysis — zero provider calls."""
    def boom():   # any answer/extraction provider call is a failure here
        raise AssertionError("no provider may be called to render a stored verdict")
    monkeypatch.setattr(at_providers, "enabled_providers", boom)
    monkeypatch.setattr(at_providers, "extraction_provider", boom)
    client, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        r = _result(db, run, prompts[0].id, org)
        _analysis(db, run, r.id, org, mentioned=False,
                  recommended=[{"name": "Alpha", "domain_if_stated": "alpha.com"}])
        run_id = run.id
    finally:
        db.close()
    got = client.get(f"/prompt-runs/{run_id}/results").json()
    sample = got["prompts"][0]["results"][0]
    assert sample["brand_mentioned"] is False
    assert sample["recommended_entities"] == [{"name": "Alpha", "domain_if_stated": "alpha.com"}]
    assert "position" in sample and "brand_urls_cited" in sample


def test_recommended_entities_preserve_order(monkeypatch):
    ordered = ('{"brand_mentioned": false, "mention_context": null, "sentiment": null, '
               '"brand_urls_cited": [], "position": null, "competitors_mentioned": [], '
               '"recommended_entities": [{"name": "First"}, {"name": "Second"}, {"name": "Third"}]}')
    _patch_extractor(monkeypatch, FakeExtractor(always=ordered))
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        _result(db, run, prompts[0].id, org)
        asyncio.run(extraction.extract_for_run(db, run))
        a = db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run.id).one()
        assert [e["name"] for e in a.recommended_entities] == ["First", "Second", "Third"]
    finally:
        db.close()


def test_example_not_competitor_but_recommendation_is():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        r = _result(db, run, prompts[0].id, org)
        _analysis(db, run, r.id, org, mentioned=False, competitors=[
            {"name": "Air Canada", "domain_if_stated": None, "mention_type": "example"},
            {"name": "Rival", "domain_if_stated": "rival.com", "mention_type": "recommendation"}])
        s = aggregation.run_summary(db, run)
        assert [c["name"] for c in s["competitors"]] == ["Rival"]   # example dropped, solution kept
    finally:
        db.close()


def test_gap_analysis_once_per_zero_mention_prompt_not_per_sample(monkeypatch):
    fake = FakeExtractor(always=GAP_JSON)
    _patch_gap(monkeypatch, fake)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        # THREE samples of the SAME prompt, all zero-mention
        for i in range(3):
            r = _result(db, run, prompts[0].id, org, run_index=i)
            _analysis(db, run, r.id, org, mentioned=False)
        res = asyncio.run(gap_analysis.run_gap_analysis_for_run(db, run))
        assert fake.calls == 1                       # once per PROMPT, not per sample
        assert res["generated"] == 1
        assert db.query(PromptGapAnalysis).filter(PromptGapAnalysis.run_id == run.id).count() == 1
    finally:
        db.close()


def test_gap_analysis_skipped_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "answer_tracking_gap_analysis_enabled", False)
    fake = FakeExtractor(always=GAP_JSON)
    _patch_gap(monkeypatch, fake)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        r = _result(db, run, prompts[0].id, org)
        _analysis(db, run, r.id, org, mentioned=False)
        res = asyncio.run(gap_analysis.run_gap_analysis_for_run(db, run))
        assert fake.calls == 0 and res.get("skipped") == "disabled"
        assert db.query(PromptGapAnalysis).filter(PromptGapAnalysis.run_id == run.id).count() == 0
    finally:
        db.close()


def test_reanalyse_regenerates_with_zero_answer_provider_calls(monkeypatch):
    def no_answer_calls():
        raise AssertionError("reanalyse must NOT call the answer providers")
    monkeypatch.setattr(at_providers, "enabled_providers", no_answer_calls)
    _patch_extractor(monkeypatch, FakeExtractor(always=VALID))
    _patch_gap(monkeypatch, FakeExtractor(always=GAP_JSON))
    client, body = auth_client()                  # registrant is org owner
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        _result(db, run, prompts[0].id, org, raw="Acme is great but so is Rival.")
        run_id = run.id
    finally:
        db.close()
    r = client.post(f"/prompt-runs/{run_id}/reanalyse")
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert db.query(PromptResultAnalysis).filter(PromptResultAnalysis.run_id == run_id).count() == 1
    finally:
        db.close()


def test_irrelevant_prompt_hint_and_does_not_disable():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        ps, prompts = _set(db, org)
        run = _run(db, ps, org)
        r = _result(db, run, prompts[0].id, org)
        # 0 mentions, NO competitors, NO recommendations => off-category hint
        _analysis(db, run, r.id, org, mentioned=False, competitors=[], recommended=[])
        s = aggregation.run_summary(db, run)
        row = next(p for p in s["per_prompt"] if p["prompt_id"] == prompts[0].id)
        assert row["irrelevant_hint"] is True
        # the prompt is NEVER auto-disabled — still active in the DB
        assert db.get(TrackedPrompt, prompts[0].id).is_active is True
    finally:
        db.close()
