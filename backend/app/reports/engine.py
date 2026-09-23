"""Recommendation engine + website scorecard + report builder (Phase 6).

Pure functions over a stored scan's signal `sections`. No external calls, no LLM —
deterministic, rule-based output. `build_report(scan_dict)` returns the canonical
report structure consumed by the JSON/CSV/PDF exporters and the dashboard.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.reports.content_intelligence import build_content_intelligence_block
from app.reports.crawl_graph import build_crawl_graph_block
from app.reports.insights import build_insights_block
from app.reports.phase4 import build_phase4_block
from app.reports.technical_seo import build_technical_seo_block
from app.reports.templates import CATEGORIES, CATEGORY, template_for

# Bumped 1.4.0 -> 1.5.0: Google Search Console integration (and its
# report["gsc_intelligence"] block) was removed. A stored `reports` row from before
# this change still has that key; get_or_build_report()'s cache-validity check
# (`row.version == REPORT_VERSION`) uses exactly this constant, so bumping it makes a
# pre-existing cached row rebuild once (dropping the stale key) instead of serving it
# forever.
REPORT_VERSION = "1.5.0"

# Bands shared with the scanner (score -> status).
_GOOD, _WARN = 75, 45


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 55: return "D"
    return "F"


def _status(score: float) -> str:
    if score >= _GOOD: return "pass"
    if score >= _WARN: return "warn"
    return "fail"


def _severity(score: float) -> str:
    """How bad the current state of this signal is."""
    if score < 25: return "Critical"
    if score < 45: return "High"
    if score < 60: return "Medium"
    return "Low"


def _priority(score: float, weight: float) -> tuple[str, float]:
    """Actionability = impact (weight) × how far from healthy (100 - score).
    Returns (level, numeric_score) where numeric_score sorts Top-10 priorities."""
    numeric = weight * (100 - score) / 100.0   # 0 .. ~15
    if numeric >= 8: level = "Critical"
    elif numeric >= 5: level = "High"
    elif numeric >= 2.5: level = "Medium"
    else: level = "Low"
    return level, round(numeric, 2)


def _brand_from_domain(domain: str) -> str:
    """A best-effort brand label derived from the AUDITED domain (never a hardcoded
    name). e.g. 'www.smile-dental.com' -> 'Smile Dental'."""
    host = (domain or "").split("//")[-1].split("/")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    label = host.split(".")[0] if host else ""
    return label.replace("-", " ").strip().title() or "Your Brand"


def _localize_example(text: str, domain: str) -> str:
    """Interpolate the audited domain + brand into a template implementation example,
    replacing the generic 'AEOMirror' / 'example.com' placeholders so the copy reflects
    the ACTUAL site being audited."""
    if not text or not domain:
        return text
    host = (domain or "").split("//")[-1].split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    brand = _brand_from_domain(domain)
    return (text.replace("https://example.com", f"https://{host}")
                .replace("example.com", host)
                .replace("AEOMirror", brand))


def _section_recommendation(section: dict, domain: str = "") -> dict | None:
    """Build one rich recommendation from a signal section, or None if the signal
    is healthy with nothing to improve (it becomes a strength instead)."""
    score = section.get("score", 0)
    status = section.get("status") or _status(score)
    issues = list(section.get("issues") or [])
    # Healthy AND clean -> not a recommendation.
    if status == "pass" and not issues:
        return None

    sid = section.get("id", "")
    weight = section.get("weight", 0) or 0
    tpl = template_for(sid)
    label = section.get("label") or tpl["title"]
    level, pscore = _priority(score, weight)
    severity = _severity(score)

    signal_recs = list(section.get("recommendations") or [])
    problem = issues[0] if issues else f"{label} needs improvement to be fully AI-visible."
    description = (
        " ".join(issues) if issues
        else f"{label} scored {score}/100 and has room to improve."
    )
    recommended_fix = signal_recs or [tpl["outcome"]]

    return {
        "id": sid,
        "issue_title": tpl["title"],
        "signal_label": label,
        "category": CATEGORY.get(sid, "AI Extractability"),
        "severity": severity,
        "priority": level,
        "priority_score": pscore,
        "score": score,
        "weight": weight,
        "status": status,
        "description": description,
        "evidence": {
            "signal_score": score,
            "status": status,
            "issues": issues,
            "findings": section.get("evidence") or {},
        },
        "business_impact": tpl["business_impact"],
        "ai_visibility_impact": tpl["ai_impact"],
        "estimated_fix_time": tpl["fix_time"],
        "difficulty": tpl["difficulty"],
        # Fix template (spec: Problem / Explanation / Recommended Fix /
        # Implementation Example / Expected Outcome).
        "fix_template": {
            "problem": problem,
            "explanation": tpl["explanation"],
            "recommended_fix": recommended_fix,
            "implementation_example": _localize_example(tpl["example"], domain),
            "expected_outcome": tpl["outcome"],
        },
    }


_PRIORITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def build_recommendations(sections: list[dict], domain: str = "") -> list[dict]:
    """All recommendations, most important first (priority, then priority_score).
    `domain` localizes each implementation example (real URL + brand, not placeholders)."""
    recs = [r for r in (_section_recommendation(s, domain) for s in sections) if r]
    recs.sort(key=lambda r: (_PRIORITY_RANK[r["priority"]], -r["priority_score"]))
    return recs


def _category_scores(sections: list[dict]) -> list[dict]:
    """Weighted score per report category (a category may fold several signals)."""
    buckets: dict[str, list[tuple[float, float]]] = {c: [] for c in CATEGORIES}
    for s in sections:
        cat = CATEGORY.get(s.get("id"), "AI Extractability")
        buckets.setdefault(cat, []).append((s.get("score", 0), s.get("weight", 0) or 1))
    out = []
    for cat in CATEGORIES:
        pairs = buckets.get(cat) or []
        if not pairs:
            continue
        wsum = sum(w for _, w in pairs) or 1
        score = round(sum(sc * w for sc, w in pairs) / wsum)
        out.append({"category": cat, "score": score, "status": _status(score),
                    "weight": round(wsum, 1)})
    return out


def _summary_text(domain: str, overall: int, crit: int, quick: int) -> str:
    grade = _grade(overall)
    if overall >= _GOOD:
        state = "in strong shape for AI visibility"
    elif overall >= _WARN:
        state = "partially ready for AI visibility, with clear gaps to close"
    else:
        state = "at risk of being invisible to AI engines"
    parts = [
        f"{domain} scored {overall}/100 (grade {grade}) and is {state}.",
    ]
    if crit:
        parts.append(f"There {'is' if crit == 1 else 'are'} {crit} critical "
                     f"issue{'' if crit == 1 else 's'} to address first.")
    else:
        parts.append("No critical issues were found.")
    if quick:
        parts.append(f"{quick} quick win{'' if quick == 1 else 's'} can be shipped in under an hour each.")
    return " ".join(parts)


def build_scorecard(scan: dict, recommendations: list[dict]) -> dict:
    sections = scan.get("sections") or []
    overall = scan.get("overall_score")
    if overall is None:
        # Derive from sections if the stored overall is absent (older rows).
        wsum = sum(s.get("weight", 0) or 0 for s in sections) or 1
        overall = round(sum(s.get("score", 0) * (s.get("weight", 0) or 0) for s in sections) / wsum)

    cats = _category_scores(sections)
    strengths = [
        {"label": s.get("label"), "category": CATEGORY.get(s.get("id"), ""),
         "score": s.get("score")}
        for s in sorted(sections, key=lambda x: -x.get("score", 0))
        if s.get("score", 0) >= _GOOD
    ]
    weaknesses = [
        {"label": s.get("label"), "category": CATEGORY.get(s.get("id"), ""),
         "score": s.get("score")}
        for s in sorted(sections, key=lambda x: x.get("score", 0))
        if s.get("score", 0) < _WARN
    ]
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for r in recommendations:
        counts[r["priority"]] = counts.get(r["priority"], 0) + 1

    quick_wins = [
        _rec_summary(r) for r in recommendations
        if r["difficulty"] == "Easy" and r["priority_score"] >= 1.5
    ][:6]
    top_priorities = [_rec_summary(r) for r in recommendations[:10]]

    domain = scan.get("domain") or scan.get("url") or "This site"
    return {
        "overall_score": overall,
        "grade": _grade(overall),
        "status": _status(overall),
        "category_scores": cats,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "quick_wins": quick_wins,
        "top_priorities": top_priorities,
        "issue_counts": counts,
        "summary": _summary_text(domain, overall, counts["Critical"], len(quick_wins)),
    }


def _rec_summary(r: dict) -> dict:
    """Compact recommendation used in scorecard lists / CSV / PDF tables."""
    return {
        "id": r["id"], "issue_title": r["issue_title"], "category": r["category"],
        "severity": r["severity"], "priority": r["priority"],
        "priority_score": r["priority_score"], "score": r["score"],
        "difficulty": r["difficulty"], "estimated_fix_time": r["estimated_fix_time"],
        "description": r["description"],
    }


def build_report(scan: dict) -> dict:
    """Full report for a stored scan dict.

    `scan` is expected to carry: scan_id, url, domain, overall_score,
    scanner_version, scanned_at, and `sections` (the Phase 3 signal list).
    Tolerates missing/empty sections (returns an empty-but-valid report).
    """
    sections = scan.get("sections") or []
    recommendations = build_recommendations(sections, scan.get("domain") or scan.get("url") or "")
    scorecard = build_scorecard(scan, recommendations)
    scan_ready = scan.get("scan_ready", True)
    bulk_pages = scan.get("bulk_pages")

    # Additive (Phase 4): deeper Schema / Internal Link / Entity intelligence + the
    # scan-only slice of Question Mining, derived from the SAME sections. Never
    # changes overall_score; safe to add to cached reports. `scan_ready` defaults True
    # so callers that don't track scan status (most tests, and pre-Phase-4 code) keep
    # today's behavior; `scan_to_input` sets it from the real Scan.status for every
    # live request, so a pending/running/failed scan gets an honest "scan_incomplete"
    # state instead of a fabricated "everything is missing" diagnosis.
    phase4 = build_phase4_block(sections, scan.get("url"), bulk_pages=bulk_pages, scan_ready=scan_ready)
    # Additive: Technical SEO & Indexability Intelligence — same sections + the raw
    # per-page HTTP/redirect facts scan_to_input already carries.
    technical_seo = build_technical_seo_block(
        sections=sections, url=scan.get("url"),
        status_code=scan.get("status_code"), redirect_chain=scan.get("redirect_chain"),
        final_url=scan.get("final_url"), bulk_pages=bulk_pages, scan_ready=scan_ready)
    # Additive: Real Crawl Graph + True Orphan Detection — built from the SAME
    # bulk-scan per-page evidence Technical SEO uses, plus each page's own outgoing
    # `link_targets`. Only ever available for a multi-page (bulk) scan.
    crawl_graph = build_crawl_graph_block(
        bulk_pages=bulk_pages, bulk_requested_urls=scan.get("bulk_requested_urls"),
        scan_ready=scan_ready)
    # Additive: Content Cannibalization & Duplicate Content Intelligence — built from
    # the SAME bulk-scan per-page evidence, plus each page's own content fingerprint.
    # Only ever available for a multi-page (bulk) scan.
    content_intelligence = build_content_intelligence_block(bulk_pages=bulk_pages, scan_ready=scan_ready)

    return {
        "report_version": REPORT_VERSION,
        "generated_at": _now_iso(),
        "scan_id": scan.get("scan_id") or scan.get("id"),
        "url": scan.get("url"),
        "domain": scan.get("domain"),
        "scanned_at": scan.get("scanned_at"),
        "scanner_version": scan.get("scanner_version"),
        "scorecard": scorecard,
        "recommendations": recommendations,
        "recommendation_count": len(recommendations),
        # Additive (Phase 1+2): read-side negative-first analysis derived from the same
        # sections — score-loss breakdown, "why is my score low", an estimated recovery
        # projection, the score-impact simulator, and the 30-day action plan. Never
        # changes overall_score; safe to add to cached reports.
        "insights": build_insights_block(scan, recommendations, scorecard.get("quick_wins")),
        "phase4": phase4,
        "technical_seo": technical_seo,
        "crawl_graph": crawl_graph,
        "content_intelligence": content_intelligence,
    }
