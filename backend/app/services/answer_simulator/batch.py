"""AEO Answer Simulator — batch orchestration.

Cost-optimized by design: the knowledge index and corpus stats are built ONCE per
batch (not once per question — this is the actual O(pages) vs O(questions) saving),
a deterministic evidence-grounded answer is ALWAYS composed, and the optional LLM
step is invoked per-question only when explicitly requested AND the question has
enough evidence to be worth explaining (an INSUFFICIENT_EVIDENCE question is skipped
by default — the "Explain why" escalation is the one place a caller can force it).
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.core.cache import TTLCache
from app.db.models import (
    EXTRACTION_COMPLETE, RUN_COMPLETED,
    Monitor, PromptResult, PromptResultAnalysis, PromptRun, SimulatorEvidence, TrackedPrompt,
)
from app.services.answer_simulator.knowledge_index import (
    EvidenceUnit, build_knowledge_index, resolve_evidence_sources,
)
from app.services.answer_simulator.providers import simulator_provider
from app.services.answer_simulator.providers.base import ProviderError
from app.services.answer_simulator.retrieval import CorpusStats, ScoredEvidence, build_corpus_stats, retrieve
from app.services.answer_simulator.scoring import (
    INSUFFICIENT_EVIDENCE, LOW, TOP_SCORE_HIGH, AnswerabilityResult, compute_answerability,
)
from app.services.answer_tracking.providers import rate_for

_CONFIDENCE_FOR_LEVEL = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low", INSUFFICIENT_EVIDENCE: "low"}

# The knowledge index (evidence units + BM25 corpus stats) is expensive to rebuild
# per request (a DB query + JSON-parsing every matching Scan.result blob), but cheap
# to rebuild per QUESTION — so it's cached across requests, not just across a single
# batch. Reuses the existing Redis-or-in-memory TTLCache (app/core/cache.py) rather
# than new infra. Keyed by the monitor's latest_scan_id, which changes on a rescan —
# a short TTL bounds staleness for the narrower edge case of a DIFFERENT same-domain
# scan changing without touching latest_scan_id.
_index_cache = TTLCache(settings.answer_simulator_index_cache_ttl_seconds)


def _serialize_index(units: list[EvidenceUnit], stats: CorpusStats) -> dict:
    return {
        "units": [dataclasses.asdict(u) for u in units],
        "doc_freq": dict(stats.doc_freq),
        "n_units": stats.n_units,
        "avg_len": stats.avg_len,
    }


def _deserialize_index(data: dict) -> tuple[list[EvidenceUnit], CorpusStats]:
    units = [EvidenceUnit(**u) for u in data["units"]]
    stats = CorpusStats(doc_freq=Counter(data["doc_freq"]), n_units=data["n_units"], avg_len=data["avg_len"])
    return units, stats


def _get_or_build_index(db: Session, monitor: Monitor) -> tuple[list[EvidenceUnit], CorpusStats]:
    cache_key = f"answer-simulator-index:{monitor.id}:{monitor.latest_scan_id}"
    cached = _index_cache.get(cache_key)
    if cached is not None:
        try:
            return _deserialize_index(cached)
        except (KeyError, TypeError):
            pass   # cache shape mismatch (e.g. a code change) — fall through and rebuild
    sources = resolve_evidence_sources(db, monitor)
    units = build_knowledge_index(sources)
    stats = build_corpus_stats(units)
    _index_cache.set(cache_key, _serialize_index(units, stats))
    return units, stats


def _brand_terms(monitor: Monitor) -> list[str]:
    terms = [monitor.brand_name, monitor.brand_domain, monitor.name, monitor.normalized_url]
    terms.extend(monitor.brand_aliases or [])
    return [t for t in terms if t]


def _deterministic_answer_text(scored: list[ScoredEvidence], level: str) -> str:
    if level == INSUFFICIENT_EVIDENCE or not scored:
        # Deliberately neutral: never "the website is bad", never a Google/AI-visibility
        # claim — just an honest statement that this site's own evidence doesn't
        # support this question (see topic_alignment.py's module docstring).
        return "We couldn't find meaningful evidence on the website for this question."
    lines = []
    if level == LOW:
        lines.append("We found some relevant information, but the website does not "
                     "provide enough evidence for a strong answer.")
    else:
        lines.append("Based on the website's currently scanned content:")
    for s in scored[:5]:
        lines.append(f"- {s.unit.text} (source: {s.unit.url})")
    return "\n".join(lines)


def _citations_for(scored: list[ScoredEvidence]) -> list[str] | None:
    if not scored:
        return None   # nothing retrieved — the answer step found no evidence to cite
    seen: list[str] = []
    for s in scored:
        if s.unit.url not in seen:
            seen.append(s.unit.url)
    return seen


def format_simulation_result(result: PromptResult, analysis: PromptResultAnalysis | None,
                             evidence_rows: list[SimulatorEvidence], prompt_text: str) -> dict:
    sorted_evidence = sorted(evidence_rows, key=lambda e: e.rank)
    # The top retrieved unit's boosted BM25 score, normalized against the SAME
    # TOP_SCORE_HIGH threshold scoring.py already uses for the HIGH answerability
    # cutoff — a genuine retrieval-relevance percentage, distinct from
    # evidence_coverage_pct (breadth/diversity). Never a Google/ranking score.
    top_score = sorted_evidence[0].score if sorted_evidence else 0.0
    evidence_relevance_pct = round(min(100.0, (top_score / TOP_SCORE_HIGH) * 100), 1) if sorted_evidence else 0.0
    return {
        "prompt_id": result.prompt_id,
        "question": prompt_text,
        "answerability": result.answerability,
        "evidence_coverage_pct": result.evidence_coverage_pct,
        "evidence_relevance_pct": evidence_relevance_pct,
        # "Does this question align with the site's evidence at all" — see
        # topic_alignment.py. Distinct from evidence_relevance_pct (how strongly
        # retrieval matched) and evidence_coverage_pct (breadth/diversity).
        "topic_alignment_score": result.topic_alignment_score,
        "question_token_coverage": result.question_token_coverage,
        # Distinct supporting pages, derived from the persisted evidence rows (no new
        # column) — the UI's "Supported by N page(s)" line.
        "supported_url_count": len({e.url for e in sorted_evidence}),
        "answer_text": result.raw_response,
        "brand_mentioned": analysis.brand_mentioned if analysis else None,
        "mention_context": analysis.mention_context if analysis else None,
        "evidence": [
            # Named explicitly to avoid any "ranking"/"Google score" implication —
            # this is a retrieval relevance score, nothing else.
            {"url": e.url, "field": e.field, "snippet": e.snippet,
             "evidence_relevance_score": e.score}
            for e in sorted_evidence
        ],
        "missing_information": (analysis.missing_information if analysis else None) or [],
        "confidence": analysis.simulator_confidence if analysis else None,
        "llm_step_used": result.llm_step_used,
        "model": result.model,
    }


def gate_simulation_result(result: dict, *, unlocked: bool, preview_count: int = 2) -> dict:
    """Free callers get a REAL, full-fidelity preview of the top `preview_count`
    evidence items (never a client-only blur) plus a locked count; Pro sees the
    full retrieved set. Applied identically to POST .../run's inline response and
    GET .../results — server-side trim, same convention as gate_question_bank."""
    if unlocked:
        return result
    evidence = result.get("evidence") or []
    if len(evidence) <= preview_count:
        return result
    return {**result, "evidence": evidence[:preview_count],
           "locked_evidence_count": len(evidence) - preview_count}


async def _simulate_one(db: Session, run: PromptRun, prompt: TrackedPrompt, *, units, stats,
                        brand_terms: list[str], provider, request_llm_step: bool,
                        semaphore: asyncio.Semaphore, cost_tracker: list[float],
                        force_llm: bool = False) -> dict:
    scored = retrieve(prompt.text, units, stats, top_k=settings.answer_simulator_max_evidence_units)
    answerability: AnswerabilityResult = compute_answerability(
        prompt.text, scored, brand_terms=brand_terms, units=units, stats=stats)

    # Evidence shown to the user / persisted as citations reflects OUR OWN confidence
    # judgment. Once the answerability guard has judged this question
    # INSUFFICIENT_EVIDENCE — whether from zero retrieval, off-topic detection, or
    # weak+low-coverage evidence (see topic_alignment.py) — the underlying BM25
    # matches may themselves be an artifact of shared generic words and must never
    # be presented as "supporting evidence" or counted toward "Supported by N pages".
    # The optional LLM step (below) still receives the full RAW `scored` list when it
    # runs (e.g. the explicit "Explain why" escalation) — its own FACTS validator
    # (prompting.py) is what prevents it from fabricating a grounded-looking claim
    # from weak evidence, so it's given the complete picture regardless.
    display_scored = scored if answerability.level != INSUFFICIENT_EVIDENCE else []

    answer_text = _deterministic_answer_text(display_scored, answerability.level)
    missing_information = (
        [] if answerability.level != INSUFFICIENT_EVIDENCE
        # Never "add more content" / never fabricates a content opportunity for a
        # genuinely unrelated question — see topic_alignment.py's module docstring.
        else ["This topic is not currently supported by the website's content."]
    )
    confidence = _CONFIDENCE_FOR_LEVEL[answerability.level]
    mention_context = None
    llm_used = False
    model_used = "deterministic"
    brand_mentioned = answerability.brand_mentioned

    should_call_llm = request_llm_step and provider is not None and (
        force_llm or answerability.level != INSUFFICIENT_EVIDENCE
    )
    if should_call_llm:
        async with semaphore:
            try:
                sim_answer = await provider.generate_answer(
                    prompt.text, scored, timeout=settings.answer_simulator_timeout_seconds)
                answer_text = sim_answer.answer_text
                missing_information = sim_answer.missing_information
                confidence = sim_answer.confidence
                mention_context = sim_answer.mention_context
                if sim_answer.brand_mentioned is not None:
                    brand_mentioned = sim_answer.brand_mentioned
                llm_used = True
                model_used = f"{provider.name}:{sim_answer.model}"
                if provider.name == "anthropic":
                    cost_tracker.append(rate_for("anthropic"))
            except ProviderError:
                pass   # fall back to the already-composed deterministic answer

    if llm_used:
        # A real, FACTS-validated LLM answer succeeded (the explicit "Explain why"
        # escalation) — show the actual evidence that backed it, overriding the
        # pre-LLM empty display list above.
        display_scored = scored

    result = PromptResult(
        run_id=run.id, prompt_id=prompt.id, organization_id=run.organization_id,
        monitor_id=run.monitor_id,
        provider=f"simulator:{provider.name}" if llm_used else "simulator",
        model=model_used, run_index=0, is_adaptive_run=False, search_enabled=False,
        raw_response=answer_text, citations=_citations_for(display_scored),
        latency_ms=None, token_usage=None, error=None,
        answerability=answerability.level, evidence_coverage_pct=answerability.evidence_coverage_pct,
        topic_alignment_score=answerability.topic_alignment_score,
        question_token_coverage=answerability.question_token_coverage,
        llm_step_used=llm_used,
    )
    db.add(result)
    db.flush()   # assign result.id before FK rows below

    evidence_rows = []
    for i, s in enumerate(display_scored, start=1):
        row = SimulatorEvidence(
            result_id=result.id, organization_id=run.organization_id, monitor_id=run.monitor_id,
            url=s.unit.url, field=s.unit.field_name, snippet=s.unit.text, score=s.score,
            matched_terms=s.matched_terms, rank=i,
        )
        db.add(row)
        evidence_rows.append(row)

    analysis = PromptResultAnalysis(
        result_id=result.id, run_id=run.id, organization_id=run.organization_id,
        monitor_id=run.monitor_id, brand_mentioned=brand_mentioned, mention_context=mention_context,
        sentiment=None, brand_urls_cited=None, competitors_mentioned=None, recommended_entities=None,
        position=None, extraction_failed=False,
        extraction_model=model_used if llm_used else "simulator:deterministic",
        raw_output=None, missing_information=missing_information, simulator_confidence=confidence,
    )
    db.add(analysis)

    return format_simulation_result(result, analysis, evidence_rows, prompt.text)


async def run_simulation_batch(db: Session, run: PromptRun, monitor: Monitor,
                               questions: list[TrackedPrompt], *,
                               request_llm_step: bool) -> list[dict]:
    units, stats = _get_or_build_index(db, monitor)
    brand_terms = _brand_terms(monitor)
    provider = simulator_provider() if request_llm_step else None
    semaphore = asyncio.Semaphore(max(1, settings.answer_tracking_max_concurrency))
    cost_tracker: list[float] = []

    results = await asyncio.gather(*(
        _simulate_one(db, run, prompt, units=units, stats=stats, brand_terms=brand_terms,
                      provider=provider, request_llm_step=request_llm_step,
                      semaphore=semaphore, cost_tracker=cost_tracker)
        for prompt in questions
    ))

    now = datetime.utcnow()
    run.started_at = run.started_at or now
    run.completed_at = now
    run.status = RUN_COMPLETED
    run.extraction_status = EXTRACTION_COMPLETE
    run.total_calls = len(questions)
    run.failed_calls = 0
    run.estimated_cost_usd = round(sum(cost_tracker), 6)
    db.commit()

    return list(results)


async def simulate_single_question(db: Session, run: PromptRun, monitor: Monitor,
                                   prompt: TrackedPrompt) -> dict:
    """The "Explain why" escalation (task spec: the ONE place a user can force the
    optional LLM step on an otherwise-skipped INSUFFICIENT_EVIDENCE question).
    Persists a new PromptResult/SimulatorEvidence/PromptResultAnalysis row for this
    one question WITHOUT touching the parent run's aggregate stats (total_calls/
    status/estimated_cost_usd) — those describe the original batch, not this
    supplementary follow-up call. Reuses the SAME cached index as
    run_simulation_batch() — a real fix for "don't rebuild for every question"."""
    units, stats = _get_or_build_index(db, monitor)
    brand_terms = _brand_terms(monitor)
    provider = simulator_provider()
    semaphore = asyncio.Semaphore(1)
    cost_tracker: list[float] = []

    out = await _simulate_one(
        db, run, prompt, units=units, stats=stats, brand_terms=brand_terms,
        provider=provider, request_llm_step=True, semaphore=semaphore,
        cost_tracker=cost_tracker, force_llm=True,
    )
    if cost_tracker:
        run.estimated_cost_usd = round((run.estimated_cost_usd or 0.0) + sum(cost_tracker), 6)
    db.commit()
    return out
