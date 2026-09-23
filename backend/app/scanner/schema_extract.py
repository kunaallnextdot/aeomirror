"""JSON-LD entity extraction — robust to the many shapes real sites emit.

Websites structure schema very differently (WordPress / Yoast / AIOSEO / RankMath
all wrap everything in a top-level ``@graph`` with no top-level ``@type``; medical
sites use LocalBusiness subtypes like ``Dentist``; some pages ship several
``<script type="application/ld+json">`` blocks). This module flattens ALL of that
into a flat list of typed nodes and answers "what entities are actually here?" so the
schema signal reports real data instead of false negatives.

Pure functions, no I/O. The schema signal composes these; nothing here scores.
"""
from __future__ import annotations

# LocalBusiness subtypes that count as a business entity (schema.org). The medical
# types matter most for this product's customers (doctors / clinics).
LOCAL_BUSINESS_SUBTYPES = {
    "LocalBusiness",
    "MedicalBusiness", "MedicalOrganization", "MedicalClinic", "Physician",
    "Dentist", "Hospital", "Pharmacy", "Optician", "VeterinaryCare",
    "Dermatology", "MedicalTherapy",
    # common non-medical LocalBusiness subtypes seen in the wild
    "ProfessionalService", "HealthAndBeautyBusiness", "Store", "Dentistry",
}

# Organization + its subtypes (a brand entity even without LocalBusiness).
ORGANIZATION_TYPES = {
    "Organization", "Corporation", "NGO", "GovernmentOrganization",
    "EducationalOrganization", "MedicalOrganization", "NewsMediaOrganization",
    "OnlineBusiness",
}

# Non-entity content types we still detect + surface ("Found: Article, WebSite …").
CONTENT_TYPES = {
    "Article": {"Article", "BlogPosting", "NewsArticle", "TechArticle", "Report"},
    "FAQPage": {"FAQPage"},
    "Product": {"Product"},
    "BreadcrumbList": {"BreadcrumbList"},
    "WebSite": {"WebSite"},
    "Service": {"Service"},
    "Review": {"Review", "AggregateRating"},
}

# Marker the fetch/parse layer uses for a block that failed JSON.parse.
_PARSE_ERROR = "__parse_error__"


def types_of(node) -> list[str]:
    """Always a list. Handles ``@type`` as a string OR an array; [] when absent."""
    if not isinstance(node, dict):
        return []
    t = node.get("@type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return []


def collect_nodes(json_obj) -> list[dict]:
    """Recursively flatten ``@graph``, arrays, and nested object values into a flat
    list of every object node reachable from the input — so a typed entity nested
    anywhere (``@graph``, ``publisher``, ``mainEntity``, ``itemListElement`` …) is
    found. Cyclic references are guarded by object identity."""
    out: list[dict] = []
    seen: set[int] = set()

    def visit(val):
        if isinstance(val, list):
            for v in val:
                visit(v)
            return
        if isinstance(val, dict):
            if id(val) in seen:
                return
            seen.add(id(val))
            if not val.get(_PARSE_ERROR):     # never treat a parse-error sentinel as a node
                out.append(val)
            graph = val.get("@graph")
            if isinstance(graph, list):
                for v in graph:
                    visit(v)
            for k, v in val.items():
                if k == "@graph":
                    continue
                if isinstance(v, (dict, list)):
                    visit(v)

    visit(json_obj)
    return out


def _nodes_have_any(nodes: list[dict], typeset: set[str]) -> bool:
    return any(t in typeset for n in nodes for t in types_of(n))


def has_entity_schema(nodes: list[dict]) -> bool:
    """True if any node is an Organization OR a LocalBusiness (incl. medical subtypes)."""
    return (_nodes_have_any(nodes, ORGANIZATION_TYPES)
            or _nodes_have_any(nodes, LOCAL_BUSINESS_SUBTYPES))


def all_types(nodes: list[dict]) -> list[str]:
    """Every distinct ``@type`` present across the flattened nodes (sorted)."""
    return sorted({t for n in nodes for t in types_of(n)})


def entity_types(nodes: list[dict]) -> list[str]:
    """The Organization/LocalBusiness types actually present (for 'Found: …' copy)."""
    wanted = ORGANIZATION_TYPES | LOCAL_BUSINESS_SUBTYPES
    return sorted({t for n in nodes for t in types_of(n) if t in wanted})


def detect_content_types(nodes: list[dict]) -> dict[str, bool]:
    """Which named content types were detected (Article, FAQPage, …)."""
    return {name: _nodes_have_any(nodes, tset) for name, tset in CONTENT_TYPES.items()}


# ------------------------- Phase 4: entity + FAQ evidence (additive) -------------------------
# Pulled from the SAME already-parsed nodes, purely additive to the signal's evidence —
# no scoring change. Used by Schema/Entity Intelligence and Question Mining, never by
# the schema signal's own score.
def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def entity_evidence(nodes: list[dict]) -> dict:
    """Real Organization/LocalBusiness field values actually present in the page's own
    JSON-LD (name/url/logo/sameAs) — never invented. `sameAs` is deduped and capped;
    an empty list here means "no sameAs relationship detected", not an error."""
    wanted = ORGANIZATION_TYPES | LOCAL_BUSINESS_SUBTYPES
    name = url = logo = None
    same_as: list[str] = []
    seen = set()
    for n in nodes:
        if not any(t in wanted for t in types_of(n)):
            continue
        name = name or _str(n.get("name"))
        url = url or _str(n.get("url"))
        logo_v = n.get("logo")
        if isinstance(logo_v, dict):
            logo_v = logo_v.get("url")
        logo = logo or _str(logo_v)
        sa = n.get("sameAs")
        for s in (sa if isinstance(sa, list) else [sa] if sa else []):
            s = _str(s)
            if s and s not in seen:
                seen.add(s)
                same_as.append(s)
    return {"name": name, "url": url, "logo": logo, "same_as": same_as[:20]}


def faq_questions(nodes: list[dict], *, limit: int = 20) -> list[dict]:
    """Real {question, answer} pairs from FAQPage `mainEntity` Question nodes — the
    text is copied verbatim from the page's own schema, never generated. `answer` is
    None when the node has no acceptedAnswer.text (kept, not dropped, so the question
    itself is still surfaced)."""
    out: list[dict] = []
    seen = set()
    for n in nodes:
        if "FAQPage" not in types_of(n):
            continue
        main = n.get("mainEntity")
        items = main if isinstance(main, list) else [main] if main else []
        for q in items:
            if not isinstance(q, dict) or "Question" not in types_of(q):
                continue
            text = _str(q.get("name"))
            if not text or text in seen:
                continue
            seen.add(text)
            ans = q.get("acceptedAnswer")
            ans_text = _str(ans.get("text")) if isinstance(ans, dict) else None
            out.append({"question": text, "answer": ans_text})
            if len(out) >= limit:
                return out
    return out


def analyze_jsonld(blocks: list) -> dict:
    """Classify a page's JSON-LD into one of THREE states the report must not collapse:

    - ``absent``    — no ``<script type="application/ld+json">`` blocks at all
    - ``malformed`` — block(s) present but NONE parsed (all failed JSON.parse)
    - ``present``   — parsed nodes (entity may or may not be present)

    ``blocks`` is the raw list from the parse layer: parsed dicts/lists plus a
    ``{"__parse_error__": True}`` sentinel for each block that failed to parse.
    Returns detected types + content flags so callers report ACTUAL data."""
    block_count = len(blocks)
    malformed = sum(1 for b in blocks if isinstance(b, dict) and b.get(_PARSE_ERROR))
    parsed = [b for b in blocks if not (isinstance(b, dict) and b.get(_PARSE_ERROR))]

    if block_count == 0:
        return {"state": "absent", "has_entity": False, "types": [],
                "entity_types": [], "content": {}, "malformed": 0, "blocks": 0,
                "entity_evidence": {"name": None, "url": None, "logo": None, "same_as": []},
                "faq_questions": []}
    if not parsed:
        return {"state": "malformed", "has_entity": False, "types": [],
                "entity_types": [], "content": {}, "malformed": malformed,
                "blocks": block_count,
                "entity_evidence": {"name": None, "url": None, "logo": None, "same_as": []},
                "faq_questions": []}

    nodes = collect_nodes(parsed)
    return {
        "state": "present",
        "has_entity": has_entity_schema(nodes),
        "types": all_types(nodes),
        "entity_types": entity_types(nodes),
        "content": detect_content_types(nodes),
        "malformed": malformed,           # >0 → present but partially malformed
        "blocks": block_count,
        "entity_evidence": entity_evidence(nodes),   # Phase 4: real name/url/logo/sameAs
        "faq_questions": faq_questions(nodes),        # Phase 4: real FAQPage Q&A text
    }
