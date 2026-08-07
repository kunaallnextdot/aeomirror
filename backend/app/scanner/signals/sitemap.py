"""XML sitemap signal: exists, valid, structure, important URLs."""
from __future__ import annotations

import re

from app.scanner.signals.base import SignalContext, SignalResult

ID, LABEL, WEIGHT = "sitemap", "XML Sitemap", 7

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def analyze(ctx: SignalContext) -> SignalResult:
    xml = ctx.sitemap_xml
    referenced = "sitemap" in ctx.robots_txt.lower()
    issues: list = []
    recs: list = []

    if not xml:
        if referenced:
            return SignalResult.build(
                ID, LABEL, WEIGHT, 45,
                issues=["robots.txt references a sitemap, but sitemap.xml could not be fetched at the site root."],
                recommendations=["Ensure the sitemap URL is reachable and returns valid XML."],
                evidence={"present": False, "referenced_in_robots": True},
            )
        return SignalResult.build(
            ID, LABEL, WEIGHT, 30,
            issues=["No sitemap.xml found. AI crawlers rely on sitemaps to discover URLs."],
            recommendations=["Publish a sitemap.xml and reference it from robots.txt."],
            evidence={"present": False, "referenced_in_robots": False},
        )

    is_index = "<sitemapindex" in xml.lower()
    looks_xml = xml.lstrip().startswith("<") and ("<urlset" in xml.lower() or is_index)
    urls = _LOC_RE.findall(xml)
    url_count = len(urls)

    score = 100.0
    if not looks_xml:
        score -= 40
        issues.append("sitemap.xml does not look like a valid <urlset>/<sitemapindex> document.")
        recs.append("Serve a well-formed XML sitemap.")
    if url_count == 0 and not is_index:
        score -= 30
        issues.append("Sitemap contains no <loc> URLs.")
        recs.append("Include your important URLs in the sitemap.")
    if not referenced:
        score -= 10
        recs.append("Reference the sitemap from robots.txt (Sitemap: ...).")

    return SignalResult.build(
        ID, LABEL, WEIGHT, score, issues=issues, recommendations=recs,
        evidence={
            "present": True,
            "is_index": is_index,
            "valid_xml": looks_xml,
            "url_count": url_count,
            "sample_urls": urls[:10],
            "referenced_in_robots": referenced,
        },
    )
