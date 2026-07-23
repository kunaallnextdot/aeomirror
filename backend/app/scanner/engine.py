"""Scanner engine: run all families over a PageBundle and produce a ScanReport."""
from __future__ import annotations

from urllib.parse import urlparse

from app.scanner.families import FAMILY_FUNCS
from app.scanner.models import FamilyResult, PageBundle, ScanReport
from app.scanner.rubric import Rubric, default_rubric

CRAWLER_IDS = {"gptbot_allowed", "claudebot_allowed",
               "perplexitybot_allowed", "google_extended_ok"}


def _domain(url: str) -> str:
    host = urlparse(url).hostname or url
    return host[4:] if host.startswith("www.") else host


def score(page: PageBundle, rubric: Rubric | None = None) -> ScanReport:
    # Default to the code rubric (identical to the seeded active DB row), so
    # scores are the same whether called directly or with the DB-loaded rubric.
    r = rubric or default_rubric()
    families = []
    for fam_id, fam_label, func in FAMILY_FUNCS:
        checks = func(page)
        earned = 0.0
        for c in checks:
            c.weight = r.check_weights.get(c.id, 0)
            c.earned = c.weight * r.status_multiplier[c.status]
            earned += c.earned
        families.append(FamilyResult(
            id=fam_id, label=fam_label, weight=r.family_weights[fam_id],
            earned=round(earned, 1), checks=checks))

    ars = max(0, min(100, round(sum(f.earned for f in families))))

    all_checks = [c for f in families for c in f.checks]
    ranked = sorted(
        (c for c in all_checks if c.status != "pass"),
        key=lambda c: (r.status_multiplier[c.status], -c.weight))
    top_issues = [{
        "id": c.id, "label": c.label, "status": c.status,
        "detail": c.detail, "fix_hint": c.fix_hint,
        "family": next(f.label for f in families if c in f.checks),
    } for c in ranked[:5]]

    crawlers = [{
        "id": c.id, "label": c.label, "status": c.status,
    } for f in families if f.id == "crawler_access" for c in f.checks
        if c.id in CRAWLER_IDS]

    return ScanReport(
        url=page.url, domain=_domain(page.url), ars=ars,
        rubric_version=r.version, families=families,
        top_issues=top_issues, crawlers=crawlers)
