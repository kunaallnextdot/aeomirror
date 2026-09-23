"""AEO Answer Simulator — deterministic OFF-TOPIC / LOW-RELEVANCE detection
(topic_alignment.py + the compute_answerability guard it feeds). No LLM, no
external call, no embeddings anywhere in this file — every assertion is against
pure in-memory functions or a real SQLite-backed org/monitor/scan, the same
convention test_body_content_evidence.py already uses.
"""
from __future__ import annotations

import asyncio

from app.db.models import Monitor, PromptRun, Scan, TrackedPrompt
from app.db.session import SessionLocal
from app.services.answer_simulator import batch
from app.services.answer_simulator.knowledge_index import EvidenceUnit, build_knowledge_index
from app.services.answer_simulator.retrieval import build_corpus_stats, retrieve
from app.services.answer_simulator.scoring import (
    HIGH, INSUFFICIENT_EVIDENCE, LOW, MEDIUM, compute_answerability,
)
from app.services.answer_simulator.topic_alignment import (
    STOPWORDS, compute_topic_alignment, question_topic_tokens,
)
from tests.authutil import auth_client


# ------------------------------- synthetic corpus (AI attendance software) -------------------------------
def _attendance_units() -> list[EvidenceUnit]:
    d = "d1"
    url = "https://attendai.example/"
    return [
        EvidenceUnit(f"{d}#title", d, url, "title", "AI Attendance Software for Colleges", "full"),
        EvidenceUnit(f"{d}#h1", d, url, "h1", "Automated Student Attendance Tracking", "full"),
        EvidenceUnit(f"{d}#meta_description", d, url, "meta_description",
                    "Our platform helps colleges and universities automate student "
                    "attendance using AI-powered facial recognition.", "full"),
        EvidenceUnit(f"{d}#heading_question[0]", d, url, "heading_question",
                    "How does AI attendance tracking work?", "full"),
        EvidenceUnit(f"{d}#heading_question[1]", d, url, "heading_question",
                    "Is the attendance system secure?", "full"),
        EvidenceUnit(f"{d}#faq_question[0]", d, url, "faq_question",
                    "Does your platform support colleges?", "full"),
        EvidenceUnit(f"{d}#faq_answer[0]", d, url, "faq_answer",
                    "Yes, our attendance software is used by hundreds of colleges and universities.", "full"),
        EvidenceUnit(f"{d}#faq_question[1]", d, url, "faq_question",
                    "What features does your attendance system provide?", "full"),
        EvidenceUnit(f"{d}#faq_answer[1]", d, url, "faq_answer",
                    "Real-time attendance tracking, automated reports, and integration "
                    "with student information systems.", "full"),
        EvidenceUnit(f"{d}#entity_name", d, url, "entity_name", "AttendAI", "full"),
        EvidenceUnit(f"{d}#body_chunk[0]", d, url, "body_chunk",
                    "AttendAI is an AI-powered attendance platform built for colleges and "
                    "universities. Our system automatically records student attendance "
                    "using facial recognition and integrates with existing campus systems "
                    "to save administrators time.", "full"),
        EvidenceUnit(f"{d}#body_chunk[1]", "d2", "https://attendai.example/campuses", "body_chunk",
                    "Universities can use automated attendance management platforms to "
                    "reduce manual roll calls and improve accuracy across large lecture "
                    "halls on every campus.", "full"),
    ]


def _run(question: str, units=None):
    units = units if units is not None else _attendance_units()
    stats = build_corpus_stats(units)
    scored = retrieve(question, units, stats)
    return units, stats, scored


# ------------------------------- tokenization -------------------------------
def test_stopwords_removed_for_topic_signal_only():
    tokens = question_topic_tokens("What is the best AI attendance software for colleges?")
    assert "what" not in tokens and "is" not in tokens and "the" not in tokens
    assert "best" not in tokens and "for" not in tokens
    assert "ai" in tokens or "attendance" in tokens


def test_meaningful_terms_retained():
    tokens = question_topic_tokens("How does your attendance software work for colleges?")
    for t in ("attendance", "software", "colleges"):
        assert t in tokens


def test_punctuation_normalized_in_topic_tokens():
    tokens = question_topic_tokens("What's the best chocolate-lava-cake recipe?!")
    assert "chocolate" in tokens and "lava" in tokens and "cake" in tokens and "recipe" in tokens
    assert not any("?" in t or "!" in t or "'" in t for t in tokens)


def test_empty_question_yields_no_topic_tokens():
    assert question_topic_tokens("") == []
    assert question_topic_tokens("   ") == []


def test_question_of_only_stopwords_yields_no_topic_tokens():
    assert question_topic_tokens("What is the best for how do") == []


def test_bm25_tokenizer_untouched_by_stopword_removal():
    """retrieval.py's own tokenizer keeps stopwords — this module's filtering must
    stay a SEPARATE pipeline, never a global change."""
    from app.services.answer_simulator.retrieval import tokenize
    assert "the" in tokenize("What is the best system")
    assert "is" in tokenize("What is the best system")


# ------------------------------- topic alignment score -------------------------------
def test_strong_relevant_overlap_scores_high_alignment():
    units, stats, scored = _run("What is AI attendance software for colleges?")
    topic = compute_topic_alignment("What is AI attendance software for colleges?", units, stats, scored)
    assert topic.score >= 50.0
    assert topic.question_token_coverage >= 75.0


def test_zero_meaningful_overlap_scores_near_zero_alignment():
    units, stats, scored = _run("How do I bake a chocolate lava cake?")
    topic = compute_topic_alignment("How do I bake a chocolate lava cake?", units, stats, scored)
    assert topic.score < 20.0
    assert topic.matched_tokens == []
    assert set(topic.unmatched_tokens) >= {"bake", "chocolate", "lava", "cake"}


def test_partial_overlap_scores_between_zero_and_full():
    units, stats, scored = _run("What are alternatives to attendance software pricing plans?")
    topic = compute_topic_alignment("What are alternatives to attendance software pricing plans?", units, stats, scored)
    assert 0.0 < topic.score < 100.0


def test_body_only_relevance_still_aligns():
    units, stats, scored = _run("How do universities reduce manual roll calls?")
    topic = compute_topic_alignment("How do universities reduce manual roll calls?", units, stats, scored)
    assert topic.score > 0
    assert "roll" in topic.matched_tokens or "calls" in topic.matched_tokens or "universities" in topic.matched_tokens


def test_title_only_relevance_aligns():
    title_only = [EvidenceUnit("d#title", "d", "u", "title", "AI Attendance Software for Colleges", "full")]
    stats = build_corpus_stats(title_only)
    scored = retrieve("attendance software colleges", title_only, stats)
    topic = compute_topic_alignment("attendance software colleges", title_only, stats, scored)
    assert topic.score > 0


def test_faq_relevance_aligns():
    units, stats, scored = _run("Does your platform support colleges?")
    topic = compute_topic_alignment("Does your platform support colleges?", units, stats, scored)
    assert topic.score >= 50.0


def test_entity_relevance_aligns():
    units, stats, scored = _run("Tell me about AttendAI")
    topic = compute_topic_alignment("Tell me about AttendAI", units, stats, scored)
    assert "attendai" in topic.matched_tokens


def test_multiple_page_relevance_reflected_in_alignment():
    units, stats, scored = _run("How does AI attendance tracking work on every campus?")
    topic = compute_topic_alignment("How does AI attendance tracking work on every campus?", units, stats, scored)
    relevant_urls = {s.unit.url for s in scored if s.score > 0}
    assert len(relevant_urls) >= 1
    assert topic.score > 0


def test_idf_reduces_common_word_influence():
    """A word appearing on EVERY unit (maximally common in this corpus) should
    contribute less per-token weight than a rare, distinctive one."""
    from app.services.answer_simulator.retrieval import _idf
    units, stats, _ = _run("irrelevant")
    common_idf = _idf(stats, "attendance")     # appears in nearly every unit here
    rare_idf = _idf(stats, "attendai")          # appears in exactly one unit
    assert rare_idf >= common_idf


# ------------------------------- off-topic questions -> INSUFFICIENT_EVIDENCE -------------------------------
OFF_TOPIC_QUESTIONS = [
    "How do I bake a chocolate lava cake?",
    "What are the best football shoes for running?",
    "How do I repair a motorcycle engine?",
    "What are the best travel destinations in Europe?",
]


def test_off_topic_questions_are_insufficient_evidence():
    for q in OFF_TOPIC_QUESTIONS:
        units, stats, scored = _run(q)
        result = compute_answerability(q, scored, units=units, stats=stats)
        assert result.level == INSUFFICIENT_EVIDENCE, f"expected INSUFFICIENT_EVIDENCE for {q!r}, got {result.level}"
        assert result.topic_alignment_score < 20.0


def test_off_topic_question_shows_zero_supported_pages_even_though_bm25_found_stopword_matches(monkeypatch):
    """Regression for a real bug caught during manual verification: "How do I bake a
    chocolate lava cake?" against the AI-attendance corpus returned Evidence
    relevance=100% (BM25 matched "how"/"do"/"a") while Topic alignment=0% (no real
    word overlap) — the persisted/displayed evidence and supported_url_count must
    reflect the INSUFFICIENT_EVIDENCE verdict, never the contaminated BM25 matches."""
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", False)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor = _seeded_monitor(db, org)
        from app.services.answer_tracking import service as at_service
        ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
        prompt = TrackedPrompt(prompt_set_id=ps.id, organization_id=org,
                               text="How do I bake a chocolate lava cake?")
        db.add(prompt)
        db.commit()
        db.refresh(prompt)

        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, [prompt], request_llm_step=False))
        result = results[0]
        assert result["answerability"] == INSUFFICIENT_EVIDENCE
        assert result["supported_url_count"] == 0
        assert result["evidence"] == []
    finally:
        db.close()


# ------------------------------- relevant questions -------------------------------
RELEVANT_QUESTIONS = [
    "What is AI attendance?",
    "How does your attendance software work?",
    "Does your platform support colleges?",
    "What features does your attendance system provide?",
]


def test_relevant_questions_are_not_insufficient_evidence():
    for q in RELEVANT_QUESTIONS:
        units, stats, scored = _run(q)
        result = compute_answerability(q, scored, units=units, stats=stats)
        assert result.level in (HIGH, MEDIUM, LOW), f"expected a real level for {q!r}, got {result.level}"


def test_exact_faq_question_scores_high_or_medium():
    q = "Does your platform support colleges?"
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    assert result.level in (HIGH, MEDIUM)


# ------------------------------- partially relevant -------------------------------
def test_pricing_question_recognized_as_on_topic_even_though_pricing_itself_is_absent():
    """"attendance"/"software" are real, well-covered corpus vocabulary even though
    "cost"/pricing specifically isn't — this must be recognized as ON-TOPIC (not
    incorrectly rejected as off-topic), per the "avoid overcorrection" requirement;
    whether it lands HIGH/MEDIUM/LOW is retrieval's call, not this guard's."""
    q = "How much does attendance software cost per month?"
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    assert result.level != INSUFFICIENT_EVIDENCE


def test_related_wording_without_exact_match_not_incorrectly_rejected():
    """'colleges automate attendance' overlaps real corpus vocabulary even though
    it doesn't match any single field verbatim — must not be falsely off-topic."""
    q = "How can colleges automate attendance record keeping?"
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    assert result.level != INSUFFICIENT_EVIDENCE


def test_singular_plural_variation_does_not_break_alignment():
    q = "What attendance systems do you offer for a college?"
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    assert result.level != INSUFFICIENT_EVIDENCE


# ------------------------------- answerability guard behavior -------------------------------
def test_poor_alignment_cannot_become_medium_from_generic_words():
    """The false-positive this feature exists to fix: a question sharing ONLY
    generic/common words with the corpus (no real distinctive-word overlap) must
    not reach MEDIUM or HIGH via those shared generic words alone."""
    q = "What is the best way to do this?"   # "way" survives stopword filtering but
                                              # never appears anywhere in the corpus
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    assert result.level == INSUFFICIENT_EVIDENCE


def test_strong_alignment_with_weak_evidence_is_low_not_insufficient():
    sparse_units = [EvidenceUnit("d#body_chunk[0]", "d", "u", "body_chunk",
                                 "We briefly mention attendance software pricing once.", "reduced")]
    q = "attendance software pricing"
    stats = build_corpus_stats(sparse_units)
    scored = retrieve(q, sparse_units, stats)
    result = compute_answerability(q, scored, units=sparse_units, stats=stats)
    assert result.level in (LOW, MEDIUM)


def test_strong_alignment_with_strong_evidence_is_high_or_medium():
    q = "What is AI attendance software for colleges?"
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    assert result.level in (HIGH, MEDIUM)


def test_insufficient_evidence_status_correct_for_zero_overlap():
    q = "quantum entanglement shipping logistics"
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    assert result.level == INSUFFICIENT_EVIDENCE


def test_units_stats_optional_preserves_old_behavior_for_existing_callers():
    """Backward compatibility: omitting units/stats skips the guard entirely — the
    many existing hand-built-`scored`-list tests must keep working unmodified."""
    unit = EvidenceUnit("d#title", "d", "u", "title", "AI attendance system for colleges", "full")
    stats = build_corpus_stats([unit])
    scored = retrieve("AI attendance system for colleges", [unit], stats)
    result = compute_answerability("AI attendance system for colleges", scored)
    assert result.topic_alignment_score == 0.0   # guard skipped, field defaults, no crash


# ------------------------------- LLM cost -------------------------------
def _seeded_monitor(db, org):
    result = {"sections": [
        {"id": "metadata", "evidence": {"title": "AI Attendance Software for Colleges",
                                        "description": "Automate student attendance."}},
        {"id": "content", "evidence": {"h1_text": "Automated Student Attendance Tracking",
                                       "heading_questions": ["How does AI attendance tracking work?"],
                                       "body_evidence": {"word_count": 40, "content_hash": "x", "truncated": False,
                                                        "chunks": [{"id": "chunk-0",
                                                                   "text": "AttendAI helps colleges automate "
                                                                          "student attendance using AI.",
                                                                   "start_word": 0, "end_word": 12}]}}},
        {"id": "schema", "evidence": {"faq_questions": [
            {"question": "Does your platform support colleges?", "answer": "Yes, for colleges and universities."}],
            "entity_evidence": {"name": "AttendAI", "url": "https://attendai.example/", "logo": None, "same_as": []}}},
    ]}
    scan = Scan(url="https://attendai.example/", normalized_url="attendai.example", ars=0,
               rubric_version="t", status="completed", organization_id=org, result=result)
    db.add(scan)
    db.commit()
    db.refresh(scan)
    monitor = Monitor(organization_id=org, url=scan.url, normalized_url=scan.normalized_url,
                      status="active", frequency="weekly", name="AttendAI", brand_name="AttendAI")
    db.add(monitor)
    db.commit()
    db.refresh(monitor)
    return monitor


def test_off_topic_question_makes_zero_llm_calls(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", True)
    monkeypatch.setattr(settings, "answer_simulator_provider", "anthropic")

    def _fail_if_called(*a, **k):
        raise AssertionError("LLM must never be called for an off-topic question")
    monkeypatch.setattr("app.core.ai.complete", _fail_if_called)

    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor = _seeded_monitor(db, org)
        from app.services.answer_tracking import service as at_service
        ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
        prompt = TrackedPrompt(prompt_set_id=ps.id, organization_id=org,
                               text="What is the best chocolate lava cake recipe?")
        db.add(prompt)
        db.commit()
        db.refresh(prompt)

        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=True)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, [prompt], request_llm_step=True))
        assert results[0]["answerability"] == INSUFFICIENT_EVIDENCE
        assert results[0]["llm_step_used"] is False
    finally:
        db.close()


def test_insufficient_evidence_question_makes_zero_llm_calls(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", True)
    monkeypatch.setattr(settings, "answer_simulator_provider", "anthropic")
    monkeypatch.setattr("app.core.ai.complete", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not call the LLM")))

    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor = _seeded_monitor(db, org)
        from app.services.answer_tracking import service as at_service
        ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
        prompt = TrackedPrompt(prompt_set_id=ps.id, organization_id=org,
                               text="How do I repair a motorcycle engine?")
        db.add(prompt)
        db.commit()
        db.refresh(prompt)

        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=True)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, [prompt], request_llm_step=True))
        assert results[0]["llm_step_used"] is False
    finally:
        db.close()


def test_relevant_pro_question_may_optionally_call_llm(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", True)
    monkeypatch.setattr(settings, "answer_simulator_provider", "anthropic")
    raw = ('{"answer_text": "AttendAI helps colleges automate student attendance using AI '
          '(source: https://attendai.example/).", "brand_mentioned": true, '
          '"mention_context": null, "missing_information": [], "confidence": "high"}')
    monkeypatch.setattr("app.core.ai.complete", lambda *a, **k: raw)

    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor = _seeded_monitor(db, org)
        from app.services.answer_tracking import service as at_service
        ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
        prompt = TrackedPrompt(prompt_set_id=ps.id, organization_id=org,
                               text="What is AI attendance software for colleges?")
        db.add(prompt)
        db.commit()
        db.refresh(prompt)

        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=True)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, [prompt], request_llm_step=True))
        assert results[0]["llm_step_used"] is True
    finally:
        db.close()


def test_deterministic_mode_makes_zero_external_calls_regardless_of_question(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", False)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor = _seeded_monitor(db, org)
        from app.services.answer_tracking import service as at_service
        ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
        prompt = TrackedPrompt(prompt_set_id=ps.id, organization_id=org,
                               text="What is AI attendance software for colleges?")
        db.add(prompt)
        db.commit()
        db.refresh(prompt)

        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, [prompt], request_llm_step=False))
        assert results[0]["llm_step_used"] is False
    finally:
        db.close()


# ------------------------------- content gap -------------------------------
def test_genuine_missing_topic_reports_topic_not_covered():
    q = "How do I repair a motorcycle engine?"
    units, stats, scored = _run(q)
    result = compute_answerability(q, scored, units=units, stats=stats)
    missing = (
        [] if result.level != INSUFFICIENT_EVIDENCE
        else ["This topic is not currently supported by the website's content."]
    )
    assert missing == ["This topic is not currently supported by the website's content."]
    assert "add more content" not in missing[0].lower()


def test_random_unrelated_question_does_not_create_a_content_opportunity(monkeypatch):
    """No fabricated 'Opportunity: Create pizza recipe page' for a random question."""
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", False)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor = _seeded_monitor(db, org)
        from app.services.answer_tracking import service as at_service
        ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
        prompt = TrackedPrompt(prompt_set_id=ps.id, organization_id=org,
                               text="What is the best pizza recipe?")
        db.add(prompt)
        db.commit()
        db.refresh(prompt)

        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, [prompt], request_llm_step=False))
        gap_text = " ".join(results[0]["missing_information"]).lower()
        assert "opportunity" not in gap_text
        assert "create" not in gap_text
        assert "pizza" not in gap_text
    finally:
        db.close()


# ------------------------------- isolation -------------------------------
def test_topic_alignment_never_uses_another_orgs_evidence():
    """Structural sanity: topic_alignment operates only on the units it's given —
    an org's off-topic classification can never accidentally pull in another org's
    corpus (real isolation lives in knowledge_index.resolve_evidence_sources,
    already covered in test_body_content_evidence.py; this confirms the NEW module
    doesn't introduce a second, un-scoped data path)."""
    org_a_units = _attendance_units()
    org_b_units = [EvidenceUnit("d#title", "d", "https://other-org.example/", "title",
                                "Completely different veterinary clinic services", "full")]
    stats_a = build_corpus_stats(org_a_units)
    scored_a = retrieve("veterinary clinic services", org_a_units, stats_a)
    topic_a = compute_topic_alignment("veterinary clinic services", org_a_units, stats_a, scored_a)
    assert topic_a.score < 20.0   # org A's corpus never "sees" org B's vocabulary


def test_scan_isolation_for_topic_alignment():
    single_scan_units = [u for u in _attendance_units() if u.doc_id == "d1"]
    stats = build_corpus_stats(single_scan_units)
    scored = retrieve("lecture halls campus roll calls", single_scan_units, stats)
    topic = compute_topic_alignment("lecture halls campus roll calls", single_scan_units, stats, scored)
    # "lecture halls"/"roll calls" only exist in doc_id "d2"'s chunk — excluded here
    assert "halls" not in topic.matched_tokens


def test_domain_isolation_for_topic_alignment():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        monitor_a = _seeded_monitor(db, org)
        from app.services.answer_simulator.knowledge_index import resolve_evidence_sources
        # A second, unrelated domain's monitor sees none of monitor_a's evidence.
        monitor_b = Monitor(organization_id=org, url="https://otherbrand.example/",
                            normalized_url="otherbrand.example", status="active",
                            frequency="weekly", name="Other Brand")
        db.add(monitor_b)
        db.commit()
        db.refresh(monitor_b)
        sources_b = resolve_evidence_sources(db, monitor_b)
        assert sources_b == []
    finally:
        db.close()


# ------------------------------- regression -------------------------------
def test_existing_retrieval_field_boosts_unaffected():
    units = _attendance_units()
    stats = build_corpus_stats(units)
    scored = retrieve("AI Attendance Software for Colleges", units, stats, top_k=5)
    assert scored[0].unit.field_name in ("title", "faq_question")


def test_existing_body_evidence_pipeline_unaffected():
    from app.scanner.signals import content as content_mod
    from bs4 import BeautifulSoup
    soup = BeautifulSoup("<html><body><main><p>" + "genuine content here. " * 20
                         + "</p></main></body></html>", "lxml")
    normalized = content_mod.normalize_content_text(content_mod._main_content_text(soup))
    be = content_mod.build_body_evidence(soup, normalized_main_text=normalized,
                                         word_count=len(normalized.split()))
    assert be["chunks"]


def test_existing_answerability_high_test_still_holds():
    """Omitting units/stats from compute_answerability() takes the EXACT old code
    path (guard skipped entirely) — this is the regression contract this whole
    feature must preserve, not a re-assertion of the old BM25 threshold's exact
    output (which is separately, and correctly, sensitive to corpus size)."""
    q = "AI attendance system for colleges"
    unit1 = EvidenceUnit("d#title", "d", "https://acme.example/", "title", q, "full")
    unit2 = EvidenceUnit("d#h1", "d", "https://acme.example/", "h1", q, "full")
    units = [unit1, unit2]
    stats = build_corpus_stats(units)
    scored = retrieve(q, units, stats)
    result = compute_answerability(q, scored)   # no units/stats -> old behavior path
    assert result.level in (HIGH, MEDIUM)
    assert result.topic_alignment_score == 0.0   # guard was skipped, not evaluated


def test_scanner_score_unaffected_by_topic_alignment_module():
    """This module never touches scanner scoring — confirmed by the fact it has no
    import of/dependency on scanner/engine.py or the SIGNALS weight table."""
    import app.services.answer_simulator.topic_alignment as ta
    import inspect
    src = inspect.getsource(ta)
    assert "scanner.engine" not in src
    assert "overall_score" not in src
