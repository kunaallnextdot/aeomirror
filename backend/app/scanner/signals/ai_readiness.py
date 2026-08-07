"""AI Readiness signal (custom rubric): how easily can ChatGPT, Claude, Gemini and
Perplexity reach, read and understand this page.

Strictly heuristic — NO LLM or paid API is called. It rewards: server-rendered
readable text, clean semantic structure, a single clear topic, machine-readable
entities (JSON-LD), Q&A framing, and emerging conventions (llms.txt).
"""
from __future__ import annotations

import re

from app.scanner.signals.base import SignalContext, SignalResult

# Display label is "AI Extractability" to avoid colliding with the headline
# "AI Readiness Score" gauge; the signal ID stays "ai_readiness" so stored scans
# and rubric versioning remain valid.
ID, LABEL, WEIGHT = "ai_readiness", "AI Extractability", 10


def analyze(ctx: SignalContext) -> SignalResult:
    soup = ctx.soup
    text = ctx.text
    text_len = len(text)
    ratio = text_len / max(len(ctx.html), 1)
    issues: list = []
    recs: list = []
    score = 0.0

    # 1. readable, server-rendered text (AI reads the raw HTML, not JS output)
    is_shell = bool(soup.select_one("#root, #app, #__next")) and text_len < 300
    if text_len >= 500 and not is_shell:
        score += 25
    elif text_len >= 150:
        score += 12
        issues.append("Thin extractable text — AI engines may not get enough context.")
        recs.append("Add substantive server-rendered text content.")
    else:
        issues.append("Almost no extractable text — the page likely renders via JavaScript.")
        recs.append("Server-render or pre-render the main content so AI crawlers can read it.")

    if ratio >= 0.10:
        score += 15
    elif ratio >= 0.04:
        score += 7
        recs.append("Raise the text-to-HTML ratio by server-rendering key content.")
    else:
        issues.append("Very low text-to-HTML ratio — content is likely JS-injected.")

    # 2. clear single topic
    if len(soup.find_all("h1")) == 1:
        score += 15
    else:
        recs.append("Use one clear H1 so AI can identify the page's primary topic.")

    # 3. machine-readable entities
    if ctx.jsonld_types:
        score += 20
    else:
        issues.append("No structured data — AI cannot reliably extract entities.")
        recs.append("Add JSON-LD (Organization, WebSite, Article/FAQ).")

    # 4. Q&A / FAQ framing (LLMs answer questions well when content is Q&A-shaped)
    lower = text.lower()
    has_qa = ("FAQPage" in ctx.jsonld_types) or "frequently asked" in lower or lower.count("?") >= 3
    if has_qa:
        score += 10
    else:
        recs.append("Add an FAQ / Q&A section — it maps directly to how people prompt AI.")

    # 5. semantic sectioning aids extraction
    if soup.find(["main", "article"]):
        score += 10

    # 6. emerging convention
    if ctx.llms_txt_present:
        score += 5

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "extractable_text_chars": text_len,
            "text_to_html_ratio": round(ratio, 3),
            "js_shell": is_shell,
            "has_structured_data": bool(ctx.jsonld_types),
            "qa_content": has_qa,
            "llms_txt": ctx.llms_txt_present,
        },
    )
