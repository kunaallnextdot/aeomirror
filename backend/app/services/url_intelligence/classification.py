"""Business-value page-type classification + template fingerprinting (Phase 7 inputs).

Deterministic and evidence-based only — no AI call, no guessing. Page type is inferred
from the URL path and the page's OWN structured-data types (never invented); template
fingerprinting is a coarse structural signature (page type + path shape + schema
types + heading pattern), not true DOM-diff clustering — documented as a heuristic
limitation (see classify_template's docstring), good enough to group "probably the
same template" pages so the AI Expert Analysis Engine can analyze one representative
per cluster instead of every URL (Phase 6).
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

PAGE_PRODUCT = "product"
PAGE_PRODUCT_CATEGORY = "product_category"
PAGE_PRICING = "pricing"
PAGE_COMPARISON = "comparison"
PAGE_BOOKING = "booking"
PAGE_FLIGHT_HOTEL = "flight_hotel_inventory"
PAGE_SERVICE = "service"
PAGE_LEAD_GEN = "lead_generation"
PAGE_INFORMATIONAL = "informational"
PAGE_LEGAL_UTILITY = "legal_utility"
PAGE_OTHER = "other"

# Business-intent weight per page type (Phase 7: "distinguish business opportunity
# from technical severity" — legal/utility pages never get high business priority by
# default, even though a technical/legal issue on them can still be flagged separately
# by the AI engine via its own severity field).
INTENT_WEIGHT = {
    PAGE_PRODUCT: 1.0,
    PAGE_PRICING: 1.0,
    PAGE_BOOKING: 0.95,
    PAGE_FLIGHT_HOTEL: 0.95,
    PAGE_LEAD_GEN: 0.9,
    PAGE_PRODUCT_CATEGORY: 0.8,
    PAGE_COMPARISON: 0.75,
    PAGE_SERVICE: 0.7,
    PAGE_INFORMATIONAL: 0.4,
    PAGE_OTHER: 0.3,
    PAGE_LEGAL_UTILITY: 0.05,
}

_LEGAL_RE = re.compile(r"/(privacy|terms|legal|cookie[s]?|accessibility|disclaimer|404|robots\.txt|sitemap\.xml)([/-]|$|\.)", re.I)
_PRICING_RE = re.compile(r"/(pricing|plans?|price)(/|$)", re.I)
_COMPARISON_RE = re.compile(r"(^|/)(vs|versus|compare|comparison)([-/]|$)", re.I)
_FLIGHT_HOTEL_RE = re.compile(r"/(flights?|hotels?|rooms?|stays?|resorts?)(/|$)", re.I)
_BOOKING_RE = re.compile(r"/(book(ing)?s?|reservations?|appointments?|schedule)(/|$)", re.I)
_LEAD_GEN_RE = re.compile(r"/(contact(-us)?|quote|demo|signup|sign-up|get-started|free-trial|request-a-quote)(/|$)", re.I)
_PRODUCT_RE = re.compile(r"/(products?|item[s]?|p)/[^/]+/?$", re.I)
_CATEGORY_RE = re.compile(r"/(category|categories|collections?|shop)(/|$)", re.I)
_INFO_RE = re.compile(r"/(blog|news|articles?|guides?|resources?|insights?)(/|$)", re.I)


def classify_page_type(url: str, evidence: dict) -> str:
    """Infer a business page type from the URL path and the page's own structured
    data — in priority order from most to least specific, so e.g. a legal page under
    /blog/privacy-policy is still correctly classified legal_utility first."""
    path = (urlparse(url).path or "/").lower()
    title = (evidence.get("title") or "").lower()
    schema_content = (evidence.get("schema") or {}).get("content") or {}
    schema_types = set((evidence.get("schema") or {}).get("types") or [])

    if _LEGAL_RE.search(path):
        return PAGE_LEGAL_UTILITY
    if _PRICING_RE.search(path) or "pricing" in title:
        return PAGE_PRICING
    if _COMPARISON_RE.search(path) or _COMPARISON_RE.search(title):
        return PAGE_COMPARISON
    if _FLIGHT_HOTEL_RE.search(path) or any(w in title for w in ("flight", "hotel")):
        return PAGE_FLIGHT_HOTEL
    if _BOOKING_RE.search(path) or "book now" in title:
        return PAGE_BOOKING
    if _LEAD_GEN_RE.search(path):
        return PAGE_LEAD_GEN
    if "Product" in schema_types or _PRODUCT_RE.search(path):
        return PAGE_PRODUCT
    if {"CollectionPage", "ItemList"} & schema_types or _CATEGORY_RE.search(path):
        return PAGE_PRODUCT_CATEGORY
    if "/service" in path:
        return PAGE_SERVICE
    if schema_content.get("Article") or _INFO_RE.search(path):
        return PAGE_INFORMATIONAL
    return PAGE_OTHER


def classify_template(url: str, page_type: str, evidence: dict) -> str:
    """A coarse structural fingerprint: pages that share it are treated as "probably
    the same template" for Phase 6 representative-template AI analysis and for
    grouping template-level findings in the Action Center. This is a HEURISTIC
    (page type + URL path shape + present schema types + heading pattern), not a true
    DOM-structure diff — two visually/structurally different templates that happen to
    share these signals could collide into one cluster. Documented as a known
    limitation in the final report, not hidden."""
    path = urlparse(url).path or "/"
    segment_count = len([s for s in path.split("/") if s])
    schema_types = ",".join(sorted((evidence.get("schema") or {}).get("types") or []))
    heading_shape = "h1" if (evidence.get("heading_counts") or {}).get("h1") else "noh1"
    basis = f"{page_type}|{segment_count}|{schema_types}|{heading_shape}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def intent_weight(page_type: str) -> float:
    return INTENT_WEIGHT.get(page_type, INTENT_WEIGHT[PAGE_OTHER])
