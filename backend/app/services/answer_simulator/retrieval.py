"""AEO Answer Simulator — deterministic retrieval.

No vector database, no external call, no ML model. A hand-rolled Okapi BM25 scorer
(the corpus here is a handful to a few hundred short EvidenceUnits per monitor —
far too small to justify a new dependency; `requirements.txt` has none of
rank_bm25/sklearn) with field boosts (title/H1/entity highest, FAQ-question/
heading-question next, body-content passages next, description/FAQ-answer lowest)
and an exact-phrase-match multiplier. Tokenization reuses the SAME normalization the
scanner already applies to page text (`content.py::normalize_content_text`) so a
page's own words are tokenized identically everywhere in the codebase.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from app.scanner.signals.content import content_shingles, normalize_content_text
from app.services.answer_simulator.knowledge_index import EvidenceUnit

# Multiplicative boost applied to a unit's raw BM25 score, by field. Centralized here
# — never re-derived ad hoc elsewhere. Conceptual priority (highest to lowest):
# title > h1 ~ entity_name > faq_question > heading_question > body_chunk >
# meta_description > faq_answer > entity_sameas. Body content sits below headings
# and above description — real passages are useful evidence but must never
# out-rank a page's own explicit title/H1/FAQ/heading signals.
FIELD_BOOST = {
    "title": 3.0,
    "h1": 2.5,
    "entity_name": 2.5,
    "faq_question": 2.2,
    "heading_question": 2.0,
    "body_chunk": 1.6,
    "meta_description": 1.5,
    "faq_answer": 1.3,
    "entity_sameas": 1.0,
}
EXACT_PHRASE_BOOST = 1.5   # applied when the normalized query is a substring of the
                           # unit text or vice versa

# At most this many body_chunk units sharing an identical content fingerprint
# (e.g. an identical cookie notice or footer block appearing on every page) may
# appear in one retrieve() top_k result — protects against repeated boilerplate
# dominating all slots WITHOUT removing anything from the underlying corpus, and
# without hiding which page(s) actually carry the duplicated text.
MAX_DUPLICATE_BODY_CHUNKS_IN_TOPK = 2

_BM25_K1 = 1.5
_BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    return normalize_content_text(text or "").split()


@dataclass
class CorpusStats:
    doc_freq: Counter          # token -> number of units containing it
    n_units: int
    avg_len: float


def build_corpus_stats(units: list[EvidenceUnit]) -> CorpusStats:
    doc_freq: Counter = Counter()
    total_len = 0
    for u in units:
        tokens = set(tokenize(u.text))
        for t in tokens:
            doc_freq[t] += 1
        total_len += len(tokenize(u.text))
    n = len(units)
    return CorpusStats(doc_freq=doc_freq, n_units=n, avg_len=(total_len / n) if n else 0.0)


@dataclass
class ScoredEvidence:
    unit: EvidenceUnit
    score: float
    matched_terms: list[str]


def _idf(stats: CorpusStats, term: str) -> float:
    n = stats.n_units
    df = stats.doc_freq.get(term, 0)
    # Standard BM25 idf with a +1 floor so a term appearing in every unit never goes
    # negative.
    return max(0.0, math.log((n - df + 0.5) / (df + 0.5) + 1.0))


def score_unit(query_tokens: list[str], unit: EvidenceUnit, stats: CorpusStats) -> tuple[float, list[str]]:
    unit_tokens = tokenize(unit.text)
    if not unit_tokens or not query_tokens:
        return 0.0, []
    unit_len = len(unit_tokens)
    term_freq = Counter(unit_tokens)
    query_terms = set(query_tokens)
    matched = [t for t in query_terms if term_freq.get(t)]
    if not matched:
        return 0.0, []
    score = 0.0
    for term in matched:
        tf = term_freq[term]
        idf = _idf(stats, term)
        denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * unit_len / max(stats.avg_len, 1.0))
        score += idf * (tf * (_BM25_K1 + 1)) / max(denom, 1e-9)

    score *= FIELD_BOOST.get(unit.field_name, 1.0)

    normalized_query = normalize_content_text(" ".join(query_tokens))
    normalized_unit = normalize_content_text(unit.text)
    if normalized_query and (normalized_query in normalized_unit or normalized_unit in normalized_query):
        score *= EXACT_PHRASE_BOOST

    return score, sorted(matched)


def _dedup_key(unit: EvidenceUnit) -> str | None:
    """A stable content fingerprint for a body_chunk unit (reuses the scanner's own
    content_shingles() at chunk granularity), used ONLY to cap duplicate/boilerplate
    text in a single top_k result — never as a similarity/relevance score, and never
    used to filter the underlying corpus. None for every other field type (field-
    level evidence like title/H1 is inherently unique enough not to need this)."""
    if unit.field_name != "body_chunk":
        return None
    shingles = content_shingles(normalize_content_text(unit.text))
    return ",".join(str(h) for h in shingles[:8]) if shingles else None


def retrieve(question: str, units: list[EvidenceUnit], stats: CorpusStats,
            *, top_k: int = 8) -> list[ScoredEvidence]:
    """Ranked, deterministic top_k evidence units for `question`. Ties break by
    (full-tier first) then field boost then unit_id, so identical input always
    produces identical output. At most MAX_DUPLICATE_BODY_CHUNKS_IN_TOPK units
    sharing an identical body-content fingerprint are allowed into the final top_k
    (see _dedup_key) — the full scored corpus is never filtered or mutated."""
    query_tokens = tokenize(question)
    scored: list[ScoredEvidence] = []
    for unit in units:
        score, matched = score_unit(query_tokens, unit, stats)
        if score > 0:
            scored.append(ScoredEvidence(unit=unit, score=score, matched_terms=matched))
    scored.sort(key=lambda s: (
        -s.score,
        0 if s.unit.evidence_tier == "full" else 1,
        -FIELD_BOOST.get(s.unit.field_name, 1.0),
        s.unit.unit_id,
    ))

    selected: list[ScoredEvidence] = []
    dup_counts: dict[str, int] = {}
    for s in scored:
        key = _dedup_key(s.unit)
        if key is not None:
            if dup_counts.get(key, 0) >= MAX_DUPLICATE_BODY_CHUNKS_IN_TOPK:
                continue
            dup_counts[key] = dup_counts.get(key, 0) + 1
        selected.append(s)
        if len(selected) >= top_k:
            break
    return selected
