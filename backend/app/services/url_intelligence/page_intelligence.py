"""Page Intelligence: an evidence-driven analysis of one page, built from what AEOMirror
already stores — the crawl's extracted evidence and checked issues, the page's Page
Opportunities row (GA4 / Search Console / verified conversions), Answer Tracking's
observed citations, and the Action Center's findings. Deterministic: no fetch, no AI
call (AI suggestions are a separate, on-demand step — page_suggestions.py).

What it never does: invent a metric or a query, predict traffic or revenue, promise
that an AI assistant will cite the page, or treat a correlation as a cause. A check
that the stored evidence can't answer is "not_assessable", not a pass or a fail. A
performance pattern needs a minimum sample; a peer comparison needs enough pages of
the same type with enough data, and is based on conversion rate (or, without
conversion data, Google click-through rate — labelled as such), never on raw traffic.

Recommendations reuse the crawl's issue codes where one exists, so a page-level
recommendation and its Action Center card are the same item (linked by finding id),
never two.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from statistics import median
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from app.db.models import (
    FINDING_IN_PROGRESS, FINDING_OPEN, URL_CRAWLED, CrawlUrl, Ga4LandingPageSnapshot, Monitor, PageAnalysis,
    PageValueRun, PageValueScore, PromptResultAnalysis, UrlFinding,
)
from app.services.url_intelligence.classification import COMMERCIAL_TYPES, LOW_PRIORITY_TYPES
from app.services.url_intelligence.issues import CATALOG, observed_value

RULES_VERSION = "1"
ANALYZE_LIMIT = 5000          # pages analysed per run, highest Business Value Score first
CHUNK_SIZE = 200
MIN_GROUP = 3                 # pages needed in each of the successful / weak groups
MIN_QUALIFYING = 9            # pages of one type with enough data before any comparison
MIN_SESSIONS = 30             # sessions before a page's conversion rate is used
MIN_IMPRESSIONS = 200         # Google impressions before a page's CTR is used
MIN_CONVERSION_SESSIONS = 100
MIN_AI_SESSIONS = 30
STALE_DAYS = 365
_PEER_FEATURE_CAP = 300
AI_CLASSES = ("chatgpt", "perplexity", "gemini", "claude", "copilot", "other_ai")

# Minimum words a page of each type usually needs to explain its offer. Advisory
# (a "potential" issue), never a hard rule; types not listed have no expectation.
MIN_WORDS = {"product": 150, "category": 100, "service": 300, "landing": 250, "hotel_property": 250,
             "booking": 150, "blog": 600, "homepage": 250}
# Structured data that is relevant to each business page type. Types not listed get
# no schema recommendation (a privacy policy needs none).
EXPECTED_SCHEMA = {
    "product": ({"Product", "ProductGroup", "IndividualProduct"}, "Product"),
    "category": ({"CollectionPage", "ItemList", "OfferCatalog"}, "CollectionPage or ItemList"),
    "service": ({"Service", "ProfessionalService", "LocalBusiness", "LegalService", "MedicalBusiness",
                 "HomeAndConstructionBusiness"}, "Service"),
    "hotel_property": ({"Hotel", "LodgingBusiness", "Resort", "HotelRoom", "Suite", "VacationRental",
                        "BedAndBreakfast", "Motel", "Hostel", "Accommodation"}, "Hotel or LodgingBusiness"),
    "booking": ({"Reservation", "LodgingReservation", "ReserveAction", "Offer", "Event", "Service"},
                "Offer or Reservation"),
    "blog": ({"Article", "BlogPosting", "NewsArticle", "TechArticle"}, "Article or BlogPosting"),
    "homepage": ({"Organization", "LocalBusiness", "Corporation", "OnlineStore", "WebSite"}, "Organization"),
}

# Recommendation catalog for checks that aren't crawl issue codes (those come from
# issues.CATALOG, so they stay the same item as their Action Center card).
REC = {
    "title_too_short": {"title": "Page title is very short", "severity": "medium", "kind": "confirmed",
        "section": "content", "benefit": "search_visibility", "effort": "low",
        "why": "A very short title rarely says what the page offers, so search results and AI tools have little to go on.",
        "fix": "Write a title of roughly 50–60 characters that names what the page offers and, if relevant, where.",
        "deps": ["Access to edit the page's SEO title in your CMS"],
        "verify": "Re-crawl the page and confirm the title is at least 30 characters."},
    "meta_description_length": {"title": "Meta description length is outside 70–160 characters", "severity": "low",
        "kind": "confirmed", "section": "content", "benefit": "search_visibility", "effort": "low",
        "why": "Search engines usually cut long descriptions and replace very short ones with text of their choosing.",
        "fix": "Rewrite the meta description to about 140–160 characters summarising the page and who it is for.",
        "deps": ["Access to edit the meta description in your CMS"],
        "verify": "Re-crawl the page and confirm the meta description is 70–160 characters."},
    "no_subheadings": {"title": "Long page without section headings", "severity": "medium", "kind": "confirmed",
        "section": "content", "benefit": "ai_answer_readiness", "effort": "medium",
        "why": "Without H2/H3 sections, readers and AI tools can't jump to or quote a specific part of the page.",
        "fix": "Split the content into sections with descriptive H2 headings (one topic each).",
        "deps": ["Content editing access"], "verify": "Re-crawl and confirm the page has H2 or H3 headings."},
    "content_short_for_type": {"title": "Less content than this page type usually needs", "severity": "low",
        "kind": "potential", "section": "content", "benefit": "content_quality", "effort": "medium",
        "why": "Pages of this type usually need more detail to answer a visitor's questions before they act.",
        "fix": "Add the details a buyer would ask about (what's included, who it's for, price/terms, next step).",
        "deps": ["Content editing access", "Product/service facts from your team"],
        "verify": "Re-crawl and compare the word count against the expectation for this page type."},
    "duplicate_title": {"title": "Same title as other pages", "severity": "medium", "kind": "confirmed",
        "section": "content", "benefit": "search_visibility", "effort": "low",
        "why": "Pages sharing one title compete with each other and are hard to tell apart in results.",
        "fix": "Give this page a title that names what is specific to it.",
        "deps": ["Access to edit page titles"], "verify": "Re-crawl and confirm no other crawled page has this title."},
    "duplicate_content": {"title": "Same content as other pages", "severity": "medium", "kind": "potential",
        "section": "content", "benefit": "search_visibility", "effort": "medium",
        "why": "Pages with identical extracted content may be treated as duplicates; only one is likely to be shown.",
        "fix": "Make the page's content specific, or point its canonical link to the main version.",
        "deps": ["Content or template changes"], "verify": "Re-crawl and confirm the content fingerprint is unique."},
    "stale_content": {"title": "Article not updated in over a year", "severity": "low", "kind": "potential",
        "section": "content", "benefit": "content_quality", "effort": "medium",
        "why": "The page's own date says it hasn't been updated for over a year; facts in it may be out of date.",
        "fix": "Review the facts, update what changed, and update the modified date when you publish.",
        "deps": ["Subject-matter review"], "verify": "Re-crawl and check the declared modified date."},
    "product_no_price": {"title": "Product page has no machine-readable price", "severity": "medium",
        "kind": "confirmed", "section": "schema", "benefit": "ai_answer_readiness", "effort": "medium",
        "why": "Without a price in structured data, search engines and AI tools can't reliably state what the product costs.",
        "fix": "Add an Offer with price, priceCurrency and availability to the page's Product structured data.",
        "deps": ["Developer access to the product template"],
        "verify": "Re-crawl and confirm the Product data includes an Offer with a price."},
    "product_offer_incomplete": {"title": "Product offer has no availability", "severity": "low", "kind": "confirmed",
        "section": "schema", "benefit": "ai_answer_readiness", "effort": "low",
        "why": "Availability (in stock / out of stock) is one of the facts shoppers and AI answers look for.",
        "fix": "Add availability (e.g. https://schema.org/InStock) to the product's Offer.",
        "deps": ["Developer access to the product template"],
        "verify": "Re-crawl and confirm the Offer includes availability."},
    "missing_relevant_schema": {"title": "Structured data doesn't describe this page type", "severity": "medium",
        "kind": "potential", "section": "schema", "benefit": "ai_answer_readiness", "effort": "medium",
        "why": "The page has structured data, but none that describes what this page is.",
        "fix": "Add structured data of the type that matches the page, using only facts the page states.",
        "deps": ["Developer access to the page template"],
        "verify": "Re-crawl and confirm the expected structured-data type is present."},
    "schema_malformed": {"title": "Structured data can't be parsed", "severity": "high", "kind": "confirmed",
        "section": "schema", "benefit": "ai_answer_readiness", "effort": "low",
        "why": "JSON-LD that fails to parse is ignored by search engines and AI tools.",
        "fix": "Fix the JSON syntax in the page's JSON-LD block(s) and validate them.",
        "deps": ["Developer access to the page template"],
        "verify": "Re-crawl and confirm every JSON-LD block parses."},
    "entity_name_mismatch": {"title": "Business name differs from the homepage", "severity": "low", "kind": "potential",
        "section": "schema", "benefit": "ai_answer_readiness", "effort": "low",
        "why": "Inconsistent business names make it harder for AI tools to know both pages describe the same business.",
        "fix": "Use the same Organization name on every page (or explain the relationship, e.g. a sub-brand).",
        "deps": ["Developer access to structured data"],
        "verify": "Re-crawl and compare the Organization name with the homepage's."},
    "canonical_elsewhere": {"title": "Canonical link points to a different URL", "severity": "medium",
        "kind": "potential", "section": "technical", "benefit": "search_visibility", "effort": "low",
        "why": "Search engines are asked to index the other URL instead of this one. This may be intentional.",
        "fix": "If this page should be indexed itself, make its canonical link point to its own URL.",
        "deps": ["Developer or CMS access"], "verify": "Re-crawl and confirm the canonical URL."},
    "orphan_page": {"title": "No crawled page links here", "severity": "medium", "kind": "potential",
        "section": "technical", "benefit": "search_visibility", "effort": "low",
        "why": "Pages without internal links are harder for visitors and crawlers to find and get less internal authority.",
        "fix": "Link to this page from related pages (category, navigation, or relevant articles).",
        "deps": ["Content editing access"], "verify": "Re-crawl and confirm at least one crawled page links here."},
    "deep_page": {"title": "Page is four or more clicks from the start page", "severity": "low", "kind": "confirmed",
        "section": "technical", "benefit": "search_visibility", "effort": "medium",
        "why": "Deeply buried pages are crawled less often and are harder for visitors to reach.",
        "fix": "Link to the page from a higher-level page so it is fewer clicks from the homepage.",
        "deps": ["Navigation or content changes"], "verify": "Re-crawl and check the page's crawl depth."},
    "no_question_sections": {"title": "No question-led sections or FAQ", "severity": "low", "kind": "potential",
        "section": "readiness", "benefit": "ai_answer_readiness", "effort": "medium",
        "why": "Sections that answer a specific question make it easy to quote a direct answer from the page.",
        "fix": "Add a short FAQ or question-led sections answering what visitors actually ask (only with real answers).",
        "deps": ["Knowledge of real customer questions"],
        "verify": "Re-crawl and confirm question headings or FAQPage data are present."},
    "low_search_ctr": {"title": "Few Google searchers click through", "severity": "medium", "kind": "potential",
        "section": "performance", "benefit": "search_visibility", "effort": "low",
        "why": "The page is shown in Google Search often but clicked far less than this site's pages on average.",
        "fix": "Rewrite the title and meta description so they state what the page offers and who it is for.",
        "deps": ["Access to edit title and meta description"],
        "verify": "After the change, wait until Search Console reports at least 200 new impressions, then compare CTR."},
    "traffic_without_conversions": {"title": "Traffic but no recorded conversions", "severity": "medium",
        "kind": "potential", "section": "performance", "benefit": "conversion", "effort": "medium",
        "why": "This commercial page gets visits, but no conversion was recorded on sessions that started on it.",
        "fix": "Check that the call to action is visible and works, and that the conversion is tracked on this path.",
        "deps": ["Conversion tracking stays configured"],
        "verify": "After the change, compare conversions per session once at least 100 new sessions are recorded."},
    "conversion_rate_below_peers": {"title": "Converts less than similar pages", "severity": "medium",
        "kind": "potential", "section": "performance", "benefit": "conversion", "effort": "medium",
        "why": "Sessions starting on this page convert at under half the median of comparable pages on this site.",
        "fix": "Compare this page with the best-converting pages of its type (see the comparison) and close the gaps.",
        "deps": ["Conversion tracking stays configured"],
        "verify": "Recompare after at least 100 new sessions."},
    "ai_visitors_not_converting": {"title": "AI-assistant visitors don't convert here", "severity": "low",
        "kind": "potential", "section": "performance", "benefit": "conversion", "effort": "medium",
        "why": "Visitors from AI assistants land here but none converted, while other visitors to the page did.",
        "fix": "Check that what AI assistants say about the page matches what it offers, and that the next step is clear.",
        "deps": ["Conversion tracking stays configured"],
        "verify": "Recheck after at least 30 new AI-assistant sessions."},
}
_CATALOG_SECTION = {"http_5xx": "technical", "http_4xx": "technical", "noindex": "technical",
                    "missing_canonical": "technical", "images_missing_alt": "technical",
                    "missing_title": "content", "title_too_long": "content", "missing_meta_description": "content",
                    "missing_h1": "content", "multiple_h1": "content", "thin_content": "content",
                    "no_structured_data": "schema"}
_CATALOG_META = {  # effort / benefit / dependencies for crawl issue codes
    "http_5xx": ("high", "technical_health", ["Developer or hosting access"]),
    "http_4xx": ("low", "technical_health", ["CMS or redirect access"]),
    "noindex": ("low", "search_visibility", ["CMS or developer access"]),
    "missing_canonical": ("low", "technical_health", ["Developer access to the page template"]),
    "images_missing_alt": ("low", "technical_health", ["Content editing access"]),
    "missing_title": ("low", "search_visibility", ["Access to edit the page's SEO title"]),
    "title_too_long": ("low", "search_visibility", ["Access to edit the page's SEO title"]),
    "missing_meta_description": ("low", "search_visibility", ["Access to edit the meta description"]),
    "missing_h1": ("low", "content_quality", ["Content editing access"]),
    "multiple_h1": ("low", "content_quality", ["Content or template changes"]),
    "thin_content": ("medium", "content_quality", ["Content editing access"]),
    "no_structured_data": ("medium", "ai_answer_readiness", ["Developer access to the page template"]),
}
_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
DISCLAIMER = ("These are associations in your own data, not proof of cause. A change is a hypothesis to test, "
              "and no change guarantees more traffic, citations or revenue.")


# --------------------------------------------------------------------------- features

def _schema_types(ev: dict) -> set[str]:
    return set((ev.get("schema") or {}).get("types") or [])


def _subheading_count(ev: dict) -> int:
    hc = ev.get("heading_counts") or {}
    return int(hc.get("h2", 0) or 0) + int(hc.get("h3", 0) or 0)


def features(ev: dict, page_type: str, inbound_links: int | None, issue_count: int | None) -> dict:
    expected = EXPECTED_SCHEMA.get(page_type)
    return {
        "word_count": ev.get("word_count"),
        "subheadings": _subheading_count(ev),
        "inbound_links": inbound_links,
        "issue_count": issue_count,
        "has_meta_description": bool(ev.get("meta_description")),
        "has_relevant_schema": bool(expected and (_schema_types(ev) & expected[0])),
        "has_question_sections": bool(ev.get("question_headings")) or "FAQPage" in _schema_types(ev),
    }


FEATURE_LABEL = {
    "word_count": ("words of text", "numeric", True), "subheadings": ("section headings", "numeric", True),
    "inbound_links": ("internal links pointing to it", "numeric", True),
    "issue_count": ("checked issues", "numeric", False),
    "has_meta_description": ("a meta description", "share", True),
    "has_relevant_schema": ("structured data for its page type", "share", True),
    "has_question_sections": ("question-led sections or an FAQ", "share", True),
}


def _group_stats(feats: list[dict]) -> dict:
    out = {}
    for key, (_, kind, _) in FEATURE_LABEL.items():
        vals = [f[key] for f in feats if f.get(key) is not None]
        if not vals:
            out[key] = None
        elif kind == "numeric":
            out[key] = float(median(vals))
        else:
            out[key] = round(sum(1 for v in vals if v) / len(vals), 2)
    return out


# --------------------------------------------------------------------------- peer groups

def build_peer_groups(db: Session, run: PageValueRun) -> dict:
    """Per business page type: the successful vs weak pages and how they differ.
    Success is conversions per session (pages with >= MIN_SESSIONS); without
    conversion data, Google click-through rate (pages with >= MIN_IMPRESSIONS),
    labelled as search performance. Never raw traffic volume."""
    inputs = run.inputs or {}
    conv = inputs.get("conversion_source")
    gsc = (inputs.get("sources") or {}).get("gsc", {}).get("available")
    types = [t for (t,) in db.query(PageValueScore.business_page_type)
             .filter(PageValueScore.run_id == run.id).distinct()]
    groups = {}
    for t in types:
        base = db.query(PageValueScore).filter(PageValueScore.run_id == run.id,
                                               PageValueScore.business_page_type == t)
        if conv:
            basis, label = "conversion_rate", "conversions per session"
            rows = (base.filter(PageValueScore.total_sessions >= MIN_SESSIONS, PageValueScore.conversions.isnot(None))
                    .options(load_only(PageValueScore.crawl_url_id, PageValueScore.conversions,
                                       PageValueScore.total_sessions, PageValueScore.inbound_links,
                                       PageValueScore.issue_count)).limit(5000).all())
            metric = {r.crawl_url_id: r.conversions / r.total_sessions for r in rows}
            minimum = f"at least {MIN_SESSIONS} sessions"
        elif gsc:
            basis, label = "search_ctr", "Google click-through rate (search performance, not business outcomes)"
            rows = (base.filter(PageValueScore.gsc_impressions >= MIN_IMPRESSIONS)
                    .options(load_only(PageValueScore.crawl_url_id, PageValueScore.gsc_clicks,
                                       PageValueScore.gsc_impressions, PageValueScore.inbound_links,
                                       PageValueScore.issue_count)).limit(5000).all())
            metric = {r.crawl_url_id: (r.gsc_clicks or 0) / r.gsc_impressions for r in rows}
            minimum = f"at least {MIN_IMPRESSIONS} Google impressions"
        else:
            groups[t] = {"available": False, "reason": "No conversion or Search Console data to tell stronger pages from weaker ones."}
            continue
        if len(rows) < MIN_QUALIFYING:
            groups[t] = {"available": False, "basis": basis, "qualifying": len(rows),
                         "reason": f"Only {len(rows)} page(s) of this type have {minimum}; at least {MIN_QUALIFYING} are needed."}
            continue
        ranked = sorted(rows, key=lambda r: (metric[r.crawl_url_id], r.crawl_url_id))
        third = max(MIN_GROUP, len(ranked) // 3)
        weak, strong = ranked[:third], ranked[-third:]
        weak_cut, strong_cut = metric[weak[-1].crawl_url_id], metric[strong[0].crawl_url_id]
        if strong_cut <= weak_cut:
            groups[t] = {"available": False, "basis": basis, "qualifying": len(rows),
                         "reason": "Pages of this type perform too similarly to separate stronger from weaker ones."}
            continue
        by_id = {r.crawl_url_id: r for r in rows}

        def feats(sample):
            ids = [r.crawl_url_id for r in sample][:_PEER_FEATURE_CAP]
            out = []
            for cu in (db.query(CrawlUrl).options(load_only(CrawlUrl.id, CrawlUrl.evidence))
                       .filter(CrawlUrl.id.in_(ids)).all()):
                r = by_id[cu.id]
                out.append(features(cu.evidence or {}, t, r.inbound_links, r.issue_count))
            return out
        groups[t] = {
            "available": True, "basis": basis, "basis_label": label, "minimum": minimum, "qualifying": len(rows),
            "successful": {"n": len(strong), "min_metric": round(strong_cut, 4),
                           "median_metric": round(median(metric[r.crawl_url_id] for r in strong), 4),
                           "features": _group_stats(feats(strong))},
            "weak": {"n": len(weak), "max_metric": round(weak_cut, 4),
                     "median_metric": round(median(metric[r.crawl_url_id] for r in weak), 4),
                     "features": _group_stats(feats(weak))},
            "type_median_metric": round(median(metric.values()), 4),
        }
    return groups


def compare(page_type: str, feats: dict, page_metric: float | None, groups: dict) -> dict:
    g = groups.get(page_type)
    if not g:
        return {"available": False, "reason": "No comparison data for this page type."}
    if not g.get("available"):
        return {"available": False, "reason": g["reason"]}
    s, w = g["successful"], g["weak"]
    if page_metric is None:
        position = "not_qualifying"
    elif page_metric >= s["min_metric"]:
        position = "successful"
    elif page_metric <= w["max_metric"]:
        position = "weak"
    else:
        position = "middle"
    diffs = []
    for key, (label, kind, higher_better) in FEATURE_LABEL.items():
        sv, wv, pv_ = s["features"].get(key), w["features"].get(key), feats.get(key)
        if sv is None or wv is None or pv_ is None:
            continue
        if kind == "numeric":
            gap = (sv - wv) if higher_better else (wv - sv)
            if gap <= 0 or abs(sv - wv) < 0.25 * max(abs(sv), abs(wv), 1):
                continue    # the groups don't really differ on this
            page_behind = pv_ < sv * 0.75 if higher_better else pv_ > sv * 1.25 + 0.5
            if page_behind:
                diffs.append({"feature": key, "text": (
                    f"Stronger {page_type.replace('_', ' ')} pages (n={s['n']}) have a median of {sv:g} {label}; "
                    f"weaker ones (n={w['n']}) {wv:g}. This page has {pv_:g}.")})
        else:
            if higher_better and sv - wv >= 0.3 and not pv_:
                diffs.append({"feature": key, "text": (
                    f"{round(sv * 100)}% of stronger {page_type.replace('_', ' ')} pages (n={s['n']}) have {label}, "
                    f"vs {round(wv * 100)}% of weaker ones (n={w['n']}). This page doesn't.")})
    return {"available": True, "basis": g["basis"], "basis_label": g["basis_label"], "minimum": g["minimum"],
            "qualifying_pages": g["qualifying"], "position": position, "page_metric": page_metric,
            "successful": {"n": s["n"], "median_metric": s["median_metric"]},
            "weak": {"n": w["n"], "median_metric": w["median_metric"]},
            "differences": diffs, "disclaimer": DISCLAIMER}


# --------------------------------------------------------------------------- the analysis

def _check(section, code, status, title, observed=None, detail=None):
    return {"section": section, "code": code, "status": status, "title": title,
            "observed": observed, "detail": detail}


def _age_days(value: str | None, today: date) -> int | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00")[:25]).date()
    except ValueError:
        try:
            d = date.fromisoformat(value[:10])
        except ValueError:
            return None
    return (today - d).days


def _same_url(a: str | None, b: str | None) -> bool:
    from app.services.analytics.aggregation import page_key
    return bool(a and b) and page_key(a)[0] == page_key(b)[0]


def analyze_page(*, url: str, page_type: str, classification_status: str, evidence: dict, issue_codes: list | None,
                 inbound_links: int | None, perf: dict, ctx: dict, today: date | None = None) -> dict:
    """Pure: the full analysis of one page from its stored inputs. `perf` holds the
    page's measured values (None = not measured); `ctx` the site context (site CTR,
    peer groups, duplicate counts, homepage entity, citations, open findings)."""
    today = today or date.today()
    ev = evidence or {}
    checks: list[dict] = []
    codes: list[str] = []          # failing checks that become recommendations

    def fail(section, code, title, observed=None, status="fail", detail=None):
        checks.append(_check(section, code, status, title, observed, detail))
        codes.append(code)

    crawl_codes = list(issue_codes or [])
    status = ev.get("http_status") or 0
    for code in crawl_codes:   # the crawl's own checked issues, unchanged
        checks.append(_check(_CATALOG_SECTION.get(code, "technical"), code,
                             "warn" if CATALOG.get(code, {}).get("kind") == "potential" else "fail",
                             CATALOG.get(code, {}).get("title", code), observed_value(code, ev)))
        codes.append(code)
    error_page = status >= 400

    # ---------------- A. content
    title = ev.get("title")
    if not error_page:
        if title and len(title) < 30:
            fail("content", "title_too_short", REC["title_too_short"]["title"], f'"{title}" ({len(title)} characters)')
        elif title and "title_too_long" not in crawl_codes:
            checks.append(_check("content", "title", "pass", "Title length", f"{len(title)} characters"))
        md_len = ev.get("meta_description_length") or 0
        if ev.get("meta_description") and not 70 <= md_len <= 160:
            fail("content", "meta_description_length", REC["meta_description_length"]["title"], f"{md_len} characters")
        elif ev.get("meta_description"):
            checks.append(_check("content", "meta", "pass", "Meta description length", f"{md_len} characters"))
        words = ev.get("word_count") or 0
        subs = _subheading_count(ev)
        if words >= 300 and subs == 0:
            fail("content", "no_subheadings", REC["no_subheadings"]["title"], f"{words} words, no H2/H3 headings")
        elif words:
            checks.append(_check("content", "headings", "pass", "Section headings", f"{subs} H2/H3 heading(s)"))
        need = MIN_WORDS.get(page_type)
        if need and "thin_content" not in crawl_codes and words and words < need:
            fail("content", "content_short_for_type", REC["content_short_for_type"]["title"],
                 f"{words} words; {page_type.replace('_', ' ')} pages usually need about {need}+", status="warn")
        dup_titles = ctx.get("duplicate_titles", 0)
        if dup_titles:
            fail("content", "duplicate_title", REC["duplicate_title"]["title"],
                 f"{dup_titles} other crawled page(s) use the title \"{title}\"")
        dup_content = ctx.get("duplicate_content", 0)
        if dup_content and not (ev.get("canonical_url") and not _same_url(ev.get("canonical_url"), url)):
            fail("content", "duplicate_content", REC["duplicate_content"]["title"],
                 f"{dup_content} other crawled page(s) have identical extracted content", status="warn")
        dates = ev.get("dates")
        if page_type == "blog":
            if dates is None:
                checks.append(_check("content", "freshness", "not_assessable", "Content freshness",
                                     detail="This page was crawled before dates were recorded."))
            else:
                age = _age_days(dates.get("modified") or dates.get("published"), today)
                if age is None:
                    checks.append(_check("content", "freshness", "not_assessable", "Content freshness",
                                         detail="The page declares no publication or update date."))
                elif age > STALE_DAYS:
                    fail("content", "stale_content", REC["stale_content"]["title"],
                         f"last dated {dates.get('modified') or dates.get('published')} ({age // 30} months ago)",
                         status="warn")
                else:
                    checks.append(_check("content", "freshness", "pass", "Content freshness",
                                         f"dated {dates.get('modified') or dates.get('published')}"))
        checks.append(_check("content", "readability", "not_assessable", "Readability",
                             detail="The full page text isn't stored, so readability isn't scored."))

    # ---------------- B. technical
    canonical = ev.get("canonical_url")
    if canonical and not error_page and not _same_url(canonical, url):
        fail("technical", "canonical_elsewhere", REC["canonical_elsewhere"]["title"], canonical, status="warn")
    if inbound_links is None:
        checks.append(_check("technical", "internal_links", "not_assessable", "Internal links to this page",
                             detail="This crawl predates internal-link counting."))
    elif inbound_links == 0 and page_type != "homepage":
        fail("technical", "orphan_page", REC["orphan_page"]["title"], "0 crawled pages link here", status="warn")
    else:
        checks.append(_check("technical", "internal_links", "pass", "Internal links to this page",
                             f"{inbound_links} crawled page(s) link here"))
    depth = ev.get("crawl_depth")
    if depth is not None and depth >= 4:
        fail("technical", "deep_page", REC["deep_page"]["title"], f"crawl depth {depth}")
    if status == 200 and not ev.get("noindex"):
        checks.append(_check("technical", "indexable", "pass", "Indexable", "HTTP 200, no noindex"))

    # ---------------- C. schema & entity
    schema = ev.get("schema") or {}
    stypes = _schema_types(ev)
    expected = EXPECTED_SCHEMA.get(page_type)
    if not error_page:
        if schema.get("state") == "malformed" or (schema.get("malformed") or 0) > 0:
            fail("schema", "schema_malformed", REC["schema_malformed"]["title"],
                 f"{schema.get('malformed')} of {schema.get('blocks')} JSON-LD block(s) failed to parse")
        if expected and stypes and not (stypes & expected[0]):
            fail("schema", "missing_relevant_schema", REC["missing_relevant_schema"]["title"],
                 f"found {', '.join(sorted(stypes))}; expected {expected[1]}", status="warn")
        elif expected and stypes & expected[0]:
            checks.append(_check("schema", "relevant_schema", "pass", "Structured data for this page type",
                                 ", ".join(sorted(stypes & expected[0]))))
        elif not expected:
            checks.append(_check("schema", "relevant_schema", "not_applicable", "Structured data for this page type",
                                 detail="No specific structured data is expected for this page type."))
        product = ev.get("product")
        if page_type == "product":
            if not product or not product.get("price"):
                fail("schema", "product_no_price", REC["product_no_price"]["title"],
                     "Product data without an Offer price" if product else "no Product data")
            elif not product.get("availability"):
                fail("schema", "product_offer_incomplete", REC["product_offer_incomplete"]["title"],
                     f"price {product.get('price')} {product.get('price_currency') or ''}, no availability".strip())
        name = (schema.get("entity_evidence") or {}).get("name")
        home = ctx.get("homepage_entity_name")
        if name and home and name.strip().lower() != home.strip().lower():
            fail("schema", "entity_name_mismatch", REC["entity_name_mismatch"]["title"],
                 f'"{name}" here vs "{home}" on the homepage', status="warn")
        elif name and home:
            checks.append(_check("schema", "entity", "pass", "Business name matches the homepage", name))

    # ---------------- D. AI answer readiness (observable signals only)
    readiness = []
    if not error_page:
        def ready(code, ok, title, observed=None, detail=None):
            st = "not_assessable" if ok is None else ("pass" if ok else "warn")
            readiness.append(_check("readiness", code, st, title, observed, detail))
        ready("summary", bool(ev.get("meta_description")) and (ev.get("meta_description_length") or 0) >= 70,
              "A clear summary is available (meta description)",
              f"{ev.get('meta_description_length') or 0} characters")
        ready("subject", bool(ev.get("h1_texts")), "The page names its subject in an H1",
              (ev.get("h1_texts") or [None])[0])
        if (ev.get("word_count") or 0) >= 300:
            ready("sections", _subheading_count(ev) >= 2, "Content is split into descriptive sections",
                  f"{_subheading_count(ev)} H2/H3 heading(s)")
        if expected:
            ready("facts", bool(stypes & expected[0]), "Key facts are machine-readable (structured data)",
                  ", ".join(sorted(stypes)) or "no structured data")
        if "question_headings" in ev:
            has_q = bool(ev.get("question_headings")) or "FAQPage" in stypes
            ready("questions", has_q, "Some sections answer a specific question",
                  f"{ev.get('question_headings')} question heading(s)"
                  + (", FAQPage data" if "FAQPage" in stypes else ""))
            if not has_q and page_type in ("product", "service", "landing", "hotel_property", "booking", "blog"):
                codes.append("no_question_sections")
        else:
            ready("questions", None, "Some sections answer a specific question",
                  detail="Section headings weren't recorded for this crawl.")
        cit = ctx.get("ai_citations")
        if cit is None:
            ready("citations", None, "Cited in tracked AI answers", detail="No completed Answer Tracking run for this website.")
        else:
            readiness.append(_check("readiness", "citations", "info", "Cited in tracked AI answers",
                                    f"{cit['count']} of {cit['answers']} tracked answers in the run of {cit['run_at'][:10]}",
                                    "Observed in your tracked prompts only; not a prediction for other questions."))
    checks.extend(readiness)
    met = sum(1 for c in readiness if c["status"] == "pass")
    assessed = sum(1 for c in readiness if c["status"] in ("pass", "warn"))

    # ---------------- performance patterns (with minimum samples)
    patterns = []
    site_ctr = ctx.get("site_ctr")
    imp, clicks = perf.get("gsc_impressions"), perf.get("gsc_clicks")
    if imp is not None and imp >= MIN_IMPRESSIONS and site_ctr:
        ctr = (clicks or 0) / imp
        if ctr < 0.5 * site_ctr:
            codes.append("low_search_ctr")
            patterns.append({"code": "low_search_ctr", "text": (
                f"{imp:,} Google impressions and {clicks or 0:,} clicks ({ctr * 100:.1f}% CTR) vs a site average of "
                f"{site_ctr * 100:.1f}%."), "sample": f"{imp:,} impressions"})
    sessions, conv = perf.get("total_sessions"), perf.get("conversions")
    if conv is not None and sessions and sessions >= MIN_CONVERSION_SESSIONS and page_type in COMMERCIAL_TYPES:
        if conv == 0:
            codes.append("traffic_without_conversions")
            patterns.append({"code": "traffic_without_conversions",
                             "text": f"{sessions:,} sessions started here and 0 conversions were recorded.",
                             "sample": f"{sessions:,} sessions"})
        else:
            g = (ctx.get("peers") or {}).get(page_type) or {}
            med = g.get("type_median_metric") if g.get("basis") == "conversion_rate" else None
            rate = conv / sessions
            if med and rate < 0.5 * med:
                codes.append("conversion_rate_below_peers")
                patterns.append({"code": "conversion_rate_below_peers", "text": (
                    f"{rate * 100:.1f} conversions per 100 sessions vs a median of {med * 100:.1f} for "
                    f"{g.get('qualifying')} comparable {page_type.replace('_', ' ')} pages."),
                    "sample": f"{sessions:,} sessions; {g.get('qualifying')} peer pages"})
            elif med and rate >= 2 * med:
                patterns.append({"code": "outperforming_peers", "positive": True, "text": (
                    f"{rate * 100:.1f} conversions per 100 sessions — at least twice the median of comparable pages "
                    f"({med * 100:.1f})."), "sample": f"{sessions:,} sessions"})
    ai = perf.get("ai_split")
    if ai and ai["ai_sessions"] >= MIN_AI_SESSIONS and ai["ai_conversions"] == 0 and ai["other_conversions"] > 0:
        codes.append("ai_visitors_not_converting")
        patterns.append({"code": "ai_visitors_not_converting", "text": (
            f"{ai['ai_sessions']} AI-assistant sessions with 0 key events, while {ai['other_sessions']:,} other "
            f"sessions recorded {ai['other_conversions']}."), "sample": f"{ai['ai_sessions']} AI sessions"})
    if perf.get("score") is not None and perf["score"] >= 60 and page_type in COMMERCIAL_TYPES:
        crawl_sev = max((_PRIORITY_ORDER.get(CATALOG[c]["severity"], 3) for c in crawl_codes if c in CATALOG), default=9)
        if crawl_sev <= 1:
            patterns.append({"code": "valuable_page_with_serious_issue", "text": (
                f"A high-value {page_type.replace('_', ' ')} page (score {perf['score']:g}) has a high or critical checked issue."),
                "sample": None})

    # ---------------- comparison
    feats = features(ev, page_type, inbound_links, len(crawl_codes) if issue_codes is not None else None)
    groups = ctx.get("peers") or {}
    g = groups.get(page_type) or {}
    if g.get("basis") == "conversion_rate":
        metric = conv / sessions if conv is not None and sessions and sessions >= MIN_SESSIONS else None
    elif g.get("basis") == "search_ctr":
        metric = (clicks or 0) / imp if imp and imp >= MIN_IMPRESSIONS else None
    else:
        metric = None
    comparison = compare(page_type, feats, metric, groups)

    # ---------------- recommendations
    recs = build_recommendations(url=url, page_type=page_type, classification_status=classification_status,
                                 evidence=ev, codes=codes, checks=checks, patterns=patterns,
                                 comparison=comparison, perf=perf, ctx=ctx)
    return {
        "rules_version": RULES_VERSION,
        "summary": {"top_issue": recs[0] if recs else None, "recommendation_count": len(recs),
                    "readiness": {"met": met, "assessed": assessed}},
        "checks": checks, "patterns": patterns, "comparison": comparison, "recommendations": recs,
        "readiness_note": ("Readiness checks look at observable structure only. No check can guarantee that "
                           "ChatGPT, Gemini, Claude or any AI assistant will cite this page."),
        "disclaimer": DISCLAIMER,
    }


def _example(code: str, ev: dict, url: str, page_type: str) -> dict | None:
    """A concrete starting point built only from the page's own values (never AI)."""
    title = ev.get("title")
    if code in ("title_too_short", "missing_title", "title_too_long", "duplicate_title", "low_search_ctr"):
        return {"label": "Current title", "current": title or "(none)",
                "pattern": "[What the page offers] – [key detail or location] | [Brand]  (about 50–60 characters)"}
    if code in ("missing_meta_description", "meta_description_length"):
        return {"label": "Current meta description", "current": ev.get("meta_description") or "(none)",
                "pattern": "[What it is] for [who]. [One concrete detail]. [Next step].  (about 140–160 characters)"}
    if code in ("no_structured_data", "missing_relevant_schema", "product_no_price", "product_offer_incomplete"):
        expected = EXPECTED_SCHEMA.get(page_type)
        if not expected:
            return None
        stype = expected[1].split(" or ")[0]
        node = {"@context": "https://schema.org", "@type": stype, "name": title or "<page subject>", "url": url}
        if stype == "Product":
            p = ev.get("product") or {}
            node["offers"] = {"@type": "Offer", "price": p.get("price") or "<price>",
                              "priceCurrency": p.get("price_currency") or "<currency>",
                              "availability": p.get("availability") or "https://schema.org/InStock"}
        return {"label": f"{stype} structured data (template — fill the <placeholders> with real values only)",
                "code": json.dumps(node, indent=2)}
    return None


def build_recommendations(*, url, page_type, classification_status, evidence, codes, checks, patterns,
                          comparison, perf, ctx) -> list[dict]:
    findings = ctx.get("findings") or {}
    by_check = {c["code"]: c for c in checks}
    by_pattern = {p["code"]: p for p in patterns}
    score = perf.get("score")
    outcome = (perf.get("conversions") or 0) > 0 or (perf.get("revenue") or 0) > 0
    type_dependent = {"content_short_for_type", "missing_relevant_schema", "product_no_price",
                      "product_offer_incomplete", "stale_content", "no_question_sections", "no_structured_data"}
    out, seen = [], set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        if code in CATALOG:
            spec = CATALOG[code]
            effort, benefit, deps = _CATALOG_META.get(code, ("medium", "technical_health", []))
            spec = {"title": spec["title"], "severity": spec["severity"], "kind": spec["kind"], "why": spec["why"],
                    "fix": spec["fix"], "verify": spec["verify"], "effort": effort, "benefit": benefit, "deps": deps,
                    "section": _CATALOG_SECTION.get(code, "technical")}
            if code == "no_structured_data" and EXPECTED_SCHEMA.get(page_type):
                spec["fix"] = f"Add {EXPECTED_SCHEMA[page_type][1]} structured data using only facts the page states."
        else:
            spec = REC[code]
        evidence_lines = []
        if code in by_check and by_check[code].get("observed"):
            evidence_lines.append(f"Observed on this page: {by_check[code]['observed']}")
        if code in by_pattern:
            evidence_lines.append(by_pattern[code]["text"])
        if code in ("content_short_for_type", "no_subheadings", "no_question_sections", "orphan_page",
                    "missing_relevant_schema", "no_structured_data", "conversion_rate_below_peers") \
                and comparison.get("available"):
            evidence_lines += [d["text"] for d in comparison["differences"]][:2]
        # priority: severity, adjusted by how much the page matters
        prio = spec["severity"]
        if prio != "critical":
            if score is not None and score >= 60 and page_type in COMMERCIAL_TYPES and prio in ("medium", "low") \
                    and spec["section"] in ("technical", "schema", "performance"):
                prio = {"medium": "high", "low": "medium"}[prio]
            elif score is not None and score < 35:
                prio = {"high": "medium", "medium": "low", "low": "low"}[prio]
            if page_type in LOW_PRIORITY_TYPES and not outcome:
                prio = "low"
        if spec["section"] == "performance":
            confidence = "medium"
        elif code in type_dependent:
            confidence = {"confirmed": "high", "likely": "medium"}.get(classification_status, "low")
        else:
            confidence = "high" if spec["kind"] == "confirmed" else "medium"
        finding = findings.get(code)
        out.append({
            "code": code, "issue": spec["title"], "url": url, "section": spec["section"],
            "kind": spec["kind"], "evidence": evidence_lines or ["Checked on this page during the latest crawl."],
            "why_it_matters": spec["why"], "recommended_change": spec["fix"],
            "example": _example(code, evidence, url, page_type),
            "priority": prio, "confidence": confidence, "effort": spec["effort"],
            "benefit_category": spec["benefit"], "dependencies": spec["deps"], "verification": spec["verify"],
            "action_center_finding": finding,   # the same item, already tracked there — never a second card
        })
    out.sort(key=lambda r: (_PRIORITY_ORDER[r["priority"]], {"high": 0, "medium": 1, "low": 2}[r["confidence"]],
                            {"performance": 0, "technical": 1, "content": 2, "schema": 3, "readiness": 4}[r["section"]]))
    return out


# --------------------------------------------------------------------------- context + persistence

def _bucket(v):
    return None if v is None else int(math.log2(v + 1))


def input_fingerprint(row: CrawlUrl, score: PageValueScore, ctx: dict, perf: dict) -> str:
    """Changes when the page's content/evidence, its checked issues, its peer group or
    its performance changes meaningfully (performance is bucketed on a log scale, so a
    handful of extra sessions doesn't trigger re-analysis)."""
    peers = (ctx.get("peers") or {}).get(score.business_page_type)
    basis = {
        "v": RULES_VERSION, "evidence": row.evidence, "codes": row.issue_codes, "links": row.inbound_link_count,
        "type": [score.business_page_type, score.classification_status],
        "perf": {k: (int(v // 10) if k == "score" else _bucket(v)) if isinstance(v, (int, float)) else v
                 for k, v in perf.items() if k != "ai_split"},
        "ai_split": {k: _bucket(v) for k, v in (perf.get("ai_split") or {}).items()},
        "peers": peers, "ctx": {k: ctx.get(k) for k in ("duplicate_titles", "duplicate_content",
                                                        "homepage_entity_name", "ai_citations", "findings")},
        "site_ctr": round(ctx["site_ctr"], 3) if ctx.get("site_ctr") else None,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def prepare(db: Session, run: PageValueRun) -> dict:
    """Site-level context computed once per run: peer groups, the homepage's entity
    name, and the latest real Answer Tracking run's cited URLs."""
    from app.services.analytics.aggregation import page_key
    from app.services.command_center import _latest_real_answer_run

    out = {"peers": build_peer_groups(db, run), "homepage_entity_name": None, "ai_citations": None}
    for cu in (db.query(CrawlUrl).options(load_only(CrawlUrl.url, CrawlUrl.evidence))
               .filter(CrawlUrl.crawl_job_id == run.crawl_job_id, CrawlUrl.status == URL_CRAWLED,
                       CrawlUrl.discovery_depth == 0).limit(5)):
        if (urlparse(cu.url).path or "/") in ("/", ""):
            out["homepage_entity_name"] = (((cu.evidence or {}).get("schema") or {})
                                           .get("entity_evidence") or {}).get("name")
            break
    monitor = db.get(Monitor, run.monitor_id)
    answer_run = _latest_real_answer_run(db, run.organization_id, monitor) if monitor else None
    if answer_run is not None:
        rows = (db.query(PromptResultAnalysis.brand_urls_cited)
                .filter(PromptResultAnalysis.run_id == answer_run.id,
                        PromptResultAnalysis.extraction_failed.is_(False)).all())
        by_key: dict[str, int] = {}
        for (cited,) in rows:
            for k in {page_key(u)[0] for u in (cited or []) if isinstance(u, str)}:
                by_key[k] = by_key.get(k, 0) + 1
        out["ai_citations"] = {"run_id": answer_run.id, "run_at": answer_run.created_at.isoformat(),
                               "answers": len(rows), "by_key": dict(list(by_key.items())[:5000])}
    return out


def _chunk_context(db: Session, run: PageValueRun, rows: list[CrawlUrl]) -> dict:
    titles = [r.page_title for r in rows if r.page_title]
    fps = [r.content_fingerprint for r in rows if r.content_fingerprint]
    title_counts = dict(db.query(CrawlUrl.page_title, func.count(CrawlUrl.id))
                        .filter(CrawlUrl.crawl_job_id == run.crawl_job_id, CrawlUrl.status == URL_CRAWLED,
                                CrawlUrl.page_title.in_(titles)).group_by(CrawlUrl.page_title).all()) if titles else {}
    fp_counts = dict(db.query(CrawlUrl.content_fingerprint, func.count(CrawlUrl.id))
                     .filter(CrawlUrl.crawl_job_id == run.crawl_job_id, CrawlUrl.status == URL_CRAWLED,
                             CrawlUrl.content_fingerprint.in_(fps)).group_by(CrawlUrl.content_fingerprint).all()) if fps else {}
    keys = {f"det:{c}:{r.template_id or 'unclassified'}" for r in rows for c in (r.issue_codes or [])}
    findings = {}
    if keys:
        for f in (db.query(UrlFinding).filter(UrlFinding.crawl_job_id == run.crawl_job_id,
                                              UrlFinding.issue_key.in_(list(keys)),
                                              UrlFinding.status.in_((FINDING_OPEN, FINDING_IN_PROGRESS))).all()):
            findings[f.issue_key] = {"id": f.id, "title": f.finding_title, "status": f.status,
                                     "verification_status": f.verification_status}
    return {"title_counts": title_counts, "fp_counts": fp_counts, "findings": findings}


def _ai_split(db: Session, run: PageValueRun, paths: set[str]) -> dict[str, dict]:
    """Per path: AI vs other sessions and GA4 key events (only when conversions come
    from GA4 key events — the per-source split exists only there)."""
    inputs = run.inputs or {}
    ga4 = (inputs.get("sources") or {}).get("ga4") or {}
    if inputs.get("conversion_source") != "ga4_key_events" or not ga4.get("available") or not paths:
        return {}
    variants = set()
    for p in paths:
        base = p.rstrip("/") or "/"
        variants |= {base, base + "/"} if base != "/" else {"/"}
    rows = (db.query(Ga4LandingPageSnapshot.landing_page, Ga4LandingPageSnapshot.traffic_class,
                     func.sum(Ga4LandingPageSnapshot.sessions), func.sum(Ga4LandingPageSnapshot.key_events))
            .filter(Ga4LandingPageSnapshot.connection_id == ga4["connection_id"],
                    Ga4LandingPageSnapshot.date >= ga4["start_key"], Ga4LandingPageSnapshot.date <= ga4["end_key"],
                    Ga4LandingPageSnapshot.landing_page.in_(list(variants)),
                    Ga4LandingPageSnapshot.traffic_class.isnot(None))
            .group_by(Ga4LandingPageSnapshot.landing_page, Ga4LandingPageSnapshot.traffic_class).all())
    out: dict[str, dict] = {}
    for page, cls, sessions, kev in rows:
        d = out.setdefault(page.rstrip("/") or "/", {"ai_sessions": 0, "ai_conversions": 0,
                                                      "other_sessions": 0, "other_conversions": 0})
        side = "ai" if cls in AI_CLASSES else "other"
        d[f"{side}_sessions"] += int(sessions or 0)
        d[f"{side}_conversions"] += int(kev or 0)
    return out


def analyze_rows(db: Session, run: PageValueRun, scores: list[PageValueScore], *, force: bool = False) -> dict:
    """Analyse these pages of `run`. Unchanged inputs → the previous analysis stays
    current (counted as reused). A failure on one page is logged and skipped, never
    replacing that page's last good analysis. Returns counts."""
    from app.services.analytics.aggregation import page_key

    ctx_site = (run.inputs or {}).get("page_intelligence") or {}
    rows = {r.id: r for r in db.query(CrawlUrl).filter(CrawlUrl.id.in_([s.crawl_url_id for s in scores])).all()}
    crawled = [rows[s.crawl_url_id] for s in scores if s.crawl_url_id in rows and rows[s.crawl_url_id].status == URL_CRAWLED]
    chunk = _chunk_context(db, run, crawled)
    splits = _ai_split(db, run, {page_key(r.url)[1] for r in crawled})
    site_ctr = (run.inputs or {}).get("site_ctr")
    counts = {"analyzed": 0, "reused": 0, "skipped": 0, "failed": 0}
    for s in scores:
        row = rows.get(s.crawl_url_id)
        if row is None or row.status != URL_CRAWLED or not row.evidence:
            counts["skipped"] += 1
            continue
        try:
            key = row.normalized_key
            path = page_key(row.url)[1]
            cit = ctx_site.get("ai_citations")
            ctx = {
                "site_ctr": site_ctr, "peers": ctx_site.get("peers") or {},
                "homepage_entity_name": ctx_site.get("homepage_entity_name"),
                "duplicate_titles": max(0, chunk["title_counts"].get(row.page_title, 1) - 1) if row.page_title else 0,
                "duplicate_content": max(0, chunk["fp_counts"].get(row.content_fingerprint, 1) - 1)
                                     if row.content_fingerprint else 0,
                "ai_citations": None if cit is None else {"count": cit["by_key"].get(page_key(row.url)[0], 0),
                                                          "answers": cit["answers"], "run_at": cit["run_at"]},
                "findings": {c: chunk["findings"][f"det:{c}:{row.template_id or 'unclassified'}"]
                             for c in (row.issue_codes or [])
                             if f"det:{c}:{row.template_id or 'unclassified'}" in chunk["findings"]},
            }
            perf = {"score": s.score, "total_sessions": s.total_sessions, "ai_sessions": s.ai_sessions,
                    "organic_sessions": s.organic_sessions, "conversions": s.conversions, "revenue": s.revenue,
                    "gsc_clicks": s.gsc_clicks, "gsc_impressions": s.gsc_impressions,
                    "ai_split": splits.get(path.rstrip("/") or "/")}
            fp = input_fingerprint(row, s, ctx, perf)
            latest = (db.query(PageAnalysis.input_fingerprint)
                      .filter(PageAnalysis.monitor_id == run.monitor_id, PageAnalysis.normalized_key == key)
                      .order_by(PageAnalysis.created_at.desc()).first())
            if latest and latest[0] == fp and not force:
                counts["reused"] += 1
                continue
            result = analyze_page(url=row.url, page_type=s.business_page_type,
                                  classification_status=s.classification_status, evidence=row.evidence,
                                  issue_codes=row.issue_codes, inbound_links=row.inbound_link_count,
                                  perf=perf, ctx=ctx)
            db.add(PageAnalysis(
                organization_id=run.organization_id, monitor_id=run.monitor_id, normalized_key=key,
                crawl_job_id=run.crawl_job_id, crawl_url_id=row.id, run_id=run.id, url=row.url,
                page_type=s.business_page_type, input_fingerprint=fp, rules_version=RULES_VERSION, result=result,
                top_issue_code=(result["summary"]["top_issue"] or {}).get("code"),
                recommendation_codes=[r["code"] for r in result["recommendations"]], created_at=datetime.utcnow()))
            db.commit()   # per page, so a later page's failure can't roll this one back
            counts["analyzed"] += 1
        except Exception:   # noqa: BLE001 — one bad page never stops the run or replaces a good result
            import logging
            logging.getLogger("app.page_intelligence").warning("page analysis failed for %s", s.crawl_url_id)
            db.rollback()
            counts["failed"] += 1
    db.commit()
    return counts


def _ranked(db: Session, run: PageValueRun):
    return (db.query(PageValueScore).join(CrawlUrl, CrawlUrl.id == PageValueScore.crawl_url_id)
            .filter(PageValueScore.run_id == run.id, CrawlUrl.status == URL_CRAWLED)
            .order_by(PageValueScore.score.desc(), PageValueScore.crawl_url_id.asc()))


def run_analysis_chunk(db: Session, run: PageValueRun, chunk_size: int = CHUNK_SIZE) -> None:
    """One bounded unit of the analysis stage: prepare site context, then analyse the
    next `chunk_size` pages in Business Value order (highest first), up to ANALYZE_LIMIT."""
    if run.analysis_status == "pending":
        inputs = dict(run.inputs or {})
        inputs["page_intelligence"] = prepare(db, run)
        run.inputs = inputs
        run.analysis_total = min(ANALYZE_LIMIT, _ranked(db, run).count())
        run.analysis_cursor, run.analyzed_urls = 0, 0
        run.analysis_status = "running"
        run.updated_at = datetime.utcnow()
        db.commit()
        return
    cursor = run.analysis_cursor or 0
    take = max(0, min(chunk_size, (run.analysis_total or 0) - cursor))
    scores = _ranked(db, run).offset(cursor).limit(take).all() if take else []
    if scores:
        analyze_rows(db, run, scores)
    db.refresh(run, attribute_names=["analysis_status"])
    if run.analysis_status == "cancelled":
        return
    run.analysis_cursor = cursor + len(scores)
    run.analyzed_urls = run.analysis_cursor
    if run.analysis_cursor >= (run.analysis_total or 0) or len(scores) < take:
        run.analysis_status = "completed"
    run.updated_at = datetime.utcnow()
    db.commit()
    if run.analysis_status == "completed":
        # The new crawl is evidence for every tracked fix: verify / reopen / re-baseline.
        from app.services.url_intelligence import optimization
        try:
            optimization.sync_after_analysis(db, run)
        except Exception:   # noqa: BLE001 — never turns a completed analysis into a failure
            db.rollback()
            import logging
            logging.getLogger("app.page_intelligence").warning("optimization sync failed for run %s", run.id)


# --------------------------------------------------------------------------- read models

def latest_analysis(db: Session, monitor_id: str, normalized_key: str) -> PageAnalysis | None:
    return (db.query(PageAnalysis)
            .filter(PageAnalysis.monitor_id == monitor_id, PageAnalysis.normalized_key == normalized_key)
            .order_by(PageAnalysis.created_at.desc(), PageAnalysis.id.desc()).first())


def history(db: Session, monitor_id: str, normalized_key: str, limit: int = 10) -> list[dict]:
    """Previous analyses of the same page, newest first, with which recommendations
    were resolved since the one before (their check passed on a later crawl)."""
    rows = (db.query(PageAnalysis)
            .filter(PageAnalysis.monitor_id == monitor_id, PageAnalysis.normalized_key == normalized_key)
            .order_by(PageAnalysis.created_at.desc(), PageAnalysis.id.desc()).limit(limit + 1).all())
    out = []
    for i, a in enumerate(rows[:limit]):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        cur_codes = set(a.recommendation_codes or [])
        prev_codes = set(prev.recommendation_codes or []) if prev else set()
        same_crawl = prev is not None and prev.crawl_job_id == a.crawl_job_id
        out.append({"id": a.id, "analyzed_at": a.created_at, "crawl_job_id": a.crawl_job_id,
                    "top_issue": a.top_issue_code, "recommendations": len(cur_codes),
                    "resolved_since_previous": sorted(prev_codes - cur_codes) if prev and not same_crawl else [],
                    "new_since_previous": sorted(cur_codes - prev_codes) if prev else []})
    return out
