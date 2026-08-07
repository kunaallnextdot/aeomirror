"""AI-written report narrative (Feature A) — PAID reports only.

Turns the deterministic rule-based report into a business-facing narrative via Claude:
an executive summary, per-issue "why it matters" insights, and an ordered action plan.
`write_narrative(report)` returns a dict merged into the report under `report["ai"]`,
or None on any failure (AI off / API error / malformed / failed validation) so the
caller keeps the existing template text.

ACCURACY IS THE PRIORITY. The model only ever sees a bounded FACTS payload built from
the already-computed report, and everything it returns is validated against those FACTS
before use: issue ids must exist, and any "NN/100" score it cites must appear in FACTS.
Anything that fails validation is dropped (or the whole narrative is rejected) rather
than shipping an invented number or finding. This module never gates access — the
caller decides who qualifies.
"""
from __future__ import annotations

import json
import re

from app.config import settings
from app.core import ai

# Backwards-compatible alias: app/scanner/ai_content.py imports this shared extractor.
_parse_json = ai.parse_json

_STR_CAP = 300          # truncate every string field sent to the model
_PAYLOAD_CAP = 8000     # hard cap on the serialized FACTS JSON
_EVIDENCE_KEYS = 6      # scalar evidence values kept per issue
_FREE_INSIGHTS = 3      # Free tier: insights for the top-N issues only
_FREE_MAX_TOKENS = 700  # Free tier is shorter (no action plan) → cheaper
# Score-like patterns whose numbers must be present in FACTS ("42/100", "scored 42").
_SCORE_RES = (re.compile(r"(\d{1,3})\s*/\s*100"),
              re.compile(r"scored\s+(\d{1,3})", re.IGNORECASE))

# Verbatim grounding contract — every claim must be derivable from FACTS.
_SYSTEM = (
    "You are writing an AI-visibility audit for a real website. You will be given FACTS "
    "extracted by a deterministic scanner. Every claim you make must be derivable from "
    "FACTS. Do NOT invent numbers, page counts, dates, competitor names, traffic "
    "estimates, or findings that are not in FACTS. If information is absent, do not "
    "speculate — omit it. Never contradict a score in FACTS. Only reference issues by "
    "the exact `id` values provided. Write plainly for a website owner: no marketing "
    "fluff, no hedging filler. Respond with ONLY the JSON object described, no preamble, "
    "no markdown fences."
)

_SUMMARY_SPEC = (
    '{"executive_summary": string (120-180 words, specific to THIS site; it MUST state '
    "the overall score as NN/100 and name the weakest and strongest areas exactly as "
    "given in FACTS.sections),\n"
    ' "issue_insights": [{"id": string (must be one of the FACTS.issues[].id values), '
    '"why_it_matters": string (2-3 sentences of concrete business / AI-visibility '
    'context), "priority_rationale": string (1 sentence on why it ranks where it does)}]'
)

# Free tier: summary + the top-3 issue insights only (no action plan).
_INSTRUCTIONS_FREE = (
    "Using ONLY the FACTS above, return a single JSON object with exactly these keys:\n"
    + _SUMMARY_SPEC
    + " for the THREE most important issues only}\n"
    "Output JSON only."
)

# Paid tier: insights for every issue plus an ordered action plan.
_INSTRUCTIONS_PAID = (
    "Using ONLY the FACTS above, return a single JSON object with exactly these keys:\n"
    + _SUMMARY_SPEC
    + " covering EVERY issue in FACTS,\n"
    ' "action_plan": [5 to 7 ordered, concrete steps as strings, each tied to an issue '
    "id in FACTS]}\n"
    "Output JSON only."
)


def _clip(value, cap: int = _STR_CAP) -> str:
    return ("" if value is None else str(value))[:cap]


def _as_int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _evidence(findings) -> dict:
    """A few scalar evidence values (counts / booleans / short strings) the signal
    already captured — never anything the model could misread as a new finding."""
    out: dict = {}
    if isinstance(findings, dict):
        for k, v in findings.items():
            if isinstance(v, bool) or isinstance(v, (int, float)):
                out[k] = v
            elif isinstance(v, str):
                out[k] = v[:_STR_CAP]
            else:
                continue
            if len(out) >= _EVIDENCE_KEYS:
                break
    return out


def _facts(report: dict) -> tuple[dict, set[str], set[int]]:
    """Build the bounded FACTS payload plus the id/score sets used for validation.

    Issues are added highest-priority first (recommendations are pre-sorted) until the
    serialized payload would exceed the cap — so the LOWEST-priority issues are the ones
    dropped, and only issues actually sent are considered valid downstream."""
    sc = report.get("scorecard") or {}
    overall = sc.get("overall_score")
    score_set: set[int] = set()
    if _as_int(overall) is not None:
        score_set.add(_as_int(overall))

    sections = []
    for c in (sc.get("category_scores") or []):
        s = c.get("score")
        sections.append({"id": c.get("category"), "label": c.get("category"),
                         "score": s, "status": c.get("status")})
        if _as_int(s) is not None:
            score_set.add(_as_int(s))

    facts: dict = {
        "domain": report.get("domain") or report.get("url"),
        "overall_score": overall,
        "grade": sc.get("grade"),
        "sections": sections,
        "issues": [],
    }
    id_set: set[str] = set()
    for r in (report.get("recommendations") or []):
        rid = r.get("id")
        if not rid:
            continue
        fx = r.get("fix_template") or {}
        rec_fix = fx.get("recommended_fix") or []
        issue = {
            "id": rid,
            "label": _clip(r.get("signal_label") or r.get("issue_title")),
            "status": r.get("status"),
            "score": r.get("score"),
            "section": r.get("category"),
            "detail": _clip(r.get("description")),
            "fix_hint": _clip(rec_fix[0] if rec_fix else fx.get("expected_outcome")),
            "evidence": _evidence((r.get("evidence") or {}).get("findings")),
        }
        probe = dict(facts, issues=facts["issues"] + [issue])
        if facts["issues"] and len(json.dumps(probe, default=str)) > _PAYLOAD_CAP:
            break  # drop this and any lower-priority issues
        facts["issues"].append(issue)
        id_set.add(str(rid))
        if _as_int(r.get("score")) is not None:
            score_set.add(_as_int(r.get("score")))
    return facts, id_set, score_set


def _cited_scores(text: str) -> set[int]:
    nums: set[int] = set()
    for pattern in _SCORE_RES:
        for m in pattern.findall(text or ""):
            nums.add(int(m))
    return nums


def _cites_unknown_score(text: str, score_set: set[int]) -> bool:
    """True if the text quotes a score ('NN/100' or 'scored NN') absent from FACTS."""
    return any(n not in score_set for n in _cited_scores(text))


def _validate(data: dict, id_set: set[str], score_set: set[int], tier: str) -> dict | None:
    """Shape + ground the model output. Returns None when it can't be trusted."""
    # Executive summary is required; drop it (→ full fallback) if it cites a bad score.
    summary = data.get("executive_summary")
    summary = summary.strip() if isinstance(summary, str) else ""
    if summary and _cites_unknown_score(summary, score_set):
        summary = ""
    if not summary:
        return None

    insights = []
    for it in (data.get("issue_insights") or []):
        if not isinstance(it, dict):
            continue
        rid = str(it.get("id") or "")
        if rid not in id_set:                      # only ids the model was actually given
            continue
        why = _clip(it.get("why_it_matters"), 600)
        rationale = _clip(it.get("priority_rationale"), 300)
        if why and _cites_unknown_score(why, score_set):
            why = ""
        if rationale and _cites_unknown_score(rationale, score_set):
            rationale = ""
        if not why and not rationale:
            continue
        insights.append({"id": rid, "why_it_matters": why, "priority_rationale": rationale})

    if tier == "free":
        # Free tier: top-3 insights, no action plan.
        return {"executive_summary": summary, "issue_insights": insights[:_FREE_INSIGHTS]}

    plan = []
    for step in (data.get("action_plan") or []):
        if isinstance(step, str) and step.strip() and not _cites_unknown_score(step, score_set):
            plan.append(_clip(step, 300))
    return {"executive_summary": summary, "issue_insights": insights, "action_plan": plan[:7]}


def write_narrative(report: dict, *, tier: str) -> dict | None:
    """Generate the grounded AI narrative for a report, or None on any failure.

    `tier` is "free" (summary + top-3 insights, smaller token budget) or "paid"
    (insights for every issue + an ordered action plan)."""
    if not settings.ai_enabled:
        return None
    facts, id_set, score_set = _facts(report)
    paid = tier == "paid"
    instructions = _INSTRUCTIONS_PAID if paid else _INSTRUCTIONS_FREE
    max_tokens = settings.ai_max_tokens if paid else _FREE_MAX_TOKENS
    user = "FACTS (JSON):\n" + json.dumps(facts, default=str) + "\n\n" + instructions
    raw = ai.complete(_SYSTEM, user, max_tokens=max_tokens)
    data = ai.parse_json(raw)
    if not isinstance(data, dict):
        return None
    return _validate(data, id_set, score_set, tier)
