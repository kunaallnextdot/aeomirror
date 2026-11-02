"""Fix verification via recrawl (Phase 9: "verify fixes through recrawling, compare
previous and current observations"). Evidence-based, same state vocabulary as
`app.services.verification` (VERIFY_* in app.db.models) but comparing deterministic
evidence for one URL rather than scan-signal scores — a genuinely different
comparison, so this is a new small module rather than a forced reuse of that one.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import anyio

from sqlalchemy.orm import Session

from app.core.fetch import FetchError, fetch
from app.core.ssrf import UnsafeUrlError, validate_url
from app.db.models import (
    CrawlUrl, UrlFinding, VERIFY_NOT_COMPARABLE, VERIFY_PARTIALLY_IMPROVED,
    VERIFY_REGRESSED, VERIFY_UNCHANGED, VERIFY_VERIFIED,
)
from app.services.url_intelligence.evidence import content_fingerprint, extract_evidence
from app.services.url_intelligence.issues import page_issues

_VERIFY_SAMPLE = 5   # affected pages re-checked per verification (bounded request time)


async def _recheck(url: str, code: str, page_type: str | None) -> bool | None:
    """True = issue still present, False = gone, None = page couldn't be re-checked."""
    try:
        safe = validate_url(url)
        bundle = await fetch(safe)
    except (FetchError, UnsafeUrlError):
        return None
    evidence = await anyio.to_thread.run_sync(extract_evidence, bundle)
    return code in page_issues(evidence, page_type)


async def _verify_deterministic(db: Session, finding: UrlFinding) -> str:
    """Re-check THIS issue on a sample of the pages it was found on. Verified only when
    every re-checked page no longer has it; the sample size is recorded with the result
    so a 5-page check is never presented as a check of all pages."""
    ev = dict(finding.evidence or {})
    code = ev.get("issue_code")
    urls = (ev.get("affected_urls") or [])[:_VERIFY_SAMPLE]
    results = await asyncio.gather(*(_recheck(u, code, finding.page_type) for u in urls)) if code else []
    checked = [r for r in results if r is not None]
    if not checked:
        status = VERIFY_NOT_COMPARABLE
    elif not any(checked):
        status = VERIFY_VERIFIED
    elif all(checked):
        status = VERIFY_UNCHANGED
    else:
        status = VERIFY_PARTIALLY_IMPROVED
    ev["last_verification"] = {
        "at": datetime.utcnow().isoformat(), "affected_count": ev.get("affected_count"),
        "pages": [{"url": u, "still_present": r} for u, r in zip(urls, results)],
    }
    finding.evidence = ev          # reassigned so the JSON change is persisted
    finding.verification_status = status
    finding.last_checked_at = datetime.utcnow()
    db.commit()
    return status


async def verify_finding(db: Session, finding: UrlFinding) -> str:
    """Re-fetch the finding's URL, recompute evidence, and compare the deterministic
    `missing_content_elements` before/after. Updates finding.verification_status +
    last_checked_at and, when the URL still belongs to an existing CrawlUrl row,
    refreshes that row's evidence/fingerprint too. Never raises — an unreachable URL
    is NOT_COMPARABLE, not a crash."""
    if finding.evidence_basis == "deterministic":
        return await _verify_deterministic(db, finding)
    if not finding.url:
        finding.verification_status = VERIFY_NOT_COMPARABLE
        finding.last_checked_at = datetime.utcnow()
        db.commit()
        return finding.verification_status

    try:
        safe = validate_url(finding.url)
        bundle = await fetch(safe)
    except (FetchError, UnsafeUrlError):
        finding.verification_status = VERIFY_NOT_COMPARABLE
        finding.last_checked_at = datetime.utcnow()
        db.commit()
        return finding.verification_status

    new_evidence = extract_evidence(bundle)
    new_fingerprint = content_fingerprint(new_evidence)

    old_missing = set((finding.evidence or {}).get("missing_content_elements") or [])
    new_missing = set(new_evidence.get("missing_content_elements") or [])

    if new_fingerprint == finding.content_fingerprint:
        status = VERIFY_UNCHANGED
    elif not new_missing and old_missing:
        status = VERIFY_VERIFIED
    elif len(new_missing) < len(old_missing):
        status = VERIFY_PARTIALLY_IMPROVED
    elif len(new_missing) > len(old_missing):
        status = VERIFY_REGRESSED
    else:
        status = VERIFY_UNCHANGED

    finding.verification_status = status
    finding.last_checked_at = datetime.utcnow()

    if finding.crawl_url_id:
        row = db.get(CrawlUrl, finding.crawl_url_id)
        if row:
            row.evidence = new_evidence
            row.content_fingerprint = new_fingerprint
            row.http_status = bundle.status_code
            row.crawled_at = datetime.utcnow()

    db.commit()
    return status


# --------------------------------------------------------------------------- URL actions
# Per-page verification for URL-wise optimization (one Page Intelligence recommendation on
# one page). Same principle as above: fetch the page again, rebuild its evidence and
# re-run the SAME deterministic rules that raised the issue (page_intelligence.
# analyze_page + issues.page_issues). Nothing here reads a score: a fix is "fixed" only
# when the check that found it now passes on fresh evidence.

PERFORMANCE_CODES = frozenset({"low_search_ctr", "traffic_without_conversions", "conversion_rate_below_peers",
                               "ai_visitors_not_converting"})
SITE_CRAWL_CODES = frozenset({"orphan_page", "deep_page"})   # depend on the whole site's links
_HTTP_CODES = ("http_4xx", "http_5xx")

# The evidence fields each check reads: what is kept as "before" and shown as "after".
_KEYS = {
    "http_5xx": [], "http_4xx": [], "noindex": ["noindex", "robots_meta"],
    "missing_title": ["title"], "title_too_long": ["title", "title_length"],
    "title_too_short": ["title", "title_length"], "missing_meta_description": ["meta_description"],
    "meta_description_length": ["meta_description_length"],
    "missing_h1": ["h1_texts", "heading_counts"], "multiple_h1": ["h1_texts", "heading_counts"],
    "thin_content": ["word_count"], "content_short_for_type": ["word_count"],
    "no_subheadings": ["word_count", "subheadings", "heading_counts"],
    "no_structured_data": ["schema"], "missing_relevant_schema": ["schema"], "schema_malformed": ["schema"],
    "entity_name_mismatch": ["schema"], "product_no_price": ["product"], "product_offer_incomplete": ["product"],
    "missing_canonical": ["canonical_url"], "canonical_elsewhere": ["canonical_url"],
    "images_missing_alt": ["images_missing_alt", "image_count"], "stale_content": ["dates"],
    "no_question_sections": ["question_headings", "subheadings"], "duplicate_title": ["title"],
    "duplicate_content": [], "orphan_page": [], "deep_page": ["crawl_depth"],
}


def _clip(v):
    if isinstance(v, str):
        return v[:300]
    if isinstance(v, list):
        return [_clip(x) for x in v[:25]]
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in list(v.items())[:25]}
    return v


def relevant_evidence(code: str, evidence: dict | None, ctx: dict | None = None,
                      inbound_links: int | None = None) -> dict:
    """The small slice of a page's evidence one check depends on (plus the HTTP status
    and the site-level counts it uses), kept as the before/after record."""
    ev = evidence or {}
    out = {"http_status": ev.get("http_status")}
    for k in _KEYS.get(code, []):
        if k in ev:
            out[k] = _clip(ev.get(k))
    c = ctx or {}
    if code == "duplicate_title":
        out["other_pages_with_this_title"] = c.get("duplicate_titles", 0)
    if code == "duplicate_content":
        out["other_pages_with_this_content"] = c.get("duplicate_content", 0)
    if code == "orphan_page":
        out["inbound_links"] = inbound_links
    return out


def _measure(code: str, rel: dict | None) -> float | None:
    """A quantity that falls as the issue is fixed, for codes where part of a fix can be
    observed (fewer images without alt text, more words). None = all-or-nothing."""
    r = rel or {}
    if code == "images_missing_alt":
        return r.get("images_missing_alt")
    if code in ("thin_content", "content_short_for_type"):
        return -r["word_count"] if r.get("word_count") is not None else None
    if code == "multiple_h1":
        return (r.get("heading_counts") or {}).get("h1")
    if code == "duplicate_title":
        return r.get("other_pages_with_this_title")
    if code == "duplicate_content":
        return r.get("other_pages_with_this_content")
    if code == "schema_malformed":
        return (r.get("schema") or {}).get("malformed")
    return None


def evaluate_codes(codes, *, url: str, page_type: str, classification_status: str, evidence: dict,
                   crawl_page_type: str | None, ctx: dict, inbound_links: int | None = None) -> dict[str, dict]:
    """Re-run the deterministic checks on `evidence` for each recommendation code.
    Returns {code: {"present": True|False|None, "observed", "reason", "evidence"}};
    present=None means this evidence can't decide it (never read as fixed)."""
    from app.services.url_intelligence.page_intelligence import analyze_page

    ev = evidence or {}
    http = ev.get("http_status") or 0
    crawl_codes = page_issues(ev, crawl_page_type)
    result = analyze_page(url=url, page_type=page_type, classification_status=classification_status, evidence=ev,
                          issue_codes=crawl_codes, inbound_links=inbound_links, perf={}, ctx=ctx)
    present_codes = {r["code"] for r in result["recommendations"]}
    by_check = {c["code"]: c for c in result["checks"]}
    out = {}
    for code in codes:
        reason = None
        if code in PERFORMANCE_CODES:
            reason = ("This is a performance pattern, not something a page check can confirm. "
                      "Compare the before-and-after performance instead.")
        elif code in SITE_CRAWL_CODES and inbound_links is None:
            reason = "This depends on links from other pages, so it is checked at the next full site crawl."
        elif http >= 400 and code not in _HTTP_CODES:
            reason = f"The page answered HTTP {http}, so this check couldn't run."
        elif code == "no_question_sections" and "question_headings" not in ev:
            reason = "Section headings weren't recorded for this page."
        elif code == "stale_content" and not ((ev.get("dates") or {}).get("modified")
                                              or (ev.get("dates") or {}).get("published")):
            reason = "The page declares no publication or update date, so freshness can't be checked."
        elif code == "entity_name_mismatch" and not ctx.get("homepage_entity_name"):
            reason = "The homepage's business name isn't known, so the names can't be compared."
        check = by_check.get("questions" if code == "no_question_sections" else code) or {}
        out[code] = {"present": None if reason else code in present_codes, "reason": reason,
                     "observed": check.get("observed") if code in present_codes else None,
                     "evidence": relevant_evidence(code, ev, ctx, inbound_links)}
    return out


def outcome(code: str, before: dict | None, check: dict) -> str:
    """fixed / partially_fixed / not_fixed / unable_to_verify for one re-run check."""
    if check["present"] is None:
        return "unable_to_verify"
    if not check["present"]:
        return "fixed"
    b, a = _measure(code, before), _measure(code, check["evidence"])
    if b is not None and a is not None and a < b:
        return "partially_fixed"
    return "not_fixed"


async def fetch_page_evidence(url: str, *, crawl_depth: int = 0, expected_key: str | None = None):
    """(evidence, None) for a fetched page, or (None, reason) when it couldn't be
    fetched — or now redirects to a different page, whose evidence would describe
    something else."""
    from app.scanner.bulk import normalize_dedup_key

    try:
        safe = validate_url(url)
        bundle = await fetch(safe)
    except UnsafeUrlError:
        return None, "The URL is not allowed to be fetched."
    except FetchError:
        return None, "The page couldn't be fetched (no response or a network error)."
    final = getattr(bundle, "final_url", "") or ""
    if expected_key and final and normalize_dedup_key(final) != expected_key:
        return None, f"The URL now redirects to {final[:200]}, a different page."
    evidence = await anyio.to_thread.run_sync(lambda: extract_evidence(bundle, crawl_depth=crawl_depth))
    return evidence, None
