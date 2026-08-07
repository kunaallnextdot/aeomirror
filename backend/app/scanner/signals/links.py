"""Internal-linking signal: internal/external mix, navigation, anchor diversity.

Note: true orphan-page detection needs a multi-page crawl (Phase 4). From a single
page we assess link density, navigation presence, and anchor-text diversity, which
are strong proxies for crawlable internal structure.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "links", "Internal Linking", 8

_GENERIC = {"click here", "read more", "here", "learn more", "more", "link", "this"}


def analyze(ctx: SignalContext) -> SignalResult:
    soup = ctx.soup
    host = (urlparse(ctx.url).hostname or "").lower()
    issues: list = []
    recs: list = []

    anchors = soup.find_all("a", href=True)
    internal, external, generic, empty = 0, 0, 0, 0
    anchor_texts: set = set()
    for a in anchors:
        href = a["href"].strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        h = urlparse(href).hostname
        if href.startswith("/") or (h and h.lower() == host) or not h:
            internal += 1
        else:
            external += 1
        text = a.get_text(" ", strip=True).lower()
        if not text:
            empty += 1
        else:
            anchor_texts.add(text)
            if text in _GENERIC:
                generic += 1

    has_nav = bool(soup.find("nav")) or bool(soup.select_one("header a, [role=navigation]"))
    diversity = len(anchor_texts) / max(internal + external, 1)

    score = 0.0
    if internal >= 5:
        score += 40
    elif internal >= 1:
        score += 20
        issues.append(f"Only {internal} internal link(s) — thin internal structure for crawlers.")
        recs.append("Add contextual internal links to related pages.")
    else:
        issues.append("No internal links found — this page looks orphaned/isolated.")
        recs.append("Link to other pages so crawlers can discover your site structure.")

    if has_nav:
        score += 25
    else:
        issues.append("No <nav> navigation detected.")
        recs.append("Add a semantic <nav> for primary navigation.")

    if diversity >= 0.5:
        score += 20
    elif anchors:
        score += 8
        recs.append("Diversify anchor text so links describe their destination.")

    if empty:
        issues.append(f"{empty} link(s) have no anchor text.")
        recs.append("Give every link descriptive anchor text.")
    else:
        score += 15
    if generic:
        recs.append(f"Replace {generic} generic anchor(s) like 'click here' with descriptive text.")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "internal_links": internal, "external_links": external,
            "has_nav": has_nav, "anchor_diversity": round(diversity, 2),
            "generic_anchors": generic, "empty_anchors": empty,
        },
    )
