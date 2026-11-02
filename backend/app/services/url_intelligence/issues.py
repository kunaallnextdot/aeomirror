"""Deterministic per-page issues (Site Intelligence v2).

Every issue here is decided by a rule over the page's OWN extracted evidence
(evidence.py) — no AI, no inference from traffic, no guessing. Two kinds:

- "confirmed": the evidence shows the problem directly (e.g. no <title> element).
- "potential": the evidence shows the condition, but whether it is a problem depends
  on intent the crawler can't see (e.g. noindex may be deliberate, a short page may be
  fine). Shown as an opportunity to review, never as a defect.

Rules that don't apply to a page are skipped, not failed: content checks never run on
an error page (a 404's missing H1 is not a content problem), and short-content checks
don't run on legal/utility or contact pages. A page answering 4xx/5xx gets the HTTP
issue only. Nothing here says anything about a search engine's index — only about what
this crawler fetched.
"""
from __future__ import annotations

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

CATALOG: dict[str, dict] = {
    "http_5xx": {
        "title": "Server error when fetched", "category": "http_status", "severity": "critical",
        "kind": "confirmed",
        "why": "The page answered with a server error, so neither visitors nor crawlers (search or AI) could read it when it was fetched.",
        "fix": "Check server logs for this URL and fix the error, or remove links to it if the page no longer exists.",
        "verify": "Re-check the URL and confirm it answers 200 with the expected content.",
    },
    "http_4xx": {
        "title": "Page not found / client error", "category": "http_status", "severity": "high",
        "kind": "confirmed",
        "why": "The page answered with a 4xx status, so links to it lead visitors and crawlers to an error.",
        "fix": "Restore the page, redirect it (301) to its replacement, or remove the internal links and sitemap entry pointing to it.",
        "verify": "Re-check the URL: it should answer 200, or 301 to a live page.",
    },
    "noindex": {
        "title": "Page asks not to be indexed (noindex)", "category": "indexability", "severity": "high",
        "kind": "potential",
        "why": "A robots noindex directive tells search engines not to index the page, which also keeps it out of AI answers built on search results. This may be intentional.",
        "fix": "If this page should be findable, remove the noindex directive from the robots meta tag. Leave it if the page is deliberately private.",
        "verify": "Re-check the page and confirm the robots meta tag no longer contains noindex.",
    },
    "missing_title": {
        "title": "Missing page title", "category": "metadata", "severity": "high", "kind": "confirmed",
        "why": "The <title> is the main label search results and AI assistants use for a page; without it they have to guess.",
        "fix": "Add a unique, descriptive <title> (about 50–60 characters) naming what the page offers.",
        "verify": "Re-check the page and confirm a non-empty <title> element is present.",
    },
    "title_too_long": {
        "title": "Page title longer than 70 characters", "category": "metadata", "severity": "low",
        "kind": "confirmed",
        "why": "Long titles are usually cut off in search results, so the end of the title isn't seen.",
        "fix": "Shorten the title to about 60 characters, keeping the most important words first.",
        "verify": "Re-check the page and confirm the title is 70 characters or fewer.",
    },
    "missing_meta_description": {
        "title": "Missing meta description", "category": "metadata", "severity": "medium",
        "kind": "confirmed",
        "why": "Without a meta description, search engines and AI tools pick their own snippet, which may not describe the page well.",
        "fix": "Add a meta description (about 140–160 characters) summarising the page and who it is for.",
        "verify": "Re-check the page and confirm a non-empty meta description is present.",
    },
    "missing_h1": {
        "title": "No H1 heading", "category": "content_structure", "severity": "medium",
        "kind": "confirmed",
        "why": "The H1 states what the page is about; without it the page's main topic is harder to identify.",
        "fix": "Add one H1 that states the page's main topic.",
        "verify": "Re-check the page and confirm exactly one H1 is present.",
    },
    "multiple_h1": {
        "title": "More than one H1 heading", "category": "content_structure", "severity": "low",
        "kind": "confirmed",
        "why": "Several H1s make the page's main topic ambiguous.",
        "fix": "Keep one H1 for the main topic and change the others to H2.",
        "verify": "Re-check the page and confirm exactly one H1 is present.",
    },
    "thin_content": {
        "title": "Very little text (under 150 words)", "category": "content_structure",
        "severity": "medium", "kind": "potential",
        "why": "Pages with very little text give search engines and AI assistants little to understand or quote. Some pages are short by design.",
        "fix": "If this page should rank or be cited, add the information people need: what it is, who it's for, key details and common questions.",
        "verify": "Re-check the page and confirm it has at least 150 words of visible text.",
    },
    "no_structured_data": {
        "title": "No structured data (JSON-LD)", "category": "structured_data", "severity": "medium",
        "kind": "confirmed",
        "why": "Structured data tells machines exactly what the page describes (organisation, product, article, FAQ). None was found on this page.",
        "fix": "Add JSON-LD schema that matches the page type, e.g. Organization on the homepage, Product on product pages, Article on posts.",
        "verify": "Re-check the page and confirm valid JSON-LD is present.",
    },
    "missing_canonical": {
        "title": "No canonical URL declared", "category": "indexability", "severity": "low",
        "kind": "confirmed",
        "why": "A canonical tag tells search engines which URL is the main version of a page, which avoids duplicate versions competing.",
        "fix": "Add <link rel=\"canonical\"> pointing to the page's preferred URL.",
        "verify": "Re-check the page and confirm a canonical link is present.",
    },
    "images_missing_alt": {
        "title": "Images without alt text", "category": "accessibility", "severity": "low",
        "kind": "potential",
        "why": "Alt text describes images to screen readers and to machines that can't see them. Decorative images should use an empty alt=\"\" instead.",
        "fix": "Add short, descriptive alt text to content images; mark purely decorative images with alt=\"\".",
        "verify": "Re-check the page and confirm every content image has an alt attribute.",
    },
}

# missing_content_elements codes (evidence.py) that map 1:1 onto catalog entries.
_FROM_EVIDENCE = ("missing_title", "title_too_long", "missing_meta_description", "missing_h1",
                  "multiple_h1", "thin_content", "no_structured_data", "missing_canonical")
_SHORT_BY_DESIGN = {"legal_utility", "lead_generation", "booking"}


def page_issues(evidence: dict | None, page_type: str | None = None) -> list[str]:
    """Issue codes for one crawled page, in catalog order. Empty for a page with no
    evidence (nothing was checked — callers must not read that as "clean")."""
    if not evidence:
        return []
    status = evidence.get("http_status") or 0
    if status >= 500:
        return ["http_5xx"]
    if status >= 400:
        return ["http_4xx"]
    missing = set(evidence.get("missing_content_elements") or [])
    codes = []
    if evidence.get("noindex"):
        codes.append("noindex")
    for code in _FROM_EVIDENCE:
        if code not in missing:
            continue
        if code == "thin_content" and page_type in _SHORT_BY_DESIGN:
            continue   # not applicable: these pages are short by design
        codes.append(code)
    if (evidence.get("images_missing_alt") or 0) > 0:
        codes.append("images_missing_alt")
    return codes


def severity_rank(codes: list[str]) -> int:
    return max((SEVERITY_RANK[CATALOG[c]["severity"]] for c in codes if c in CATALOG), default=0)


def observed_value(code: str, evidence: dict) -> str:
    """The exact evidence behind one issue on one page, in plain words."""
    e = evidence or {}
    if code in ("http_5xx", "http_4xx"):
        return f"HTTP {e.get('http_status')}"
    if code == "noindex":
        return f"robots meta: {e.get('robots_meta')!r}"
    if code == "missing_title":
        return "no <title> element"
    if code == "title_too_long":
        return f"title is {e.get('title_length')} characters"
    if code == "missing_meta_description":
        return "no meta description"
    if code in ("missing_h1", "multiple_h1"):
        return f"{(e.get('heading_counts') or {}).get('h1', 0)} H1 heading(s)"
    if code == "thin_content":
        return f"{e.get('word_count')} words of visible text"
    if code == "no_structured_data":
        return "no JSON-LD found"
    if code == "missing_canonical":
        return "no canonical link"
    if code == "images_missing_alt":
        return f"{e.get('images_missing_alt')} of {e.get('image_count')} images have no alt attribute"
    return ""
