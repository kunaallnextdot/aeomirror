"""Lightweight, deterministic site-level understanding ("Understand Websites
Holistically"). Built ENTIRELY from data the URL Intelligence Engine already
extracted per page during a completed crawl — no new LLM call, no new fetch, no new
JSON-LD parse. Every inference carries its own confidence/basis so it is never
presented as more certain than the underlying crawl evidence actually supports;
when evidence is thin, confidence is explicitly "insufficient_evidence", never a
guessed label dressed up as a finding.

This is a real, evidence-driven improvement on the ONLY other "business type"
guess anywhere in the app — app/admin/analytics.py's `_classify_industry`, an
internal-admin-only domain-NAME keyword matcher. This instead looks at what the
site's own crawled pages actually are and what their own schema actually says.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from app.db.models import URL_CRAWLED, CrawlJob, CrawlUrl

# Each rule: (predicate over (page_type -> count, total_pages), business_type label).
# Evaluated in order; the first match wins. Thresholds are deliberately conservative
# (a minority share is not enough to claim a site-wide business type).
_BUSINESS_TYPE_RULES: list[tuple] = [
    (lambda c, t: (c.get("product", 0) + c.get("product_category", 0)) / t >= 0.25,
     "E-commerce / online retail"),
    (lambda c, t: (c.get("booking", 0) + c.get("flight_hotel_inventory", 0)) / t >= 0.15,
     "Hospitality / booking"),
    (lambda c, t: c.get("service", 0) / t >= 0.25, "Service business"),
    (lambda c, t: c.get("lead_generation", 0) / t >= 0.2, "Lead-generation / agency"),
    (lambda c, t: c.get("informational", 0) / t >= 0.5, "Content / media / publisher"),
]


def build_site_profile(db: Session, crawl_job: CrawlJob) -> dict:
    """A read-only summary — never triggers a crawl, never calls an LLM. Returns
    {"available": False, ...} when there isn't enough crawled data to say anything,
    rather than fabricating a profile from nothing."""
    rows = (db.query(CrawlUrl)
           .filter(CrawlUrl.crawl_job_id == crawl_job.id, CrawlUrl.status == URL_CRAWLED)
           .all())
    if not rows:
        return {"available": False, "reason": "no_crawled_pages"}

    total = len(rows)
    type_counts: Counter = Counter(r.page_type or "other" for r in rows)

    business_label, business_basis = None, None
    for rule, label in _BUSINESS_TYPE_RULES:
        if rule(type_counts, total):
            dominant_type, dominant_count = max(type_counts.items(), key=lambda kv: kv[1])
            business_label = label
            business_basis = (f"{dominant_count} of {total} crawled pages were "
                              f"classified as '{dominant_type}' pages.")
            break

    # Cross-page entity consistency, reusing each page's own already-extracted
    # schema.entity_evidence (see services/url_intelligence/evidence.py, which in
    # turn reuses app.scanner.schema_extract.entity_evidence) — never a new parse.
    names: Counter = Counter()
    logos: Counter = Counter()
    pages_with_entity = 0
    for r in rows:
        entity_evidence = (((r.evidence or {}).get("schema") or {}).get("entity_evidence") or {})
        has_any = entity_evidence.get("name") or entity_evidence.get("logo")
        if has_any:
            pages_with_entity += 1
        if entity_evidence.get("name"):
            names[entity_evidence["name"]] += 1
        if entity_evidence.get("logo"):
            logos[entity_evidence["logo"]] += 1

    distinct_names = list(names.keys())
    distinct_logos = list(logos.keys())
    # Consistency is only a meaningful claim once at least 2 pages actually carry
    # entity data — with 0 or 1 such pages there is nothing to compare, so the
    # field is explicitly None ("not applicable"), never a fabricated True/False.
    consistent = (len(distinct_names) <= 1 and len(distinct_logos) <= 1) if pages_with_entity >= 2 else None

    return {
        "available": True,
        "pages_analyzed": total,
        "page_type_distribution": dict(type_counts),
        "business_type": {
            "label": business_label or "Not confidently inferable from this crawl",
            "confidence": "medium" if business_label else "insufficient_evidence",
            "basis": business_basis or (
                "No single page-type pattern reached the threshold needed to infer "
                "a business type from this crawl."),
        },
        "entity": {
            "detected_name": names.most_common(1)[0][0] if names else None,
            "pages_with_entity_data": pages_with_entity,
            "consistent": consistent,
            "distinct_names_found": distinct_names,
            "distinct_logos_found": distinct_logos,
            "confidence": ("high" if pages_with_entity >= 5
                          else "low" if pages_with_entity else "insufficient_evidence"),
            "basis": (f"{pages_with_entity} of {total} crawled pages carry "
                     f"Organization/LocalBusiness schema with a name or logo."
                     if pages_with_entity else
                     "No crawled page carries Organization/LocalBusiness schema."),
        },
    }
