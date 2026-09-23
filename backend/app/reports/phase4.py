"""Phase 4 — deeper AEO intelligence: Schema, Internal Link, Entity, and Question
Mining. Pure, deterministic, read-side derivations over the SAME scanner evidence
Phase 1/2 already use (`sections` from a stored scan) — no new scanner logic, no LLM,
no new database tables. Mirrors `reports.insights`'s architecture and conventions
exactly (same gating style, same "never invent evidence" discipline).

Every URL, question, and schema type surfaced here is copied verbatim from the
scanner's own evidence (`scanner/schema_extract.py`, `scanner/signals/schema.py`,
`scanner/signals/links.py`, `scanner/signals/content.py`). Where the underlying data
genuinely cannot support a claim (a multi-page link graph, Knowledge Graph presence,
search volume), this module says so explicitly rather than approximating or inventing
a value — see the `*_note` / `insufficient_evidence` fields below.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# ------------------------------- shared lookups -------------------------------
def _section(sections: list[dict] | None, signal_id: str) -> dict:
    for s in sections or []:
        if s.get("id") == signal_id:
            return s
    return {}


# ------------------------------- schema relevance gating -------------------------------
# Organization/WebSite/BreadcrumbList apply to virtually every website and stay
# unconditional. These 5 types are genuinely content-type-dependent — recommending
# them regardless of the page's actual content is a real false-positive risk (e.g.
# "Missing Product schema" on a page that sells nothing). Gated conservatively using
# ONLY already-computed, real scan evidence (never a new LLM call, never a guessed
# page type): a URL-path hint or a real pattern found in the page's own scanned body
# text. When no such evidence exists, the type is simply not surfaced as missing —
# never a fabricated relevance claim, but also never silently dropped when real
# evidence DOES support it (see `_schema_relevance`).
_GATED_SCHEMA_TYPES = {"Article", "FAQPage", "Product", "Service", "Review"}
_ARTICLE_MIN_WORDS = 400
_PRODUCT_PATH_HINTS = ("/product", "/shop", "/store", "/pricing", "/buy", "/item")
_SERVICE_PATH_HINTS = ("/service", "/services", "/solutions")
_REVIEW_PATH_HINTS = ("/review", "/reviews", "/testimonial", "/testimonials")
_PRICE_PATTERN = re.compile(
    r"(\$|USD|₹|INR|€|EUR|£|GBP)\s?\d|\d+(\.\d+)?\s?(USD|INR|EUR|GBP)\b", re.IGNORECASE)
_RATING_PATTERN = re.compile(
    r"\b\d(\.\d)?\s*(out of|/)\s*5\b|\bstar rating\b|\bcustomer review(s)?\b|"
    r"\btestimonial(s)?\b|\b\d\s*stars?\b", re.IGNORECASE)
_SERVICE_TEXT_PATTERN = re.compile(
    r"\bwe (offer|provide)\b|\bour services\b|\bbook (a|an)\b|\bschedule (a|an)\b|"
    r"\bget a quote\b|\brequest a (quote|consultation)\b", re.IGNORECASE)


def _body_text(content_ev: dict) -> str:
    chunks = (content_ev.get("body_evidence") or {}).get("chunks") or []
    return " ".join(c.get("text", "") for c in chunks if isinstance(c, dict))


def _path_has_any(url: str | None, hints: tuple[str, ...]) -> bool:
    if not url:
        return False
    path = (urlparse(url).path or "").lower()
    return any(h in path for h in hints)


def _schema_relevance(name: str, url: str | None, content_ev: dict) -> tuple[bool, str | None]:
    """Conservative, non-LLM relevance check for a gated schema type. Returns
    (relevant, evidence) — `evidence` is real, quotable text explaining WHY (never
    invented) used to extend the recommendation's why_it_matters. Returns
    (False, None) when no real signal supports relevance — the safe default."""
    if name == "Article":
        wc = content_ev.get("word_count") or 0
        if wc >= _ARTICLE_MIN_WORDS:
            return True, f"this page has {wc} words of substantial body content"
        return False, None
    if name == "FAQPage":
        qs = content_ev.get("heading_questions") or []
        if qs:
            return True, f'this page has a question-shaped heading ("{qs[0]}")'
        return False, None
    if name == "Product":
        if _path_has_any(url, _PRODUCT_PATH_HINTS):
            return True, "this page's URL path suggests a product/shop page"
        if _PRICE_PATTERN.search(_body_text(content_ev)):
            return True, "this page's content includes price/currency text"
        return False, None
    if name == "Service":
        if _path_has_any(url, _SERVICE_PATH_HINTS):
            return True, "this page's URL path suggests a service page"
        if _SERVICE_TEXT_PATTERN.search(_body_text(content_ev)):
            return True, "this page's content describes a service being offered"
        return False, None
    if name == "Review":
        if _path_has_any(url, _REVIEW_PATH_HINTS):
            return True, "this page's URL path suggests review/testimonial content"
        if _RATING_PATTERN.search(_body_text(content_ev)):
            return True, "this page's content includes rating or review language"
        return False, None
    return True, None   # Organization / WebSite / BreadcrumbList — always relevant


# ============================== 1. Schema Intelligence ==============================
# One canonical checklist of the schema types this scanner can actually detect
# (scanner/schema_extract.py CONTENT_TYPES + the Organization/LocalBusiness entity
# check). Order = display priority. Copy is generic/templated (like reports/templates.py)
# — never claims a specific rich-result eligibility, since the scanner has no way to
# verify Google's actual rendering of a given type.
_SCHEMA_CHECKLIST = [
    ("Organization", "Your organization/entity information is not consistently "
     "represented in structured data, making it harder for AI engines to identify "
     "who you are.",
     "Add Organization (or a LocalBusiness subtype such as MedicalBusiness/Dentist) "
     "JSON-LD with name, url, logo and sameAs."),
    ("WebSite", "Without WebSite schema, AI engines can't reliably associate your "
     "pages with your site's identity.",
     "Add WebSite JSON-LD with name and url."),
    ("Article", "Article schema signals this is authored, citable content rather than "
     "a generic page.",
     "Add Article/BlogPosting JSON-LD to editorial or blog pages."),
    ("FAQPage", "FAQPage schema maps your content directly onto how people ask AI "
     "assistants questions.",
     "Add FAQPage JSON-LD with real Question/Answer pairs drawn from your content."),
    ("BreadcrumbList", "Breadcrumb schema helps AI engines understand your site's "
     "page hierarchy.",
     "Add BreadcrumbList JSON-LD reflecting your navigation path."),
    ("Product", "Product schema is how AI/shopping-aware engines understand pricing "
     "and availability.",
     "Add Product JSON-LD with name, price and availability where you sell something."),
    ("Service", "Service schema tells AI engines precisely what you offer, improving "
     "eligibility for service-related answers.",
     "Add Service JSON-LD describing what you offer."),
    ("Review", "Review/rating schema is a trust signal AI engines weigh when "
     "recommending a business.",
     "Add Review/AggregateRating JSON-LD only where you have genuine reviews."),
]


def _bulk_signal_summary(bulk_pages: list[dict] | None, signal_id: str,
                         *, weak_below: float = 60.0) -> dict | None:
    """Cross-page summary for `signal_id` from a bulk scan's ALREADY-STORED
    `sections_summary` (score/issues/recommendations — no schema/link "evidence" is
    persisted per bulk page, so this works from what genuinely exists rather than
    requiring a scanner change). Every URL/issue here is copied from that stored data."""
    if not bulk_pages:
        return None
    weak = []
    checked = 0
    for p in bulk_pages:
        if p.get("error"):
            continue
        checked += 1
        sec = _section(p.get("sections_summary"), signal_id)
        if not sec:
            continue
        if (sec.get("score") if sec.get("score") is not None else 100) < weak_below:
            weak.append({"url": p.get("url"), "score": sec.get("score"),
                        "issues": sec.get("issues") or []})
    return {"pages_checked": checked, "weak_page_count": len(weak), "weak_pages": weak}


def build_schema_intelligence(sections: list[dict] | None, url: str | None,
                              bulk_pages: list[dict] | None = None) -> dict:
    """What schema exists, what's missing, why it matters, what to do — for the
    scanned page (+ a cross-page summary when this is a bulk scan). Uses ONLY the
    `schema` signal's own evidence; never invents a type or a URL.

    Content-type-dependent types (Article/FAQPage/Product/Service/Review — see
    `_GATED_SCHEMA_TYPES`) are only surfaced as missing when real scan evidence
    (`content` signal's word_count/heading_questions/body text, or the URL path)
    supports their relevance to THIS page — never a fixed checklist applied blindly
    to every page. Organization/WebSite/BreadcrumbList remain unconditional; they
    apply to every website regardless of content type."""
    row = _section(sections, "schema")
    ev = row.get("evidence") or {}
    state = ev.get("state", "absent")
    content_flags = ev.get("content") or {}
    has_entity = bool(ev.get("has_entity"))

    present = set(k for k, v in content_flags.items() if v)
    if has_entity:
        present.add("Organization")

    content_ev = (_section(sections, "content") or {}).get("evidence") or {}

    missing = []
    for name, why, action in _SCHEMA_CHECKLIST:
        if name in present:
            continue
        if name in _GATED_SCHEMA_TYPES:
            relevant, evidence = _schema_relevance(name, url, content_ev)
            if not relevant:
                continue
            if evidence:
                why = f"{why} ({evidence[0].upper()}{evidence[1:]}.)"
        missing.append({
            "type": name, "why_it_matters": why, "recommended_action": action,
            "affected_urls": [url] if url else [],
        })

    return {
        "state": state,                                    # absent | malformed | present
        "malformed_blocks": ev.get("malformed", 0),
        "detected_types": ev.get("detected_types") or [],   # raw @type values found
        "present_types": sorted(present),                   # canonical checklist types found
        "missing_types": missing,
        "signal_score": row.get("score"),
        "related_recommendation_id": "schema",
        "bulk": _bulk_signal_summary(bulk_pages, "schema"),
    }


# ============================== 2. Internal Link Intelligence ==============================
def build_link_intelligence(sections: list[dict] | None, url: str | None,
                            bulk_pages: list[dict] | None = None) -> dict:
    """Deeper read of the `links` signal's own evidence. A single-page scan can only
    ever see this page's OUTGOING links — it has no way to know who links TO this page,
    so this NEVER claims "orphan page" or "no inbound links"; that claim would require
    a multi-page link graph this scanner does not build. Genuinely weak/zero outgoing
    linking is reported as exactly that."""
    row = _section(sections, "links")
    ev = row.get("evidence") or {}
    internal = ev.get("internal_links", 0) or 0
    generic = ev.get("generic_anchors", 0) or 0
    empty = ev.get("empty_anchors", 0) or 0
    has_nav = bool(ev.get("has_nav"))
    diversity = ev.get("anchor_diversity")

    issues = []
    urls = [url] if url else []
    if internal == 0:
        issues.append({
            "type": "potentially_isolated_page", "label": "Potentially isolated page",
            "detail": "This page contains no internal links of its own. Whether any "
                      "OTHER page links to it cannot be determined from a single-page "
                      "scan — insufficient crawl graph data.",
            "affected_urls": urls,
            "recommended_action": "Add contextual internal links from this page to "
                                  "related pages. Additional source-page analysis "
                                  "required to confirm inbound links.",
        })
    elif internal < 5:
        issues.append({
            "type": "weak_internal_linking", "label": "Weak internal-link coverage",
            "detail": f"This page contains only {internal} internal link(s) — thin "
                      "outgoing link structure for crawlers to follow.",
            "affected_urls": urls,
            "recommended_action": "Add contextual internal links from this page to "
                                  "related pages.",
        })
    if not has_nav:
        issues.append({
            "type": "no_navigation", "label": "No semantic navigation detected",
            "detail": "No <nav> element or navigation landmark was found on this page.",
            "affected_urls": urls,
            "recommended_action": "Add a semantic <nav> for primary navigation.",
        })
    if generic:
        issues.append({
            "type": "generic_anchor_text", "label": "Generic anchor text",
            "detail": f"{generic} link(s) use generic anchor text (e.g. 'click here').",
            "affected_urls": urls,
            "recommended_action": "Replace generic anchor text with descriptive text "
                                  "naming the destination page.",
        })
    if empty:
        issues.append({
            "type": "empty_anchor_text", "label": "Links with no anchor text",
            "detail": f"{empty} link(s) have no anchor text at all.",
            "affected_urls": urls,
            "recommended_action": "Give every link descriptive anchor text.",
        })

    return {
        "internal_links": internal, "has_nav": has_nav,
        "anchor_diversity": diversity, "generic_anchors": generic, "empty_anchors": empty,
        "issues": issues,
        "signal_score": row.get("score"),
        "related_recommendation_id": "links",
        "inbound_link_graph_available": False,
        "inbound_link_graph_note": ("Insufficient crawl graph data: this scanner audits "
                                    "one page (or a user-supplied list of pages) at a "
                                    "time and does not build a full inbound-link graph "
                                    "of your site, so true orphan-page status can't be "
                                    "claimed."),
        "bulk": _bulk_signal_summary(bulk_pages, "links"),
    }


# ============================== 3. Entity Intelligence ==============================
# A small, fixed, deterministic checklist of entity SIGNALS this scanner can actually
# verify from a page's own JSON-LD. `completeness_pct` is a coverage metric over this
# checklist — NOT a claim about Google Knowledge Graph inclusion, which this scanner
# has no way to observe and never claims.
_ENTITY_CHECKS = ["has_entity_schema", "has_name", "has_url", "has_logo",
                  "has_sameas", "has_website_schema"]


def build_entity_intelligence(sections: list[dict] | None, url: str | None) -> dict:
    row = _section(sections, "schema")
    ev = row.get("evidence") or {}
    entity_ev = ev.get("entity_evidence") or {}
    same_as = entity_ev.get("same_as") or []
    checks = {
        "has_entity_schema": bool(ev.get("has_entity")),
        "has_name": bool(entity_ev.get("name")),
        "has_url": bool(entity_ev.get("url")),
        "has_logo": bool(entity_ev.get("logo")),
        "has_sameas": bool(same_as),
        "has_website_schema": bool((ev.get("content") or {}).get("WebSite")),
    }
    missing_signals = [k for k in _ENTITY_CHECKS if not checks[k]]
    completeness = round(100.0 * (len(_ENTITY_CHECKS) - len(missing_signals))
                         / len(_ENTITY_CHECKS), 1)

    return {
        "primary_entity_types": ev.get("entity_types") or [],
        "primary_entity_name": entity_ev.get("name"),
        "entity_url": entity_ev.get("url"),
        "logo": entity_ev.get("logo"),
        "same_as": same_as,
        "same_as_note": None if same_as else "No sameAs relationship detected.",
        "checks": checks,
        "missing_signals": missing_signals,
        "completeness_pct": completeness,
        "affected_urls": [url] if url else [],
        "related_recommendation_id": "schema",
        "knowledge_graph_note": ("No Google Knowledge Graph / Knowledge Panel evidence "
                                 "is available from this scan — presence there can be "
                                 "neither confirmed nor claimed here."),
    }


# ============================== 4. Question Mining ==============================
# Deterministic keyword classification — no LLM call. Order matters: the first
# matching category wins, "informational" is the fallback for everything else.
_CATEGORY_RULES = [
    ("pricing", ("price", "pricing", "cost", "how much", "fee", "fees", "cheap", "expensive")),
    ("comparison", (" vs ", " vs.", "versus", "compare", "comparison", "better than", "alternative")),
    ("local", ("near me", "near you", "in my area", "local", "nearby")),
    ("how_to", ("how to", "how do i", "how can i", "steps to", "guide to")),
    ("problem_solution", ("fix", "problem", "issue", "not working", "trouble", "error", "broken")),
    ("service", ("service", "services", "treat", "treatment", "offer", "provide", "provider")),
]


def classify_question(text: str) -> str:
    """Deterministic category for any question text (scan-derived OR an Answer
    Tracking prompt) — the ONE classifier reused everywhere a question needs a
    category (Question Mining here, and the Question Bank in
    services/answer_tracking/visibility.py). No LLM."""
    t = (text or "").lower()
    for category, keywords in _CATEGORY_RULES:
        if any(kw in t for kw in keywords):
            return category
    return "informational"


def normalize_question_key(text: str | None) -> str:
    """A stable dedup key for matching the SAME question across sources (scan FAQ,
    scan heading, an Answer Tracking prompt): trims, collapses repeated whitespace,
    lowercases, and drops a single trailing '?'/'!'/'.' (plus the whitespace that
    trailing punctuation may leave behind). Deliberately shallow — it must merge
    "What is A?" / "what is a ?" / "What Is A" into one key, but must NEVER merge two
    genuinely different questions (e.g. "How much does X cost?" vs "How long does X
    take?" stay distinct, since only whitespace/case/terminal punctuation are touched)."""
    if not text:
        return ""
    collapsed = " ".join(text.split())          # trim + collapse internal whitespace
    return collapsed.lower().rstrip("?!.").rstrip()


def build_question_mining(sections: list[dict] | None, url: str | None) -> dict:
    """Real questions already present in the scanned page's own content: FAQPage
    schema Q&A (verbatim) and question-shaped headings (verbatim). Grouped into
    deterministic clusters. This is the scan-only slice of Question Mining — when the
    scan's site also has Answer Tracking data, the report/AI-visibility route enriches
    this further with tracked prompts and content-gap questions (see
    services/answer_tracking/visibility.py's opportunity cross-referencing), since that
    data lives outside a single scan."""
    schema_row = _section(sections, "schema")
    content_row = _section(sections, "content")
    faq = (schema_row.get("evidence") or {}).get("faq_questions") or []
    headings = (content_row.get("evidence") or {}).get("heading_questions") or []

    questions: list[dict] = []
    seen = set()
    for q in faq:
        key = (q.get("question") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        questions.append({"text": q["question"], "answer": q.get("answer"),
                          "source": "schema_faq", "affected_url": url,
                          "category": classify_question(q["question"])})
    for h in headings:
        key = h.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        questions.append({"text": h, "answer": None, "source": "page_heading",
                          "affected_url": url, "category": classify_question(h)})

    clusters: dict[str, list[dict]] = {}
    for q in questions:
        clusters.setdefault(q["category"], []).append(q)

    return {"questions": questions, "clusters": clusters, "total_count": len(questions)}


# ============================== compose + gate ==============================
# Same {"available": False, "reason": ...} shape `get_report_ai_visibility` already
# uses (see routes_reports.py / AIVisibilityEmpty) — reused here rather than inventing
# a second "no data" convention.
UNAVAILABLE_SCAN_INCOMPLETE = {"available": False, "reason": "scan_incomplete"}


def build_phase4_block(sections: list[dict] | None, url: str | None,
                       bulk_pages: list[dict] | None = None,
                       scan_ready: bool = True) -> dict:
    """The full, ungated Phase 4 block embedded additively into `build_report`'s
    output (`report["phase4"]`) — mirrors `reports.insights.build_insights_block`.
    Scan-only: no scanner/scoring change, no persistence, no AI call.

    `scan_ready` is the CENTRALIZED readiness guard (default True so every existing
    caller/test that doesn't pass it keeps today's behavior): a pending/running/failed
    scan has NOT produced real scanner evidence yet, so `sections` being empty or
    partial there is not the same fact as "genuinely no schema/links/entities/questions
    found" on a COMPLETED scan. Declaring 8 missing schema types for a scan that hasn't
    even run yet would be exactly the fabricated diagnosis this project forbids — so an
    unready scan gets an honest `{"available": False, "reason": "scan_incomplete"}`
    instead of any schema/link/entity/question content at all."""
    if not scan_ready:
        return dict(UNAVAILABLE_SCAN_INCOMPLETE)
    return {
        "available": True,
        "schema": build_schema_intelligence(sections, url, bulk_pages=bulk_pages),
        "links": build_link_intelligence(sections, url, bulk_pages=bulk_pages),
        "entity": build_entity_intelligence(sections, url),
        "questions": build_question_mining(sections, url),
    }


def gate_phase4(phase4: dict | None, unlocked: bool) -> dict | None:
    """Server-side trim to a free preview — same model as `insights.gate_insights` /
    `insights.gate_recommendations`: the locked detail never leaves the server, a
    caller only ever receives a small real preview + a locked count.

    An unready-scan block (`available: False`) carries no schema/links/entity/questions
    keys at all — there is nothing to trim, and the "insufficient evidence" state itself
    is not paid-only information, so it passes through unchanged for every tier."""
    if not phase4 or unlocked or phase4.get("available") is False:
        return phase4
    out = dict(phase4)

    schema = phase4.get("schema") or {}
    if schema:
        missing = schema.get("missing_types") or []
        s = {**schema, "missing_types": missing[:2], "preview": True,
             "locked_missing_count": max(0, len(missing) - 2)}
        bulk = schema.get("bulk")
        if bulk:
            weak = bulk.get("weak_pages") or []
            s["bulk"] = {**bulk, "weak_pages": weak[:2],
                        "locked_weak_page_count": max(0, len(weak) - 2)}
        out["schema"] = s

    links = phase4.get("links") or {}
    if links:
        issues = links.get("issues") or []
        li = {**links, "issues": issues[:2], "preview": True,
             "locked_issue_count": max(0, len(issues) - 2)}
        bulk = links.get("bulk")
        if bulk:
            weak = bulk.get("weak_pages") or []
            li["bulk"] = {**bulk, "weak_pages": weak[:2],
                         "locked_weak_page_count": max(0, len(weak) - 2)}
        out["links"] = li

    entity = phase4.get("entity") or {}
    if entity:
        missing = entity.get("missing_signals") or []
        same_as = entity.get("same_as") or []
        out["entity"] = {**entity, "missing_signals": missing[:2], "preview": True,
                         "locked_missing_signal_count": max(0, len(missing) - 2),
                         "same_as": same_as[:1],
                         "locked_same_as_count": max(0, len(same_as) - 1)}

    q = phase4.get("questions") or {}
    if q:
        questions = q.get("questions") or []
        out["questions"] = {"questions": questions[:3], "preview": True,
                            "locked_question_count": max(0, len(questions) - 3),
                            "total_count": q.get("total_count", len(questions))}

    return out


__all__ = [
    "build_schema_intelligence", "build_link_intelligence",
    "build_entity_intelligence", "build_question_mining",
    "build_phase4_block", "gate_phase4",
    "classify_question", "normalize_question_key",
]
