"""AI content-quality analysis (Feature B) — Pro-only, per page, on demand.

Given a page's visible text, asks Claude to score tone / clarity / structure and
produce concrete, content-specific suggestions plus a short before/after rewrite.
`analyze_content(text, url=...)` returns the validated dict, or None on any failure
(AI off / API error / malformed response) so the endpoint can answer 503. All parsing
is defensive; the input text is capped by the caller (~6000 chars).
"""
from __future__ import annotations

import json

from app.config import settings
from app.core import ai
from app.reports.ai_writer import _parse_json  # shared defensive JSON extractor

_TEXT_CAP = 6000

_SYSTEM = (
    "You are an expert content editor evaluating a web page's writing for both human "
    "readers and AI engines (ChatGPT, Claude, Gemini, Perplexity). Judge tone, clarity "
    "and structure, and give specific, actionable feedback grounded in the actual text — "
    "quote at most a short phrase in each suggestion."
)

_INSTRUCTIONS = (
    "Return ONLY a JSON object with exactly these keys:\n"
    '{"tone": {"assessment": string, "score_0_100": integer},\n'
    ' "clarity": {"assessment": string, "score_0_100": integer},\n'
    ' "structure": {"assessment": string, "score_0_100": integer,\n'
    '   "fixes": [2 to 6 short, numbered, concrete actions to restructure THIS page '
    "(e.g. \"Add a 2-3 sentence introduction before the service list\"), each grounded "
    "in the actual content — never generic SEO advice],\n"
    '   "implementation": string (a short skeleton, 3 to 8 lines, showing the improved '
    "heading structure for THIS page, e.g. \"H1: ...\\nIntro: ...\\nH2: ...\\nH3: ...\" — "
    "not a full rewrite of the page)},\n"
    ' "suggestions": [4 to 8 specific, actionable items referencing the actual '
    "content, each quoting at most a short phrase],\n"
    ' "rewrite_example": {"before": string (a short excerpt from the page), '
    '"after": string (an improved version)}}\n'
    "Output JSON only."
)


def _score(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _clip(value, cap: int = 600) -> str:
    return ("" if value is None else str(value))[:cap]


def _meter(block) -> dict:
    block = block if isinstance(block, dict) else {}
    return {"assessment": _clip(block.get("assessment"), 400),
            "score_0_100": _score(block.get("score_0_100"))}


def _structure_meter(block) -> dict:
    """Same shape as _meter(), plus `fixes` (a short numbered action list) and
    `implementation` (a short heading-structure skeleton) — real model output, never
    fabricated client-side. Both are optional: a page whose structure is already fine
    may have no fixes at all, and the frontend must render that honestly (no fix
    needed), not force placeholder content."""
    block = block if isinstance(block, dict) else {}
    fixes = [
        _clip(f, 200) for f in (block.get("fixes") or [])
        if isinstance(f, str) and f.strip()
    ][:6]
    out = _meter(block)
    out["fixes"] = fixes
    out["implementation"] = _clip(block.get("implementation"), 600)
    return out


def _shape(data: dict) -> dict | None:
    suggestions = [
        _clip(s, 300) for s in (data.get("suggestions") or [])
        if isinstance(s, str) and s.strip()
    ][:8]
    rewrite = data.get("rewrite_example") if isinstance(data.get("rewrite_example"), dict) else {}
    out = {
        "tone": _meter(data.get("tone")),
        "clarity": _meter(data.get("clarity")),
        "structure": _structure_meter(data.get("structure")),
        "suggestions": suggestions,
        "rewrite_example": {
            "before": _clip(rewrite.get("before"), 800),
            "after": _clip(rewrite.get("after"), 800),
        },
    }
    # Require at least one substantive field, else treat as a failed analysis.
    if not suggestions and not out["rewrite_example"]["before"]:
        return None
    return out


def analyze_content(text: str, *, url: str, page_title: str | None = None,
                    timeout: int | None = None) -> dict | None:
    """Analyze one page's text, or None on any failure. `timeout` overrides the AI
    per-request timeout (the interactive path passes a shorter one)."""
    if not settings.ai_enabled:
        return None
    snippet = (text or "").strip()[:_TEXT_CAP]
    if not snippet:
        return None
    context = {"url": url, "title": (page_title or "")[:200], "text": snippet}
    user = "Page (JSON):\n" + json.dumps(context) + "\n\n" + _INSTRUCTIONS
    raw = ai.complete(_SYSTEM, user, max_tokens=settings.ai_max_tokens, timeout=timeout)
    if not raw:
        return None
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return None
    return _shape(data)
