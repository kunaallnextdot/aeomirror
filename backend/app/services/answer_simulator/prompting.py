"""AEO Answer Simulator — FACTS-bounded LLM prompt + post-hoc validation.

Mirrors the exact pattern already proven in app/reports/ai_writer.py: build a
char-capped FACTS payload, a strict "answer ONLY from the evidence" system prompt,
then VALIDATE the model's JSON output against the facts it was actually given before
ever using it. Any answer that cites a URL not present in FACTS is rejected — the
caller falls back to the deterministic, evidence-only answer rather than ship an
unverifiable claim. This is the concrete mechanism that satisfies "no invented
facts/claims" for the optional LLM step.
"""
from __future__ import annotations

import re

from app.services.answer_simulator.retrieval import ScoredEvidence

_PAYLOAD_CHAR_CAP = 4000

_SYSTEM_PROMPT = (
    "You are an AEO answer simulator. Answer the user's question ONLY using the "
    "supplied EVIDENCE — verbatim text already extracted from the website's own "
    "scanned pages. Do not invent facts, statistics, customers, pricing, rankings, "
    "or product capabilities. Every factual claim must be traceable to one of the "
    "EVIDENCE entries; when you rely on an entry, cite its url. If the EVIDENCE does "
    "not support an answer, say so plainly in `missing_information` rather than "
    "guessing. Respond with a single JSON object with exactly these keys: "
    '"answer_text" (string), "brand_mentioned" (boolean or null if you cannot tell), '
    '"mention_context" (string or null), "missing_information" (array of strings), '
    '"confidence" ("high"|"medium"|"low").'
)

_URL_RE = re.compile(r"https?://[^\s\)\]\"']+")


def build_facts_payload(question: str, evidence: list[ScoredEvidence], *,
                        char_cap: int = _PAYLOAD_CHAR_CAP) -> dict:
    """Bounded FACTS: {"question", "evidence": [{"url","field","text","score"}, ...]}.
    Each evidence text is already capped at its source length (title<=120,
    description<=300, h1<=200, etc — see knowledge_index.py). Drops the LOWEST-score
    evidence first if the serialized payload would exceed `char_cap`."""
    entries = [
        {"url": s.unit.url, "field": s.unit.field_name, "text": s.unit.text,
         "score": round(s.score, 3)}
        for s in sorted(evidence, key=lambda s: -s.score)
    ]
    while entries:
        size = len(question) + sum(len(e["url"]) + len(e["field"]) + len(e["text"]) + 20 for e in entries)
        if size <= char_cap:
            break
        entries.pop()  # lowest score last, per the sort above
    return {"question": question, "evidence": entries}


def build_prompt(facts: dict) -> tuple[str, str]:
    user_content = (
        f"QUESTION: {facts['question']}\n\n"
        f"EVIDENCE (from the website's own scanned pages):\n"
        + "\n".join(
            f"- [{e['field']}] {e['text']} (source: {e['url']})"
            for e in facts["evidence"]
        )
    )
    return _SYSTEM_PROMPT, user_content


def validate_against_facts(parsed: dict | None, facts: dict) -> dict | None:
    """Rejects (returns None) any parsed answer that: is missing required keys, cites
    a URL not present in FACTS, or has a malformed `confidence`/`missing_information`
    shape. A None here means the caller MUST fall back to the deterministic-only
    answer — never ship an unvalidated LLM claim."""
    if not isinstance(parsed, dict):
        return None
    answer_text = parsed.get("answer_text")
    if not isinstance(answer_text, str) or not answer_text.strip():
        return None
    confidence = parsed.get("confidence")
    if confidence not in ("high", "medium", "low"):
        return None
    missing = parsed.get("missing_information")
    if missing is None:
        missing = []
    if not isinstance(missing, list) or not all(isinstance(m, str) for m in missing):
        return None
    brand_mentioned = parsed.get("brand_mentioned")
    if brand_mentioned is not None and not isinstance(brand_mentioned, bool):
        return None
    mention_context = parsed.get("mention_context")
    if mention_context is not None and not isinstance(mention_context, str):
        return None

    allowed_urls = {e["url"] for e in facts["evidence"]}
    # Strip trailing sentence punctuation a natural-language answer commonly leaves
    # attached to a URL ("...see https://x.example/page.") before comparing — this is
    # normalization of formatting, not a relaxation of the FACTS check itself.
    cited_urls = {u.rstrip(").,;:!?]\"'") for u in _URL_RE.findall(answer_text)}
    if cited_urls - allowed_urls:
        return None   # cited something outside the given evidence — reject, don't trust

    return {
        "answer_text": answer_text.strip(),
        "brand_mentioned": brand_mentioned,
        "mention_context": mention_context,
        "missing_information": missing,
        "confidence": confidence,
    }
