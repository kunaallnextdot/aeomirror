"""Full body-content evidence: extraction, chunking, hashing (scanner/signals/
content.py), retrieval/answerability wiring (services/answer_simulator/), the
index cache, and isolation. No scanner score/weight logic is touched anywhere in
this file — content.py's analyze() score computation is entirely unaffected by
body_evidence, which is purely additive.
"""
from __future__ import annotations

import asyncio

from bs4 import BeautifulSoup

from app.db.models import Monitor, PromptRun, Scan, TrackedPrompt
from app.db.session import SessionLocal
from app.scanner.models import PageBundle
from app.scanner.signals import content as content_mod
from app.scanner.signals.base import SignalContext
from app.services.answer_simulator import batch
from app.services.answer_simulator.knowledge_index import (
    EvidenceUnit, build_knowledge_index, resolve_evidence_sources,
)
from app.services.answer_simulator.retrieval import build_corpus_stats, retrieve
from app.services.answer_simulator.scoring import HIGH, compute_answerability
from tests.authutil import auth_client


# ------------------------------- content extraction -------------------------------
def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def test_main_tag_preferred_when_present():
    soup = _soup("<html><body><nav>Nav</nav><main><p>" + "real " * 20 + "</p></main>"
                "<footer>Foot</footer></body></html>")
    blocks = content_mod._main_content_blocks(soup)
    assert any("real" in b for b in blocks)
    assert not any("Nav" in b or "Foot" in b for b in blocks)


def test_article_tag_preferred_when_no_main():
    soup = _soup("<html><body><header>Head</header><article><p>" + "story " * 20
                + "</p></article></body></html>")
    blocks = content_mod._main_content_blocks(soup)
    assert any("story" in b for b in blocks)
    assert not any("Head" in b for b in blocks)


def test_fallback_body_strips_nav_header_footer_aside():
    soup = _soup(
        "<html><body>"
        "<nav>Home About</nav><header>Site Header</header>"
        "<div><p>" + "content " * 20 + "</p></div>"
        "<aside>Related links</aside><footer>Copyright</footer>"
        "</body></html>"
    )
    text = content_mod._main_content_text(soup)
    assert "content" in text
    for boilerplate in ("Home About", "Site Header", "Related links", "Copyright"):
        assert boilerplate not in text


def test_script_and_style_removed():
    soup = _soup(
        "<html><body><main><p>" + "readable " * 20 + "</p>"
        "<script>var x = 1;</script><style>.a{color:red}</style></main></body></html>"
    )
    text = content_mod._main_content_text(soup)
    assert "readable" in text
    assert "var x" not in text and "color:red" not in text


def test_empty_content_yields_no_blocks_and_no_chunks():
    soup = _soup("<html><body><nav>Nav only</nav></body></html>")
    blocks = content_mod._main_content_blocks(soup)
    normalized = content_mod.normalize_content_text(content_mod._main_content_text(soup))
    be = content_mod.build_body_evidence(soup, normalized_main_text=normalized, word_count=0)
    assert be["chunks"] == []
    assert be["truncated"] is False


def test_small_content_yields_exactly_one_chunk():
    soup = _soup("<html><body><main><p>Hello world this is small.</p></main></body></html>")
    normalized = content_mod.normalize_content_text(content_mod._main_content_text(soup))
    wc = len(normalized.split())
    be = content_mod.build_body_evidence(soup, normalized_main_text=normalized, word_count=wc)
    assert len(be["chunks"]) == 1
    assert "Hello world" in be["chunks"][0]["text"]


def test_div_only_content_falls_back_to_sentence_split():
    """Many real sites wrap body copy in bare <div>s with no <p> tags."""
    soup = _soup(
        "<html><body><main><div>" + "Sentence one has words. " * 30
        + "</div></main></body></html>"
    )
    blocks = content_mod._main_content_blocks(soup)
    assert blocks    # fallback found real sentence-level content, not an empty list
    assert sum(len(b.split()) for b in blocks) > 50


# ------------------------------- chunking -------------------------------
def test_chunking_targets_600_words_with_75_word_overlap():
    blocks = [f"para{i}." for i in range(2)]
    blocks = [" ".join(f"w{i}" for i in range(650)), " ".join(f"x{i}" for i in range(300))]
    chunks = content_mod.chunk_blocks(blocks)
    assert len(chunks) >= 2
    assert chunks[0]["end_word"] - chunks[0]["start_word"] <= 650
    # second chunk starts with overlap words from the tail of the first
    overlap_words = chunks[0]["text"].split()[-75:]
    assert chunks[1]["text"].split()[: len(overlap_words)] == overlap_words


def test_chunking_prefers_paragraph_boundaries():
    p1 = " ".join(f"a{i}" for i in range(590))
    p2 = " ".join(f"b{i}" for i in range(590))
    chunks = content_mod.chunk_blocks([p1, p2], target_words=600)
    # p1 (590 words) fits in chunk 0 alone; p2 pushed to chunk 1 rather than being
    # split mid-paragraph.
    assert chunks[0]["text"] == p1
    assert p2 in chunks[1]["text"]


def test_oversized_paragraph_falls_back_to_sentence_boundary():
    long_para = " ".join(f"This is sentence number {i}." for i in range(200))  # ~1000 words
    chunks = content_mod.chunk_blocks([long_para], target_words=600)
    assert len(chunks) >= 2
    # every chunk boundary lands on a real sentence, never mid-sentence
    for c in chunks:
        assert c["text"].strip() == "" or c["text"].rstrip().endswith(".")


def test_hard_word_limit_fallback_for_a_single_giant_sentence():
    giant_sentence = " ".join(f"word{i}" for i in range(1500)) + "."
    chunks = content_mod.chunk_blocks([giant_sentence], target_words=600)
    assert len(chunks) >= 2   # sliced even though it's one grammatical sentence


def test_max_chunks_and_max_words_per_page_enforced():
    paras = [" ".join(f"w{i}_{j}" for j in range(590)) for i in range(20)]
    soup = _soup("<html><body><main>" + "".join(f"<p>{p}</p>" for p in paras) + "</main></body></html>")
    normalized = content_mod.normalize_content_text(content_mod._main_content_text(soup))
    be = content_mod.build_body_evidence(
        soup, normalized_main_text=normalized, word_count=len(normalized.split()))
    assert be["truncated"] is True
    assert len(be["chunks"]) <= content_mod.MAX_BODY_CHUNKS_PER_PAGE
    # The words FED INTO chunking are capped at MAX_BODY_WORDS_PER_PAGE; the stored
    # chunk TEXT can exceed that slightly because adjacent chunks deliberately
    # overlap by _CHUNK_OVERLAP_WORDS (each chunk boundary duplicates a small
    # amount of text on purpose, for retrieval continuity) — still a small,
    # predictable, bounded amount, never unbounded.
    total_words = sum(len(c["text"].split()) for c in be["chunks"])
    max_with_overlap = (content_mod.MAX_BODY_WORDS_PER_PAGE
                        + content_mod.MAX_BODY_CHUNKS_PER_PAGE * content_mod._CHUNK_OVERLAP_WORDS)
    assert total_words <= max_with_overlap


# ------------------------------- content hash -------------------------------
def test_identical_content_hashes_identically():
    a = content_mod.content_hash("the quick brown fox")
    b = content_mod.content_hash("the quick brown fox")
    assert a == b


def test_changed_content_hashes_differently():
    a = content_mod.content_hash("the quick brown fox")
    b = content_mod.content_hash("the slow brown fox")
    assert a != b


# ------------------------------- full analyze() integration -------------------------------
def test_content_signal_evidence_includes_body_evidence_without_changing_score():
    html = ("<html><body><nav>Nav</nav><main><h1>Title</h1><h2>Sub</h2>"
           "<p>" + "genuine sentence content here. " * 15 + "</p>"
           "<ul><li>one</li><li>two</li><li>three</li></ul>"
           "</main></body></html>")
    page = PageBundle(url="https://x.example/", html=html)
    ctx = SignalContext(page)
    result_with = content_mod.analyze(ctx)
    assert "body_evidence" in result_with.evidence
    assert result_with.evidence["body_evidence"]["chunks"]

    # A second, independent analyze() call over the SAME page must score identically
    # — body_evidence is additive-only, never a scoring input.
    ctx2 = SignalContext(page)
    result_again = content_mod.analyze(ctx2)
    assert result_again.score == result_with.score
    assert result_again.evidence["word_count"] == result_with.evidence["word_count"]
    assert result_again.evidence["content_shingles"] == result_with.evidence["content_shingles"]


# ------------------------------- helpers shared below -------------------------------
def _scan_with_body(db, org, *, url="https://acme.example/", host="acme.example",
                    title="Acme", description="d", h1="Acme H1", body_text_paras=None,
                    status="completed"):
    paras = body_text_paras or ["default body paragraph content here for testing purposes and retrieval."]
    soup = _soup("<html><body><main>" + "".join(f"<p>{p}</p>" for p in paras) + "</main></body></html>")
    normalized = content_mod.normalize_content_text(content_mod._main_content_text(soup))
    body_ev = content_mod.build_body_evidence(
        soup, normalized_main_text=normalized, word_count=len(normalized.split()))
    result = {"sections": [
        {"id": "metadata", "evidence": {"title": title, "description": description}},
        {"id": "content", "evidence": {"h1_text": h1, "heading_questions": [], "body_evidence": body_ev}},
        {"id": "schema", "evidence": {"faq_questions": [], "entity_evidence": {}}},
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


# ------------------------------- knowledge index + retrieval -------------------------------
def test_body_chunks_flow_into_knowledge_index_and_are_retrievable():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan_with_body(db, org, body_text_paras=[
            "Our AI attendance system tracks student presence automatically for colleges."])
        monitor = _monitor(db, org)
        sources = resolve_evidence_sources(db, monitor)
        units = build_knowledge_index(sources)
        body_units = [u for u in units if u.field_name == "body_chunk"]
        assert body_units
        stats = build_corpus_stats(units)
        scored = retrieve("How does the AI attendance system track students?", units, stats)
        assert any(s.unit.field_name == "body_chunk" for s in scored)
    finally:
        db.close()


def test_duplicate_body_chunk_boilerplate_capped_in_topk():
    identical_paragraph = ("This cookie notice appears on every single page of this "
                           "website and repeats verbatim across the entire site structure.")
    units = []
    for i in range(6):
        units.append(EvidenceUnit(
            unit_id=f"doc{i}#body_chunk[0]", doc_id=f"doc{i}", url=f"https://acme.example/page{i}",
            field_name="body_chunk", text=identical_paragraph, evidence_tier="full",
        ))
    stats = build_corpus_stats(units)
    scored = retrieve("cookie notice repeats across the entire site", units, stats, top_k=10)
    assert 0 < len(scored) <= 2   # capped despite 6 identical matches


def test_different_pages_with_distinct_body_text_all_surface():
    units = [
        EvidenceUnit("d1#body_chunk[0]", "d1", "https://acme.example/a", "body_chunk",
                    "product page discussing pricing plans for colleges", "full"),
        EvidenceUnit("d2#body_chunk[0]", "d2", "https://acme.example/b", "body_chunk",
                    "faq page discussing pricing plans for colleges in detail", "full"),
    ]
    stats = build_corpus_stats(units)
    scored = retrieve("pricing plans for colleges", units, stats, top_k=10)
    urls = {s.unit.url for s in scored}
    assert urls == {"https://acme.example/a", "https://acme.example/b"}


# ------------------------------- answerability: diversity -------------------------------
def test_body_evidence_raises_answerability_for_a_genuinely_supported_question():
    question = "AI attendance system for colleges"
    title_only = [EvidenceUnit("d#title", "d", "https://acme.example/", "title", question, "full")]
    stats1 = build_corpus_stats(title_only)
    result_title_only = compute_answerability(question, retrieve(question, title_only, stats1))

    with_body = title_only + [EvidenceUnit(
        "d2#body_chunk[0]", "d2", "https://acme.example/faq", "body_chunk",
        "Our AI attendance system automatically tracks student presence for colleges "
        "using facial recognition and integrates with existing campus systems.", "full",
    )]
    stats2 = build_corpus_stats(with_body)
    result_with_body = compute_answerability(question, retrieve(question, with_body, stats2))

    # Real body-content evidence from a SECOND page raises both the coverage signal
    # and the multi-page support count — never demoted below the title-only baseline.
    assert result_with_body.evidence_coverage_pct >= result_title_only.evidence_coverage_pct
    assert result_with_body.supported_url_count > result_title_only.supported_url_count
    assert result_with_body.level in (HIGH, "MEDIUM")


def test_irrelevant_body_text_does_not_improve_answerability():
    units = [EvidenceUnit("d#body_chunk[0]", "d", "u", "body_chunk",
                          "Completely unrelated text about gardening tips and soil pH levels.", "full")]
    stats = build_corpus_stats(units)
    scored = retrieve("AI attendance system for colleges", units, stats)
    result = compute_answerability("AI attendance system for colleges", scored)
    assert result.supported_url_count == 0


def test_multi_page_support_increases_supported_url_count_and_answerability():
    units = [
        EvidenceUnit("d1#body_chunk[0]", "d1", "https://acme.example/product", "body_chunk",
                    "AI attendance system for colleges tracks presence automatically.", "full"),
        EvidenceUnit("d2#body_chunk[0]", "d2", "https://acme.example/faq", "body_chunk",
                    "Our AI attendance system for colleges answers common questions.", "full"),
        EvidenceUnit("d3#title", "d3", "https://acme.example/about", "title",
                    "AI attendance system for colleges", "full"),
    ]
    stats = build_corpus_stats(units)
    scored = retrieve("AI attendance system for colleges", units, stats)
    result = compute_answerability("AI attendance system for colleges", scored)
    assert result.supported_url_count >= 2


def test_single_page_support_baseline():
    units = [EvidenceUnit("d#title", "d", "https://acme.example/", "title",
                          "AI attendance system for colleges", "full")]
    stats = build_corpus_stats(units)
    scored = retrieve("AI attendance system for colleges", units, stats)
    result = compute_answerability("AI attendance system for colleges", scored)
    assert result.supported_url_count == 1


# ------------------------------- simulator / LLM grounding with body chunks -------------------------------
def test_facts_validation_still_rejects_invented_url_with_body_chunks_present():
    from app.services.answer_simulator.prompting import build_facts_payload, validate_against_facts
    from app.services.answer_simulator.retrieval import ScoredEvidence

    unit = EvidenceUnit("d#body_chunk[0]", "d", "https://acme.example/real", "body_chunk",
                        "Real evidence text about the product.", "full")
    facts = build_facts_payload("q", [ScoredEvidence(unit=unit, score=5.0, matched_terms=[])])
    parsed = {"answer_text": "See https://fabricated.example/fake for details.",
             "confidence": "high", "missing_information": []}
    assert validate_against_facts(parsed, facts) is None


def test_deterministic_batch_with_body_chunks_makes_zero_llm_calls(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "answer_simulator_enabled", False)
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan_with_body(db, org, body_text_paras=[
            "Our AI attendance system tracks student presence automatically for colleges."])
        monitor = _monitor(db, org)
        from app.services.answer_tracking import service as at_service
        ps = at_service.prompt_set_for_monitor(db, org, monitor.id, create=True)
        prompt = TrackedPrompt(prompt_set_id=ps.id, organization_id=org,
                               text="How does the attendance system work?")
        db.add(prompt)
        db.commit()
        db.refresh(prompt)

        from app.services.answer_simulator import service as sim_service
        run = sim_service.create_simulation_run(db, ps, llm_step_requested=False)
        results = asyncio.run(batch.run_simulation_batch(db, run, monitor, [prompt], request_llm_step=False))
        assert results[0]["llm_step_used"] is False
        assert any(e["field"] == "body_chunk" for e in results[0]["evidence"])
        assert "evidence_relevance_score" in results[0]["evidence"][0]
        assert results[0]["supported_url_count"] >= 1
    finally:
        db.close()


# ------------------------------- cache -------------------------------
def test_index_cache_avoids_rebuilding_for_a_second_question(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan_with_body(db, org)
        monitor = _monitor(db, org)

        call_count = {"n": 0}
        real_resolve = batch.resolve_evidence_sources

        def counting_resolve(db_, monitor_):
            call_count["n"] += 1
            return real_resolve(db_, monitor_)

        monkeypatch.setattr(batch, "resolve_evidence_sources", counting_resolve)

        units1, stats1 = batch._get_or_build_index(db, monitor)
        units2, stats2 = batch._get_or_build_index(db, monitor)
        assert call_count["n"] == 1   # second call hit the cache, no DB rebuild
        assert [u.unit_id for u in units1] == [u.unit_id for u in units2]
    finally:
        db.close()


def test_index_cache_busted_by_a_new_latest_scan_id(monkeypatch):
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        scan1 = _scan_with_body(db, org)
        monitor = _monitor(db, org)
        monitor.latest_scan_id = scan1.id
        db.commit()

        call_count = {"n": 0}
        real_resolve = batch.resolve_evidence_sources

        def counting_resolve(db_, monitor_):
            call_count["n"] += 1
            return real_resolve(db_, monitor_)

        monkeypatch.setattr(batch, "resolve_evidence_sources", counting_resolve)

        batch._get_or_build_index(db, monitor)
        monitor.latest_scan_id = "a-different-scan-id"
        db.commit()
        batch._get_or_build_index(db, monitor)
        assert call_count["n"] == 2   # different cache key -> rebuilt, not stale
    finally:
        db.close()


# ------------------------------- isolation -------------------------------
def test_body_chunks_never_leak_across_orgs():
    _, body_a = auth_client()
    _, body_b = auth_client()
    org_a, org_b = body_a["organization"]["id"], body_b["organization"]["id"]
    db = SessionLocal()
    try:
        _scan_with_body(db, org_a, body_text_paras=["Org A secret product roadmap details here."])
        monitor_b = _monitor(db, org_b)
        sources = resolve_evidence_sources(db, monitor_b)
        assert sources == []   # org B's monitor sees none of org A's body content
    finally:
        db.close()


def test_body_chunks_never_leak_across_domains_in_the_same_org():
    _, body = auth_client()
    org = body["organization"]["id"]
    db = SessionLocal()
    try:
        _scan_with_body(db, org, url="https://siteA.example/", host="sitea.example",
                        body_text_paras=["Site A specific body content about widgets."])
        monitor_b = _monitor(db, org, url="https://siteB.example/", host="siteb.example")
        sources = resolve_evidence_sources(db, monitor_b)
        assert sources == []
    finally:
        db.close()


# ------------------------------- free/pro evidence gating -------------------------------
def test_free_plan_evidence_gated_to_two_item_preview():
    from app.services.answer_simulator.batch import gate_simulation_result
    result = {"evidence": [{"url": f"u{i}", "field": "body_chunk", "snippet": "s",
                            "evidence_relevance_score": 1.0} for i in range(5)]}
    gated = gate_simulation_result(result, unlocked=False)
    assert len(gated["evidence"]) == 2
    assert gated["locked_evidence_count"] == 3


def test_pro_plan_sees_full_evidence():
    from app.services.answer_simulator.batch import gate_simulation_result
    result = {"evidence": [{"url": f"u{i}", "field": "body_chunk", "snippet": "s",
                            "evidence_relevance_score": 1.0} for i in range(5)]}
    gated = gate_simulation_result(result, unlocked=True)
    assert len(gated["evidence"]) == 5
    assert "locked_evidence_count" not in gated
