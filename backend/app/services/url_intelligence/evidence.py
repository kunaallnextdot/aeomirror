"""Deterministic evidence extraction (Phase 4) for one crawled page.

Reuses the SAME parsing the single-page scanner already does — `SignalContext` (HTML
parsed once into BeautifulSoup/JSON-LD) and `schema_extract` (JSON-LD entity/content
flattening) — rather than re-implementing HTML parsing. Nothing here calls the AI
model or scores anything; it only extracts facts that are actually present on the
page, so the AI Expert Analysis Engine (ai_analysis.py) has real evidence to reason
over instead of the raw HTML.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse

from app.scanner.models import PageBundle
from app.scanner.schema_extract import analyze_jsonld, collect_nodes, types_of
from app.scanner.signals.base import SignalContext

_WS_RE = re.compile(r"\s+")

# schema.org types that carry commerce attributes worth surfacing as "product evidence".
_PRODUCT_TYPES = {"Product", "IndividualProduct", "ProductGroup"}
_OFFER_TYPES = {"Offer", "AggregateOffer"}
_RATING_TYPES = {"AggregateRating", "Rating"}


def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _product_evidence(nodes: list[dict]) -> dict | None:
    """Real price/availability/rating fields actually present in the page's own
    Product/Offer/AggregateRating JSON-LD — never invented, never estimated."""
    product = next((n for n in nodes if any(t in _PRODUCT_TYPES for t in types_of(n))), None)
    if not product:
        return None
    offers = product.get("offers")
    offer = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else None
    if offer is None:
        offer = next((n for n in nodes if any(t in _OFFER_TYPES for t in types_of(n))), None)
    rating = product.get("aggregateRating")
    if not isinstance(rating, dict):
        rating = next((n for n in nodes if any(t in _RATING_TYPES for t in types_of(n))), None)
    return {
        "name": _str(product.get("name")),
        "price": _str(str(offer.get("price"))) if isinstance(offer, dict) and offer.get("price") is not None else None,
        "price_currency": _str(offer.get("priceCurrency")) if isinstance(offer, dict) else None,
        "availability": _str(offer.get("availability")) if isinstance(offer, dict) else None,
        "rating_value": _str(str(rating.get("ratingValue"))) if isinstance(rating, dict) and rating.get("ratingValue") is not None else None,
        "review_count": _str(str(rating.get("reviewCount"))) if isinstance(rating, dict) and rating.get("reviewCount") is not None else None,
    }


def _breadcrumbs(soup) -> list[str]:
    nav = soup.select_one('[aria-label*="readcrumb" i], nav.breadcrumb, nav.breadcrumbs, .breadcrumb, .breadcrumbs')
    if not nav:
        return []
    return [a.get_text(" ", strip=True) for a in nav.find_all("a") if a.get_text(strip=True)][:10]


def extract_evidence(page: PageBundle, *, crawl_depth: int = 0,
                     ctx: SignalContext | None = None) -> dict:
    """Build the full deterministic evidence dict for a fetched page. Pure function —
    no I/O, no AI, never raises (a parse failure degrades a field to None/empty rather
    than aborting the whole extraction). Pass `ctx` to share its parsed HTML with the
    caller (the crawler reuses it for link discovery instead of parsing again)."""
    ctx = ctx or SignalContext(page)
    soup = ctx.soup

    title_tag = soup.find("title")
    title = _WS_RE.sub(" ", title_tag.get_text(strip=True)) if title_tag else None

    meta_desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    meta_description = _str(meta_desc_tag.get("content")) if meta_desc_tag else None

    canonical_tag = soup.find("link", rel=re.compile("^canonical$", re.I))
    canonical_url = urljoin(page.url, canonical_tag["href"]) if canonical_tag and canonical_tag.get("href") else None

    robots_meta_tag = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    robots_meta = _str(robots_meta_tag.get("content")) if robots_meta_tag else None
    noindex = bool(robots_meta and "noindex" in robots_meta.lower())

    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    heading_counts = {f"h{n}": len(soup.find_all(f"h{n}")) for n in range(1, 7)}

    word_count = len(ctx.text.split())

    host = (urlparse(page.url).hostname or "").lower()
    internal_links, external_links = 0, 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:")):
            continue
        h = urlparse(href).hostname
        if href.startswith("/") or (h and h.lower() == host) or not h:
            internal_links += 1
        else:
            external_links += 1

    jsonld = analyze_jsonld(ctx.jsonld)
    nodes = collect_nodes(ctx.jsonld)
    product = _product_evidence(nodes)
    breadcrumbs = _breadcrumbs(soup)

    missing: list[str] = []
    if not title:
        missing.append("missing_title")
    elif len(title) > 70:
        missing.append("title_too_long")
    if not meta_description:
        missing.append("missing_meta_description")
    if not h1s:
        missing.append("missing_h1")
    elif len(h1s) > 1:
        missing.append("multiple_h1")
    if word_count < 150:
        missing.append("thin_content")
    if jsonld["state"] != "present":
        missing.append("no_structured_data")
    if not canonical_url:
        missing.append("missing_canonical")

    return {
        "http_status": page.status_code,
        "final_url": page.final_url or page.url,
        "redirected": bool(page.redirect_chain),
        "title": title,
        "title_length": len(title) if title else 0,
        "meta_description": meta_description,
        "meta_description_length": len(meta_description) if meta_description else 0,
        "canonical_url": canonical_url,
        "robots_meta": robots_meta,
        "noindex": noindex,
        "h1_texts": h1s[:5],
        "heading_counts": heading_counts,
        "word_count": word_count,
        "internal_link_count": internal_links,
        "external_link_count": external_links,
        "breadcrumbs": breadcrumbs,
        "schema": jsonld,
        "product": product,
        "sitemap_present": page.sitemap_present,
        "llms_txt_present": page.llms_txt_present,
        "crawl_depth": crawl_depth,
        "missing_content_elements": missing,
    }


def content_fingerprint(evidence: dict) -> str:
    """A stable hash of the evidence fields that matter for 'has this page materially
    changed' (title/description/headings/word_count bucket/schema types/missing
    elements) — used to skip re-analysis (Phase 6 cache) and to detect drift for
    verification/regression checks. Deliberately excludes volatile fields like
    http_status/redirected so a transient 200->200 re-fetch with identical content
    always fingerprints identically."""
    bucket = (evidence.get("word_count") or 0) // 50   # coarse bucket, ignores tiny edits
    basis = "|".join([
        str(evidence.get("title") or ""),
        str(evidence.get("meta_description") or ""),
        ",".join(evidence.get("h1_texts") or []),
        str(sorted((evidence.get("heading_counts") or {}).items())),
        str(bucket),
        ",".join(sorted((evidence.get("schema") or {}).get("types") or [])),
        ",".join(sorted(evidence.get("missing_content_elements") or [])),
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
