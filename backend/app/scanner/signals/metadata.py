"""Metadata signal: title, description, canonical, robots meta, OpenGraph, Twitter."""
from __future__ import annotations

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "metadata", "Metadata", 12


def analyze(ctx: SignalContext) -> SignalResult:
    soup = ctx.soup
    issues: list = []
    recs: list = []
    score = 0.0

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if title and 10 <= len(title) <= 65:
        score += 25
    elif title:
        score += 15
        issues.append(f"Title length {len(title)} is outside the ideal 10-65 characters.")
        recs.append("Tighten the <title> to 10-65 characters.")
    else:
        issues.append("No <title> tag.")
        recs.append("Add a descriptive <title> tag.")

    desc = soup.find("meta", attrs={"name": "description"})
    desc_val = (desc.get("content") or "").strip() if desc else ""
    if desc_val:
        score += 20
    else:
        issues.append("No meta description.")
        recs.append("Add a meta description summarizing the page.")

    canonical = soup.find("link", attrs={"rel": "canonical"})
    canonical_href = (canonical.get("href") or "").strip() if canonical else ""
    if canonical_href:
        score += 15
    else:
        issues.append("No canonical URL.")
        recs.append("Add a <link rel=\"canonical\"> to avoid duplicate-content ambiguity.")

    robots_meta = soup.find("meta", attrs={"name": "robots"})
    robots_val = (robots_meta.get("content") or "").lower() if robots_meta else ""
    if "noindex" in robots_val:
        issues.append("Page sets meta robots 'noindex' — it asks engines not to index it.")
        recs.append("Remove 'noindex' if this page should be discoverable.")
    else:
        score += 10

    og = {m.get("property"): m.get("content") for m in soup.find_all("meta", attrs={"property": True})
          if str(m.get("property", "")).startswith("og:")}
    if {"og:title", "og:description"} & set(og):
        score += 15
    else:
        issues.append("Missing OpenGraph tags (og:title, og:description).")
        recs.append("Add OpenGraph tags for rich sharing and AI context.")

    tw = [m for m in soup.find_all("meta", attrs={"name": True})
          if str(m.get("name", "")).startswith("twitter:")]
    if tw:
        score += 15
    else:
        issues.append("No Twitter Card tags.")
        recs.append("Add twitter:card metadata.")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "title": title[:120], "title_length": len(title),
            "has_description": bool(desc_val), "has_canonical": bool(canonical_href),
            "robots_meta": robots_val, "open_graph_tags": len(og), "twitter_tags": len(tw),
            # Technical SEO & Indexability (additive; no score impact): the raw
            # canonical href and the X-Robots-Tag response header, already available
            # here (this signal already parses <link rel=canonical> and has the
            # response headers via `ctx`) but not previously surfaced as evidence.
            "canonical": canonical_href or None,
            "x_robots_tag": ctx.header("x-robots-tag"),
            # Content Cannibalization & Duplicate Content Intelligence (additive; no
            # score impact): the raw meta-description TEXT, already parsed above
            # (only `has_description` was previously surfaced) — needed to detect a
            # duplicate meta description across pages.
            "description": desc_val[:300] or None,
        },
    )
