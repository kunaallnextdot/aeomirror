"""AEO Answer Simulator — deterministic OFF-TOPIC / LOW-RELEVANCE detection.

A SEPARATE signal layered on top of retrieval.py's BM25 scoring (never a rewrite of
it), specifically to catch the false-positive case where an unrelated question
retrieves nonzero evidence purely through common/generic words ("best", "for",
"how", "system"...) rather than any real topical overlap with the site's own
content. No LLM, no embeddings, no external call, no new dependency — deterministic
token-overlap + the SAME BM25 idf formula retrieval.py already uses (reused, not
duplicated) against a stopword-filtered view of the question that is used ONLY by
this module; the underlying BM25 corpus/tokenization in retrieval.py is untouched.

Terminology discipline (do not drift from this): "topic alignment" / "evidence
relevance" / "question coverage" / "insufficient evidence" — never "AI understands
the question", "semantic understanding", or "intent classification". This module
does not understand anything; it counts which of the question's real words
literally appear anywhere in the site's own scanned evidence, weighted by how rare
those words are across that evidence (idf) so common site-wide vocabulary
("software", "system") can't manufacture relevance either.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.answer_simulator.knowledge_index import EvidenceUnit
from app.services.answer_simulator.retrieval import CorpusStats, ScoredEvidence, _idf, tokenize

# Versioned so a future change to the set is a visible, deliberate diff — never a
# silent drift. English function/conversational words only; deliberately does NOT
# include domain words like "system"/"software"/"platform" (a word being generic
# ON THIS SITE is instead caught by the idf weighting below, not by hardcoding it
# here — a word that's rare elsewhere but common on THIS corpus should still count).
STOPWORD_SET_VERSION = "v1"
STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "what", "which", "who", "whom", "whose", "this", "that", "these", "those",
    "am", "do", "does", "did", "doing", "have", "has", "had", "having",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
    "of", "in", "on", "at", "by", "for", "with", "about", "against", "between",
    "into", "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "out", "off", "over", "under", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "just", "and", "or", "but",
    "if", "as", "it", "its", "your", "you", "we", "our", "they", "their", "i", "me",
    "my", "he", "she", "him", "her", "his", "them", "us", "yourself", "does",
    "best", "good", "great", "new", "get", "got", "like", "want", "need",
    "please", "thanks", "also", "really", "much", "many", "make", "made",
})
_MIN_TOPIC_TOKEN_LEN = 3

# --- centralized weights for topic_alignment_score (0-100); documented once ---
# Sum of the five component weights is 1.0 (before the exact-phrase bonus, applied
# on top and capped at 100).
WEIGHTED_OVERLAP_WEIGHT = 0.45   # idf-weighted: DO the question's real words exist,
                                 # weighted by how distinctive they are on this site
RAW_OVERLAP_WEIGHT = 0.15        # simple fraction of question tokens found at all
TOP_RELEVANCE_WEIGHT = 0.20      # retrieval's own top BM25+boost score, normalized
PAGE_DIVERSITY_WEIGHT = 0.10     # how many distinct pages support it
FIELD_DIVERSITY_WEIGHT = 0.10    # how many distinct evidence types support it
EXACT_PHRASE_BONUS = 10.0        # the question's own meaningful-token phrase appears verbatim

# Normalization references — reuse retrieval/scoring's own existing constants rather
# than inventing new ones, so this signal stays consistent with the BM25/answerability
# scale already in use.
_TOP_RELEVANCE_REFERENCE = 8.0   # == scoring.TOP_SCORE_HIGH; a local constant (not an
                                 # import) to avoid a scoring.py <-> topic_alignment.py
                                 # import cycle — scoring.py imports THIS module.
_PAGE_DIVERSITY_REFERENCE = 2    # == scoring.MIN_URLS_FOR_DIVERSITY, same reason.


@dataclass
class TopicAlignmentResult:
    score: float                        # 0-100
    question_token_coverage: float      # 0-100: % of meaningful question tokens
                                         # found ANYWHERE in the site's evidence
    matched_tokens: list[str] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)
    meaningful_token_count: int = 0


def question_topic_tokens(question: str) -> list[str]:
    """Stopword-filtered, deduped, order-preserving tokens for TOPIC ALIGNMENT
    classification only — a separate pipeline from retrieval.py's BM25 tokenization
    (which intentionally keeps every token, no stopword removal, so existing
    retrieval behavior is unaffected by this addition)."""
    seen: list[str] = []
    for t in tokenize(question):
        if len(t) < _MIN_TOPIC_TOKEN_LEN or t in STOPWORDS:
            continue
        if t not in seen:
            seen.append(t)
    return seen


def _evidence_token_set(units: list[EvidenceUnit]) -> set[str]:
    """Every token (same base tokenization as BM25) appearing anywhere in the
    knowledge index — built from the already-in-memory, already-cached units, never
    a new fetch/parse."""
    tokens: set[str] = set()
    for u in units:
        tokens.update(tokenize(u.text))
    return tokens


def compute_topic_alignment(question: str, units: list[EvidenceUnit], stats: CorpusStats,
                            scored: list[ScoredEvidence]) -> TopicAlignmentResult:
    """Deterministic, in-memory, zero external calls. Answers "does this question
    actually align with the website's own evidence" — a DIFFERENT question from
    "how strongly did retrieval match" (that's retrieval.py's BM25 score /
    evidence_relevance_score), kept separate on purpose (see module docstring)."""
    q_tokens = question_topic_tokens(question)
    if not q_tokens:
        return TopicAlignmentResult(score=0.0, question_token_coverage=0.0,
                                    matched_tokens=[], unmatched_tokens=[],
                                    meaningful_token_count=0)

    evidence_tokens = _evidence_token_set(units)
    matched = [t for t in q_tokens if t in evidence_tokens]
    unmatched = [t for t in q_tokens if t not in evidence_tokens]

    raw_overlap = len(matched) / len(q_tokens)
    # idf reuses retrieval.py's OWN formula (never a second implementation) — a
    # question word that's common across THIS site's evidence (e.g. "software")
    # contributes less than one that's rare/distinctive (e.g. an entity name).
    weights = {t: _idf(stats, t) for t in q_tokens}
    total_weight = sum(weights.values()) or 1.0
    weighted_overlap = sum(weights[t] for t in matched) / total_weight

    top_relevance = scored[0].score if scored else 0.0
    top_relevance_component = min(1.0, top_relevance / _TOP_RELEVANCE_REFERENCE)

    relevant = [s for s in scored if s.score > 0]
    distinct_urls = len({s.unit.url for s in relevant})
    distinct_fields = len({s.unit.field_name for s in relevant})
    page_component = min(1.0, distinct_urls / _PAGE_DIVERSITY_REFERENCE)
    field_component = min(1.0, distinct_fields / 3)

    # retrieval.py's BM25 corpus intentionally keeps stopwords (see its own module
    # docstring) — so `scored`/top_relevance/page/field can be entirely an artifact
    # of shared GENERIC words ("how", "do", "a"...) between an off-topic question and
    # the site's evidence, with zero real topical overlap. Gate the whole retrieval-
    # derived component on there being at least one MEANINGFUL match — otherwise a
    # completely unrelated question could still borrow retrieval "confidence" it
    # never earned, defeating the point of this module (see STEP 9 of the spec:
    # generic words alone must never manufacture relevance).
    retrieval_component = 0.0
    if matched:
        retrieval_component = (
            top_relevance_component * TOP_RELEVANCE_WEIGHT
            + page_component * PAGE_DIVERSITY_WEIGHT
            + field_component * FIELD_DIVERSITY_WEIGHT
        )

    score = 100.0 * (
        weighted_overlap * WEIGHTED_OVERLAP_WEIGHT
        + raw_overlap * RAW_OVERLAP_WEIGHT
        + retrieval_component
    )

    if len(q_tokens) >= 2:
        phrase = " ".join(q_tokens)
        if any(phrase in " ".join(tokenize(u.text)) for u in units):
            score += EXACT_PHRASE_BONUS

    score = round(min(100.0, max(0.0, score)), 1)
    coverage = round(raw_overlap * 100, 1)

    return TopicAlignmentResult(
        score=score, question_token_coverage=coverage,
        matched_tokens=matched, unmatched_tokens=unmatched,
        meaningful_token_count=len(q_tokens),
    )
