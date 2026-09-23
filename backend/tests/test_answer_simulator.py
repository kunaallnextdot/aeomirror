"""AEO Answer Simulator — deterministic evidence engine, unit-level.

Pure builders (knowledge_index/retrieval/scoring/prompting) are tested directly
against hand-built Scan/EvidenceUnit fixtures (same convention as
test_phase4.py/test_content_intelligence.py); provider adapters are tested with
their transport mocked so NO real network call is ever made; batch orchestration is
tested against a real SQLite-backed org/monitor/scan, asserting persistence shape
and the zero-LLM-by-default cost rule. HTTP-level routing/gating/isolation tests
live in test_answer_simulator_routes.py.
"""
from __future__ import annotations

import asyncio

import pytest

from app.db.models import (
    Monitor, PromptResult, PromptResultAnalysis, PromptRun, SimulatorEvidence, TrackedPrompt,
)
from app.db.session import SessionLocal
from app.services.answer_simulator import batch
from app.services.answer_simulator.knowledge_index import (
    EvidenceDoc, EvidenceUnit, build_knowledge_index, resolve_evidence_sources,
)
from app.services.answer_simulator.prompting import (
    build_facts_payload, build_prompt, validate_against_facts,
)
from app.services.answer_simulator.providers.base import ProviderError
from app.services.answer_simulator.retrieval import ScoredEvidence, build_corpus_stats, retrieve
from app.services.answer_simulator.scoring import (
    HIGH, INSUFFICIENT_EVIDENCE, LOW, MEDIUM, compute_answerability,
)
from tests.authutil import auth_client


# ------------------------------- fixtures -------------------------------
def _scan(db, org, *, url="https://acme.example/", host="acme.example",
         title=None, description=None, h1=None, heading_questions=None,
         faq_questions=None, entity_evidence=None, bulk_pages=None, status="completed"):
    from app.db.models import Scan
    if bulk_pages is not None:
        result = {"bulk": {"pages": bulk_pages, "urls": [p["url"] for p in bulk_pages]}}
    else:
        result = {"sections": [
            {"id": "metadata", "evidence": {"title": title, "description": description}},
            {"id": "content", "evidence": {"h1_text": h1, "heading_questions": heading_questions or []}},
            {"id": "schema", "evidence": {"faq_questions": faq_questions or [],
                                          "entity_evidence": entity_evidence or {}}},
        ]}
    s = Scan(url=url, normalized_url=host, ars=0, rubric_version="t", status=status,
             organization_id=org, result=result)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _monitor(db, org, *, url="https://acme.example/", host="acme.example", name="Acme"):
    m = Monitor(organization_id=org, url=url, normalized_url=host, status="active",
               frequency="weekly", name=name)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# ------------------------------- knowledge_index -------------------------------
def test_single_page_scan_yields_full_tier_evidence():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan(db, org, title="Acme Attendance Software", description="AI attendance for colleges",
             h1="AI Attendance", heading_questions=["What is AI attendance?"],
             faq_questions=[{"question": "Is it free?", "answer": "There is a free tier."}],
             entity_evidence={"name": "Acme Inc", "url": "https://acme.example/", "logo": None, "same_as": []})
        monitor = _monitor(db, org)
        sources = resolve_evidence_sources(db, monitor)
        assert len(sources) == 1
        doc = sources[0]
        assert doc.evidence_tier == "full"
        assert doc.title == "Acme Attendance Software"
        assert doc.faq_pairs == [{"question": "Is it free?", "answer": "There is a free tier."}]
        assert doc.entity["name"] == "Acme Inc"

        units = build_knowledge_index(sources)
        fields = {u.field_name for u in units}
        assert {"title", "meta_description", "h1", "heading_question", "faq_question",
               "faq_answer", "entity_name"} <= fields
    finally:
        db.close()


def test_bulk_scan_pages_yield_reduced_tier_with_no_faq_or_entity():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan(db, org, bulk_pages=[
            {"url": "https://acme.example/a", "title": "Page A", "description": "desc a", "h1": "H1 A"},
            {"url": "https://acme.example/b", "title": "Page B", "description": None, "h1": None},
        ])
        monitor = _monitor(db, org)
        sources = resolve_evidence_sources(db, monitor)
        assert len(sources) == 2
        assert all(d.evidence_tier == "reduced" for d in sources)
        assert all(d.faq_pairs == [] and d.entity is None for d in sources)

        units = build_knowledge_index(sources)
        assert not any(u.field_name in ("faq_question", "faq_answer", "entity_name") for u in units)
    finally:
        db.close()


def test_evidence_sources_scoped_to_the_monitors_own_domain():
    """A scan for a DIFFERENT domain in the same org never leaks into this monitor's
    knowledge index — true isolation, not just a mismatch flag."""
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan(db, org, url="https://acme.example/", host="acme.example", title="Acme page")
        _scan(db, org, url="https://other.example/", host="other.example", title="Other page")
        monitor = _monitor(db, org, url="https://acme.example/", host="acme.example")
        sources = resolve_evidence_sources(db, monitor)
        assert len(sources) == 1
        assert sources[0].url == "https://acme.example/"
    finally:
        db.close()


def test_incomplete_scan_excluded_from_evidence_sources():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan(db, org, title="Not ready", status="running")
        monitor = _monitor(db, org)
        assert resolve_evidence_sources(db, monitor) == []
    finally:
        db.close()


def test_no_scans_yields_empty_knowledge_index():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor = _monitor(db, org)
        assert resolve_evidence_sources(db, monitor) == []
        assert build_knowledge_index([]) == []
    finally:
        db.close()


# ------------------------------- retrieval -------------------------------
def _units():
    doc_id = "s1:https://acme.example/"
    return [
        EvidenceUnit(f"{doc_id}#title", doc_id, "https://acme.example/", "title",
                    "AI attendance system for colleges", "full"),
        EvidenceUnit(f"{doc_id}#h1", doc_id, "https://acme.example/", "h1",
                    "AI attendance for universities", "full"),
        EvidenceUnit(f"{doc_id}#faq_question[0]", doc_id, "https://acme.example/", "faq_question",
                    "What pricing plans are available?", "full"),
        EvidenceUnit(f"{doc_id}#meta_description", doc_id, "https://acme.example/", "meta_description",
                    "Completely unrelated marketing copy about shoes.", "full"),
        EvidenceUnit(f"{doc_id}#entity_name", doc_id, "https://acme.example/", "entity_name",
                    "Acme Inc", "full"),
    ]


def test_title_and_h1_boost_rank_above_unrelated_description():
    units = _units()
    stats = build_corpus_stats(units)
    scored = retrieve("AI attendance system for colleges", units, stats, top_k=5)
    assert scored[0].unit.field_name in ("title", "h1")
    fields = [s.unit.field_name for s in scored]
    assert "meta_description" not in fields   # zero token overlap -> excluded


def test_faq_question_boost_surfaces_pricing_question():
    units = _units()
    stats = build_corpus_stats(units)
    scored = retrieve("What are your pricing plans?", units, stats, top_k=5)
    assert scored and scored[0].unit.field_name == "faq_question"


def test_entity_name_boost_surfaces_brand_query():
    units = _units()
    stats = build_corpus_stats(units)
    scored = retrieve("Acme Inc", units, stats, top_k=5)
    assert scored and scored[0].unit.field_name == "entity_name"


def test_exact_phrase_boost_ranks_full_phrase_match_first():
    units = [
        EvidenceUnit("d#h1", "d", "u", "h1", "best ai attendance system", "full"),
        EvidenceUnit("d#title", "d", "u", "title", "attendance system reviews and ai comparisons", "full"),
    ]
    stats = build_corpus_stats(units)
    scored = retrieve("best ai attendance system", units, stats, top_k=5)
    assert scored[0].unit.field_name == "h1"   # exact-phrase unit wins despite lower field boost


def test_irrelevant_evidence_excluded_entirely():
    units = _units()
    stats = build_corpus_stats(units)
    scored = retrieve("weather forecast tomorrow", units, stats, top_k=5)
    assert scored == []


# ------------------------------- answerability scoring -------------------------------
def test_answerability_high_with_strong_multi_field_evidence():
    units = _units()
    stats = build_corpus_stats(units)
    scored = retrieve("AI attendance system for colleges", units, stats, top_k=5)
    result = compute_answerability("AI attendance system for colleges", scored)
    assert result.level == HIGH


def test_answerability_insufficient_when_nothing_retrieved():
    result = compute_answerability("weather forecast tomorrow", [])
    assert result.level == INSUFFICIENT_EVIDENCE
    assert result.evidence_coverage_pct == 0.0
    assert result.brand_mentioned is False


def test_answerability_low_or_medium_for_weak_single_match():
    units = [EvidenceUnit("d#faq_answer[0]", "d", "u", "faq_answer", "we also mention pricing briefly", "reduced")]
    stats = build_corpus_stats(units)
    scored = retrieve("pricing", units, stats, top_k=5)
    result = compute_answerability("pricing", scored)
    assert result.level in (LOW, MEDIUM)


def test_brand_mention_substring_match_against_retrieved_evidence():
    units = [EvidenceUnit("d#title", "d", "https://acme.example/", "title", "Acme attendance software", "full")]
    stats = build_corpus_stats(units)
    scored = retrieve("Acme attendance software", units, stats, top_k=5)
    result = compute_answerability("Acme attendance software", scored, brand_terms=["Acme"])
    assert result.brand_mentioned is True
    assert result.mention_detection_method == "substring"


def test_brand_mention_false_when_brand_absent_from_evidence():
    units = [EvidenceUnit("d#title", "d", "u", "title", "generic attendance software", "full")]
    stats = build_corpus_stats(units)
    scored = retrieve("generic attendance software", units, stats, top_k=5)
    result = compute_answerability("generic attendance software", scored, brand_terms=["Acme"])
    assert result.brand_mentioned is False


# ------------------------------- prompting (FACTS bounding + validation) -------------------------------
def _scored_evidence():
    unit = EvidenceUnit("d#title", "d", "https://acme.example/pricing", "title", "Acme Pricing", "full")
    return [ScoredEvidence(unit=unit, score=5.0, matched_terms=["pricing"])]


def test_facts_payload_drops_lowest_score_evidence_under_char_cap():
    unit1 = EvidenceUnit("d#a", "d", "https://acme.example/a", "title", "x" * 100, "full")
    unit2 = EvidenceUnit("d#b", "d", "https://acme.example/b", "title", "y" * 100, "full")
    evidence = [ScoredEvidence(unit1, 9.0, []), ScoredEvidence(unit2, 1.0, [])]
    facts = build_facts_payload("q", evidence, char_cap=150)
    assert len(facts["evidence"]) == 1
    assert facts["evidence"][0]["url"] == "https://acme.example/a"   # higher score kept


def test_validate_against_facts_accepts_answer_citing_known_url():
    facts = build_facts_payload("What is the price?", _scored_evidence())
    parsed = {"answer_text": "See https://acme.example/pricing for pricing.",
             "brand_mentioned": True, "mention_context": "pricing page",
             "missing_information": [], "confidence": "high"}
    validated = validate_against_facts(parsed, facts)
    assert validated is not None
    assert validated["answer_text"].startswith("See")


def test_validate_against_facts_rejects_answer_citing_unknown_url():
    facts = build_facts_payload("What is the price?", _scored_evidence())
    parsed = {"answer_text": "See https://not-in-evidence.example/fake for pricing.",
             "brand_mentioned": True, "mention_context": None,
             "missing_information": [], "confidence": "high"}
    assert validate_against_facts(parsed, facts) is None


def test_validate_against_facts_rejects_malformed_confidence():
    facts = build_facts_payload("q", _scored_evidence())
    parsed = {"answer_text": "ok", "confidence": "extremely-high", "missing_information": []}
    assert validate_against_facts(parsed, facts) is None


def test_validate_against_facts_rejects_non_dict():
    facts = build_facts_payload("q", _scored_evidence())
    assert validate_against_facts(None, facts) is None
    assert validate_against_facts("not a dict", facts) is None


def test_build_prompt_embeds_question_and_evidence_urls():
    facts = build_facts_payload("What is the price?", _scored_evidence())
    system, user = build_prompt(facts)
    assert "ONLY" in system
    assert "What is the price?" in user
    assert "https://acme.example/pricing" in user


# ------------------------------- LLM provider adapters (transport mocked, zero network) -------------------------------
def test_local_llm_provider_raises_on_connection_failure(monkeypatch):
    from app.services.answer_simulator.providers.local_llm_provider import LocalLLMProvider
    from app.services.answer_tracking.providers import base as at_base

    async def _boom(*a, **k):
        raise at_base.ProviderError("transport error: connection refused", transient=True)
    monkeypatch.setattr("app.services.answer_simulator.providers.local_llm_provider.post_json", _boom)

    provider = LocalLLMProvider(base_url="http://localhost:11434", model="llama3.2")
    with pytest.raises(ProviderError):
        asyncio.run(provider.generate_answer("What is the price?", _scored_evidence(), timeout=5))


def test_local_llm_provider_succeeds_with_valid_json_response(monkeypatch):
    from app.services.answer_simulator.providers.local_llm_provider import LocalLLMProvider

    async def _fake_post_json(url, *, headers, json, timeout):
        return {"response": (
            '{"answer_text": "See https://acme.example/pricing.", "brand_mentioned": true, '
            '"mention_context": "pricing", "missing_information": [], "confidence": "high"}'
        )}
    monkeypatch.setattr("app.services.answer_simulator.providers.local_llm_provider.post_json", _fake_post_json)

    provider = LocalLLMProvider(base_url="http://localhost:11434", model="llama3.2")
    result = asyncio.run(provider.generate_answer("What is the price?", _scored_evidence(), timeout=5))
    assert result.answer_text.startswith("See")
    assert result.confidence == "high"


def test_local_llm_provider_rejects_response_citing_unknown_url(monkeypatch):
    from app.services.answer_simulator.providers.local_llm_provider import LocalLLMProvider

    async def _fake_post_json(url, *, headers, json, timeout):
        return {"response": (
            '{"answer_text": "See https://fabricated.example/x.", "confidence": "high", '
            '"missing_information": []}'
        )}
    monkeypatch.setattr("app.services.answer_simulator.providers.local_llm_provider.post_json", _fake_post_json)

    provider = LocalLLMProvider(base_url="http://localhost:11434", model="llama3.2")
    with pytest.raises(ProviderError):
        asyncio.run(provider.generate_answer("What is the price?", _scored_evidence(), timeout=5))


def test_anthropic_simulator_provider_raises_when_ai_complete_returns_none(monkeypatch):
    from app.services.answer_simulator.providers import anthropic_simulator_provider as mod
    monkeypatch.setattr(mod.ai, "complete", lambda *a, **k: None)
    provider = mod.AnthropicSimulatorProvider()
    with pytest.raises(ProviderError):
        asyncio.run(provider.generate_answer("What is the price?", _scored_evidence(), timeout=5))


def test_anthropic_simulator_provider_raises_on_malformed_json(monkeypatch):
    from app.services.answer_simulator.providers import anthropic_simulator_provider as mod
    monkeypatch.setattr(mod.ai, "complete", lambda *a, **k: "not json at all")
    provider = mod.AnthropicSimulatorProvider()
    with pytest.raises(ProviderError):
        asyncio.run(provider.generate_answer("What is the price?", _scored_evidence(), timeout=5))


def test_anthropic_simulator_provider_succeeds_and_is_validated(monkeypatch):
    from app.services.answer_simulator.providers import anthropic_simulator_provider as mod
    raw = ('{"answer_text": "See https://acme.example/pricing.", "brand_mentioned": false, '
          '"mention_context": null, "missing_information": [], "confidence": "medium"}')
    monkeypatch.setattr(mod.ai, "complete", lambda *a, **k: raw)
    provider = mod.AnthropicSimulatorProvider()
    result = asyncio.run(provider.generate_answer("What is the price?", _scored_evidence(), timeout=5))
    assert result.confidence == "medium"
    assert result.brand_mentioned is False


def test_simulator_provider_registry_returns_none_when_disabled(monkeypatch):
    from app.config import settings
    from app.services.answer_simulator import providers as sim_providers
    monkeypatch.setattr(settings, "answer_simulator_enabled", False)
    assert sim_providers.simulator_provider() is None


def test_simulator_provider_registry_resolves_local_and_anthropic(monkeypatch):
    from app.config import settings
    from app.services.answer_simulator import providers as sim_providers
    from app.services.answer_simulator.providers.anthropic_simulator_provider import AnthropicSimulatorProvider
    from app.services.answer_simulator.providers.local_llm_provider import LocalLLMProvider

    monkeypatch.setattr(settings, "answer_simulator_enabled", True)
    monkeypatch.setattr(settings, "answer_simulator_provider", "local")
    assert isinstance(sim_providers.simulator_provider(), LocalLLMProvider)

    monkeypatch.setattr(settings, "answer_simulator_provider", "anthropic")
    assert isinstance(sim_providers.simulator_provider(), AnthropicSimulatorProvider)

    monkeypatch.setattr(settings, "answer_simulator_provider", "none")
    assert sim_providers.simulator_provider() is None


# ------------------------------- batch orchestration (real DB, deterministic path) -------------------------------
def _seeded_monitor_and_prompts(db, org, *, texts=("What pricing plans are available?",)):
    _scan(db, org, title="Acme Attendance Software", description="AI attendance for colleges",
         h1="Acme AI Attendance", heading_questions=["What is AI attendance?"],
         faq_questions=[{"question": "What pricing plans are available?",
                         "answer": "We offer Basic and Pro plans."}],
         entity_evidence={"name": "Acme Inc", "url": "https://acme.example/", "logo": None, "same_as": []})
    monitor = _monitor(db, org, name="Acme")
    monitor.brand_name = "Acme"
    db.commit()
    from app.services.answer_tracking import service as at_service
    ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
    prompts = []
    for text in texts:
        p = TrackedPrompt(prompt_set_id=ps.id, organization_id=org, text=text)
        db.add(p)
        prompts.append(p)
    db.commit()
    for p in prompts:
        db.refresh(p)
    return monitor, ps, prompts


def test_deterministic_batch_makes_zero_llm_calls_and_persists_results(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", False)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor, ps, prompts = _seeded_monitor_and_prompts(db, org)
        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, prompts, request_llm_step=False))

        assert len(results) == 1
        assert results[0]["llm_step_used"] is False
        assert results[0]["answerability"] in (HIGH, MEDIUM, LOW, INSUFFICIENT_EVIDENCE)

        stored = db.query(PromptResult).filter(PromptResult.run_id == run.id).all()
        assert len(stored) == 1
        assert stored[0].provider == "simulator"
        assert stored[0].llm_step_used is False
        assert db.get(PromptRun, run.id).status == "completed"
        assert db.get(PromptRun, run.id).estimated_cost_usd == 0.0

        evidence_rows = db.query(SimulatorEvidence).filter(SimulatorEvidence.result_id == stored[0].id).all()
        assert len(evidence_rows) > 0
        analysis = (db.query(PromptResultAnalysis)
                   .filter(PromptResultAnalysis.result_id == stored[0].id).first())
        assert analysis is not None
        assert analysis.extraction_model == "simulator:deterministic"
    finally:
        db.close()


def test_insufficient_evidence_question_skips_llm_step_even_when_requested(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", True)
    monkeypatch.setattr(settings, "answer_simulator_provider", "anthropic")

    def _fail_if_called(*a, **k):
        raise AssertionError("LLM must not be called for an INSUFFICIENT_EVIDENCE question")
    monkeypatch.setattr("app.core.ai.complete", _fail_if_called)

    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor, ps, prompts = _seeded_monitor_and_prompts(
            db, org, texts=("completely unrelated question about spacecraft propulsion",))
        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=True)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, prompts, request_llm_step=True))
        assert results[0]["answerability"] == INSUFFICIENT_EVIDENCE
        assert results[0]["llm_step_used"] is False
    finally:
        db.close()


def test_explain_forces_llm_on_insufficient_evidence_question(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", True)
    monkeypatch.setattr(settings, "answer_simulator_provider", "anthropic")
    raw = ('{"answer_text": "This site does not cover spacecraft propulsion.", '
          '"brand_mentioned": false, "mention_context": null, "missing_information": '
          '["spacecraft propulsion content"], "confidence": "low"}')
    monkeypatch.setattr("app.core.ai.complete", lambda *a, **k: raw)

    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor, ps, prompts = _seeded_monitor_and_prompts(
            db, org, texts=("completely unrelated question about spacecraft propulsion",))
        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, prompts, request_llm_step=False))
        assert results[0]["llm_step_used"] is False

        explained = asyncio.run(batch.simulate_single_question(db, run, monitor, prompts[0]))
        assert explained["llm_step_used"] is True
        assert "spacecraft" in explained["answer_text"].lower()
    finally:
        db.close()


def test_llm_provider_failure_falls_back_to_deterministic_answer(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", True)
    monkeypatch.setattr(settings, "answer_simulator_provider", "anthropic")
    monkeypatch.setattr("app.core.ai.complete", lambda *a, **k: None)   # simulates any provider failure

    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor, ps, prompts = _seeded_monitor_and_prompts(db, org)
        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=True)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, prompts, request_llm_step=True))
        assert results[0]["llm_step_used"] is False   # fell back, run did not fail
        assert db.get(PromptRun, run.id).status == "completed"
    finally:
        db.close()


def test_external_provider_tracking_never_invoked_by_simulator_run(monkeypatch):
    """Regression: a simulator batch must never touch the live provider adapters."""
    from app.services.answer_tracking.providers import anthropic_provider, openai_provider, perplexity_provider

    def _fail(*a, **k):
        raise AssertionError("Provider Tracking adapter must never be called by the simulator")
    for mod in (anthropic_provider, openai_provider, perplexity_provider):
        monkeypatch.setattr(mod, "query", _fail, raising=False)

    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor, ps, prompts = _seeded_monitor_and_prompts(db, org)
        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, prompts, request_llm_step=False))
        assert len(results) == 1
    finally:
        db.close()


def test_no_scanner_score_touched_by_simulator(monkeypatch):
    """The simulator never reads or writes the scanner's overall_score / signal
    weights — it is an entirely separate subsystem."""
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", False)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan = _scan(db, org, title="Acme", description="d", h1="H1")
        original_result = dict(scan.result)
        monitor, ps, prompts = _seeded_monitor_and_prompts(db, org)
        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        asyncio.run(batch.run_simulation_batch(db, run, monitor, prompts, request_llm_step=False))
        db.refresh(scan)
        assert scan.result.get("overall_score") == original_result.get("overall_score")
    finally:
        db.close()
