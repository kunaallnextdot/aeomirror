"""AEO Answer Simulator — deterministic Answerability scoring.

Pure function, no LLM, no network. Given a question and its already-retrieved
evidence (see retrieval.py), estimates how well the site's own scanned content
could support an answer. Never a claim about Google ranking or actual AI-provider
behavior — see PART 4/21 of the product spec this implements.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.services.answer_simulator.knowledge_index import EvidenceUnit
from app.services.answer_simulator.retrieval import CorpusStats, ScoredEvidence
from app.services.answer_simulator.topic_alignment import compute_topic_alignment

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# --- centralized weights (documented once, used only here) ---
TOP_SCORE_HIGH = 8.0            # top retrieved unit's boosted BM25 score
TOP_SCORE_MEDIUM = 3.0
MIN_UNITS_FOR_COVERAGE = 2      # >=2 distinct relevant units before calling it "covered"
FULL_TIER_BONUS = 1.15          # applied to the top score when >=1 relevant unit is full-tier
MIN_URLS_FOR_DIVERSITY = 2      # >=2 distinct supporting URLs before calling it "diverse"
MULTI_PAGE_BONUS = 1.10         # applied alongside FULL_TIER_BONUS when >=2 distinct URLs
                                 # support the answer — evidence DIVERSITY, never word count

# --- off-topic / low-relevance guard (calibrated against the test matrix in
# test_topic_alignment.py — RELEVANT/PARTIALLY RELEVANT/OFF-TOPIC questions against
# a synthetic AI-attendance-software corpus). Runs BEFORE the positive scoring
# above can ever produce MEDIUM/HIGH — see compute_answerability(). ---
OFF_TOPIC_THRESHOLD = 20.0      # topic_alignment_score below this -> INSUFFICIENT_EVIDENCE
MIN_QUESTION_COVERAGE = 25.0    # question_token_coverage below this, TOGETHER WITH...
MIN_EVIDENCE_SCORE = TOP_SCORE_MEDIUM   # ...a top BM25 score below this -> INSUFFICIENT_EVIDENCE


@dataclass
class AnswerabilityResult:
    level: str                  # HIGH | MEDIUM | LOW | INSUFFICIENT_EVIDENCE
    evidence_coverage_pct: float
    brand_mentioned: bool
    mention_detection_method: str   # always "substring" here — never confused with an LLM judgment
    supported_url_count: int = 0    # distinct pages whose evidence supports this answer
    # "Does this question align with the site's evidence at all" — a SEPARATE signal
    # from evidence_coverage_pct/evidence_relevance (how STRONGLY retrieval matched).
    # See topic_alignment.py's module docstring for why these stay separate.
    topic_alignment_score: float = 0.0
    question_token_coverage: float = 0.0


def _insufficient(topic_score: float = 0.0, topic_coverage: float = 0.0) -> AnswerabilityResult:
    return AnswerabilityResult(
        level=INSUFFICIENT_EVIDENCE, evidence_coverage_pct=0.0,
        brand_mentioned=False, mention_detection_method="substring",
        supported_url_count=0, topic_alignment_score=topic_score,
        question_token_coverage=topic_coverage,
    )


def compute_answerability(question: str, scored: list[ScoredEvidence], *,
                          brand_terms: list[str] | None = None,
                          units: list[EvidenceUnit] | None = None,
                          stats: CorpusStats | None = None) -> AnswerabilityResult:
    """`units`/`stats` are OPTIONAL — when omitted, the off-topic guard below is
    skipped entirely and this behaves exactly as before (existing pure-unit-list
    call sites/tests that construct a `scored` list by hand, with no real question
    corpus, are unaffected). Real callers (services/answer_simulator/batch.py) always
    pass both, since the guard needs the FULL knowledge index (not just the already-
    filtered top-k `scored` list) to know whether the question's words appear
    ANYWHERE in the site's evidence."""
    topic = compute_topic_alignment(question, units, stats, scored) if units is not None and stats is not None else None

    if topic is not None:
        if topic.meaningful_token_count == 0:
            return _insufficient(topic.score, topic.question_token_coverage)
        if topic.score < OFF_TOPIC_THRESHOLD:
            return _insufficient(topic.score, topic.question_token_coverage)
        top_score_for_guard = scored[0].score if scored else 0.0
        if topic.question_token_coverage < MIN_QUESTION_COVERAGE and top_score_for_guard < MIN_EVIDENCE_SCORE:
            return _insufficient(topic.score, topic.question_token_coverage)

    relevant = [s for s in scored if s.score > 0]
    if not relevant:
        return _insufficient(topic.score if topic else 0.0, topic.question_token_coverage if topic else 0.0)

    top = relevant[0].score
    distinct_fields = len({s.unit.field_name for s in relevant})
    distinct_urls = len({s.unit.url for s in relevant})
    # Three roughly-equal signals: how MUCH relevant evidence, how many DIFFERENT
    # evidence types, and how many DIFFERENT pages support it — never word count,
    # which is not a quality signal.
    coverage_pct = min(100.0, round(
        (len(relevant) / MIN_UNITS_FOR_COVERAGE) * 34
        + (distinct_fields / 3) * 33
        + (distinct_urls / MIN_URLS_FOR_DIVERSITY) * 33, 1))

    tier_bonus = FULL_TIER_BONUS if any(s.unit.evidence_tier == "full" for s in relevant) else 1.0
    diversity_bonus = MULTI_PAGE_BONUS if distinct_urls >= MIN_URLS_FOR_DIVERSITY else 1.0
    effective_top = top * tier_bonus * diversity_bonus

    if effective_top >= TOP_SCORE_HIGH and len(relevant) >= MIN_UNITS_FOR_COVERAGE:
        level = HIGH
    elif effective_top >= TOP_SCORE_MEDIUM:
        level = MEDIUM
    else:
        level = LOW

    brand_mentioned = _substring_brand_match(relevant, brand_terms or [])

    return AnswerabilityResult(
        level=level, evidence_coverage_pct=coverage_pct,
        brand_mentioned=brand_mentioned, mention_detection_method="substring",
        supported_url_count=distinct_urls,
        topic_alignment_score=topic.score if topic else 0.0,
        question_token_coverage=topic.question_token_coverage if topic else 0.0,
    )


def _domain_root(url: str | None) -> str:
    if not url:
        return ""
    host = url.strip().lower()
    if "//" not in host:
        host = "//" + host
    host = (urlparse(host).netloc or "").split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")


def _substring_brand_match(relevant: list[ScoredEvidence], brand_terms: list[str]) -> bool:
    """Case-insensitive substring/alias/domain-root match against the concatenated
    text of the retrieved evidence units. Deliberately weaker than the LLM-based
    mention detection Provider Tracking uses (see extraction.py) — never conflate the
    two; the caller always stamps `mention_detection_method: "substring"`."""
    terms = [t.strip().lower() for t in brand_terms if t and t.strip()]
    if not terms:
        return False
    hay = " ".join(s.unit.text for s in relevant).lower()
    hay += " " + " ".join(_domain_root(s.unit.url) for s in relevant)
    return any(term in hay for term in terms)
