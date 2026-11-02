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

PAGE_HOMEPAGE = "homepage"
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
    PAGE_HOMEPAGE: 0.9,
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

    if path in ("", "/") or re.fullmatch(r"/(index|home)(\.html?|\.php)?", path):
        return PAGE_HOMEPAGE   # the site root: identified by path alone, not by content
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


# ---------------------------------------------------------------------------
# Business page types (Page Opportunities). A layer over classify_page_type, not a
# second classifier: the engine type above stays the crawl's stored, template-defining
# label, and this maps it — plus login/account and legal-vs-utility splits the engine
# doesn't make — onto the business vocabulary, with a confidence, the evidence behind
# it, and a status. Deterministic: URL path, title, H1, breadcrumbs, the page's own
# schema.org types and product offer data. No AI call.

BP_PRODUCT = "product"
BP_CATEGORY = "category"
BP_SERVICE = "service"
BP_LANDING = "landing"
BP_HOTEL = "hotel_property"
BP_BOOKING = "booking"
BP_BLOG = "blog"
BP_HOMEPAGE = "homepage"
BP_UTILITY = "utility"
BP_LEGAL = "legal"
BP_ACCOUNT = "login_account"
BP_UNKNOWN = "unknown"
BUSINESS_PAGE_TYPES = (BP_PRODUCT, BP_CATEGORY, BP_SERVICE, BP_LANDING, BP_HOTEL, BP_BOOKING,
                       BP_BLOG, BP_HOMEPAGE, BP_UTILITY, BP_LEGAL, BP_ACCOUNT, BP_UNKNOWN)
# Page types that can directly produce a sale, lead or reservation.
COMMERCIAL_TYPES = frozenset({BP_PRODUCT, BP_CATEGORY, BP_SERVICE, BP_LANDING, BP_HOTEL, BP_BOOKING,
                              BP_HOMEPAGE})
LOW_PRIORITY_TYPES = frozenset({BP_UTILITY, BP_LEGAL, BP_ACCOUNT})

STATUS_CONFIRMED = "confirmed"   # independent signals agree
STATUS_LIKELY = "likely"         # one strong signal
STATUS_UNCERTAIN = "uncertain"   # weak or conflicting signals — never shown as fact

_ACCOUNT_RE = re.compile(r"/(log-?in|sign-?in|log-?out|sign-?out|account|my-account|profile|register|"
                         r"password|forgot-password|reset-password|wp-login\.php|auth)([/?.-]|$)", re.I)
_UTILITY_RE = re.compile(r"/(cart|basket|checkout|search|404|sitemap(\.xml)?|robots\.txt|feed|rss|tag|tags|"
                         r"author|wp-admin|wp-json|unsubscribe|thank-?you|thanks|order-confirmation)([/?.-]|$)", re.I)
_LEGAL_ONLY_RE = re.compile(r"/(privacy|privacy-policy|terms|terms-of-service|terms-and-conditions|tos|legal|"
                            r"cookie[s]?|cookie-policy|accessibility|disclaimer|gdpr|imprint|impressum|"
                            r"refund-policy|return-policy|shipping-policy)([/.-]|$)", re.I)
_LANDING_RE = re.compile(r"/(lp|landing|offers?|promo|campaign|get-started|free-trial|demo|quote|"
                         r"request-a-quote|contact(-us)?|pricing|plans?)([/-]|$)", re.I)
_SERVICE_RE = re.compile(r"/(services?|solutions?|what-we-do|treatments?|practice-areas?)([/-]|$)", re.I)
_HOTEL_RE = re.compile(r"/(hotels?|rooms?|suites?|stays?|resorts?|villas?|properties|property|accommodations?|"
                       r"rentals?|lodging)([/-]|$)", re.I)

# The page's own schema.org @type values that point to a business type.
_SCHEMA_SIGNALS = {
    BP_PRODUCT: {"Product", "IndividualProduct", "ProductGroup", "Offer", "AggregateOffer"},
    BP_CATEGORY: {"CollectionPage", "ItemList", "OfferCatalog"},
    BP_SERVICE: {"Service", "ProfessionalService", "LegalService", "MedicalBusiness", "HomeAndConstructionBusiness"},
    BP_HOTEL: {"Hotel", "LodgingBusiness", "Resort", "BedAndBreakfast", "Motel", "Hostel",
               "VacationRental", "HotelRoom", "Suite", "Accommodation", "Apartment"},
    BP_BOOKING: {"ReserveAction", "Reservation", "LodgingReservation", "FoodEstablishmentReservation"},
    BP_BLOG: {"Article", "BlogPosting", "NewsArticle", "TechArticle", "Blog"},
}
_TEXT_SIGNALS = {
    BP_PRODUCT: ("add to cart", "add to basket", "buy now", "in stock"),
    BP_BOOKING: ("book now", "reserve", "reservation", "book your", "check availability", "booking"),
    BP_HOTEL: ("hotel", "resort", "suite", "room", "villa", "stay at"),
    BP_SERVICE: ("services", "our service", "consultation"),
    BP_BLOG: ("blog", "article", "guide", "how to"),
    BP_LEGAL: ("privacy policy", "terms of service", "terms and conditions", "cookie policy"),
    BP_ACCOUNT: ("log in", "login", "sign in", "my account", "create account", "register"),
    BP_CATEGORY: ("collection", "category", "shop all"),
}
_ENGINE_TO_BUSINESS = {
    PAGE_HOMEPAGE: BP_HOMEPAGE, PAGE_PRODUCT: BP_PRODUCT, PAGE_PRODUCT_CATEGORY: BP_CATEGORY,
    PAGE_PRICING: BP_LANDING, PAGE_COMPARISON: BP_LANDING, PAGE_LEAD_GEN: BP_LANDING,
    PAGE_BOOKING: BP_BOOKING, PAGE_FLIGHT_HOTEL: BP_HOTEL, PAGE_SERVICE: BP_SERVICE,
    PAGE_INFORMATIONAL: BP_BLOG, PAGE_OTHER: BP_UNKNOWN,
}
# Weight of each independent signal family toward confidence (sums above 1 are capped).
_W_PATH, _W_SCHEMA, _W_TEXT, _W_COMMERCE, _W_BREADCRUMB = 0.45, 0.45, 0.2, 0.3, 0.1


def _path_type(path: str, engine_type: str | None) -> tuple[str | None, str | None]:
    """(business type, the path rule that matched) from the URL alone."""
    if _ACCOUNT_RE.search(path):
        return BP_ACCOUNT, "login/account path"
    if _LEGAL_ONLY_RE.search(path):
        return BP_LEGAL, "legal/policy path"
    if _UTILITY_RE.search(path):
        return BP_UTILITY, "utility path (cart, search, feed, tag…)"
    if _BOOKING_RE.search(path):
        return BP_BOOKING, "booking/reservation path"
    if _HOTEL_RE.search(path):
        return BP_HOTEL, "hotel/property path"
    if _PRODUCT_RE.search(path):
        return BP_PRODUCT, "product path"
    if _CATEGORY_RE.search(path):
        return BP_CATEGORY, "category/collection path"
    if _SERVICE_RE.search(path):
        return BP_SERVICE, "service path"
    if _LANDING_RE.search(path) or _PRICING_RE.search(path) or _COMPARISON_RE.search(path):
        return BP_LANDING, "landing/conversion path (pricing, demo, quote, contact…)"
    if _INFO_RE.search(path):
        return BP_BLOG, "blog/editorial path"
    if engine_type == PAGE_LEGAL_UTILITY:
        return BP_UTILITY, "utility path"
    return None, None


def classify_business_page(url: str, evidence: dict | None, engine_page_type: str | None = None) -> dict:
    """{"page_type", "confidence" (0–1), "status", "evidence": [str]} for one URL.

    Confidence adds up the independent signal families that agree with the chosen
    type (URL path, the page's own schema types, title/H1 wording, product offer data,
    breadcrumbs) and is reduced when another family points elsewhere. "confirmed"
    needs two agreeing families (or the site root); one strong family is "likely";
    anything weaker — or a page with no crawled evidence — is "uncertain"."""
    path = (urlparse(url).path or "/").lower()
    ev = evidence or {}
    if path in ("", "/") or re.fullmatch(r"/(index|home)(\.html?|\.php)?", path):
        return {"page_type": BP_HOMEPAGE, "confidence": 0.99, "status": STATUS_CONFIRMED,
                "evidence": ["The URL is the site root."]}

    path_type, path_rule = _path_type(path, engine_page_type)
    schema_types = set((ev.get("schema") or {}).get("types") or [])
    text = " ".join([ev.get("title") or "", *(ev.get("h1_texts") or [])]).lower()
    crumbs = " ".join(ev.get("breadcrumbs") or []).lower()
    product = ev.get("product") or {}

    votes: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    def vote(t: str, w: float, why: str) -> None:
        votes[t] = votes.get(t, 0.0) + w
        reasons.setdefault(t, []).append(why)

    if path_type:
        vote(path_type, _W_PATH, f"URL matches a {path_rule}.")
    for t, types in _SCHEMA_SIGNALS.items():
        hit = sorted(schema_types & types)
        if hit:
            vote(t, _W_SCHEMA, f"The page's structured data declares {', '.join(hit)}.")
    for t, words in _TEXT_SIGNALS.items():
        hit = next((w for w in words if w in text), None)
        if hit:
            vote(t, _W_TEXT, f"Title/H1 mentions \"{hit}\".")
    if product.get("price"):
        vote(BP_PRODUCT, _W_COMMERCE, f"Product offer data with a price ({product['price']}"
                                       f"{' ' + product['price_currency'] if product.get('price_currency') else ''}).")
    if crumbs and len(ev.get("breadcrumbs") or []) >= 2 and path_type in (BP_PRODUCT, BP_CATEGORY, BP_SERVICE, BP_HOTEL):
        vote(path_type, _W_BREADCRUMB, "Breadcrumb trail places it below a section page.")

    if not votes:
        engine = _ENGINE_TO_BUSINESS.get(engine_page_type or PAGE_OTHER, BP_UNKNOWN)
        if engine != BP_UNKNOWN:
            return {"page_type": engine, "confidence": 0.3, "status": STATUS_UNCERTAIN,
                    "evidence": [f"Only the crawl's page-type rules suggest {engine.replace('_', ' ')}."]}
        why = ("No classifying signal in the URL, title, headings or structured data."
               if evidence else "The page hasn't been crawled, so only its URL could be checked.")
        return {"page_type": BP_UNKNOWN, "confidence": 0.0, "status": STATUS_UNCERTAIN, "evidence": [why]}

    # Legal/account/utility pages are decided by their path: a privacy policy that
    # mentions "sign in" is still a privacy policy.
    if path_type in LOW_PRIORITY_TYPES:
        best = path_type
    else:
        best = max(votes, key=lambda t: (votes[t], t == path_type))
    support = votes[best]
    conflict = sum(w for t, w in votes.items() if t != best)
    confidence = round(max(0.05, min(0.98, support - 0.5 * conflict)), 2)
    families = len(reasons[best])
    if families >= 2 and confidence >= 0.7:
        status = STATUS_CONFIRMED
    elif confidence >= 0.4:
        status = STATUS_LIKELY
    else:
        status = STATUS_UNCERTAIN
    out = list(reasons[best])
    for t, w in sorted(votes.items(), key=lambda kv: -kv[1]):
        if t != best:
            out.append(f"Conflicting signal: {reasons[t][0].rstrip('.')} (suggests {t.replace('_', ' ')}).")
    return {"page_type": best, "confidence": confidence, "status": status, "evidence": out[:6]}
