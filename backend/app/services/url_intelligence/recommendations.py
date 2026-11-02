"""The PREPARING phase: evidence-backed recommendations from persisted per-page issues.

For every (template, issue) pair, ONE finding lists exactly the pages that have that
issue — each page was checked by issues.page_issues at crawl time, so a template-level
finding here is never extrapolated from a representative page (unlike AI findings,
which are labeled "ai_representative" for that reason). The finding carries the count
and identity of affected URLs, sample observations from real evidence, and the
catalog's why / fix / verification text.

Then user decisions are carried forward from the previous completed crawl of the same
site and engine version, matched by `issue_key`: "in progress" and "won't fix" stay; an
issue marked fixed that is still present is reopened with verification "unchanged"
(the fix didn't take), never silently left "fixed". `first_detected_at` is inherited,
so recurring issues keep their real age.

Idempotent: re-running the chunk (a worker retry) replaces this crawl's deterministic
findings rather than duplicating them.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from app.db.models import (
    CRAWL_COMPLETED, FINDING_FIXED, FINDING_IN_PROGRESS, FINDING_OPEN, FINDING_WONT_FIX,
    URL_CRAWLED, VERIFY_UNCHANGED, CrawlJob, CrawlUrl, UrlFinding,
)
from app.services.url_intelligence.issues import CATALOG, observed_value

_AFFECTED_SAMPLE = 50       # affected URLs stored on the finding (the count is exact)
_OBSERVATION_SAMPLE = 5     # pages whose exact evidence is quoted on the finding

PAGE_TYPE_LABEL = {
    "homepage": "homepage", "product": "product", "product_category": "category",
    "pricing": "pricing", "comparison": "comparison", "booking": "booking",
    "flight_hotel_inventory": "inventory", "service": "service", "lead_generation": "contact/lead",
    "informational": "article/resource", "legal_utility": "legal/utility", "other": "other",
}


def _title(code: str, n: int, size: int, page_type: str | None) -> str:
    base = CATALOG[code]["title"]
    if size <= 1:
        return base
    label = PAGE_TYPE_LABEL.get(page_type or "other", "other")
    return f"{base} — {n} of {size} {label} pages"


def build_deterministic_findings(db: Session, job: CrawlJob) -> int:
    (db.query(UrlFinding)
     .filter(UrlFinding.crawl_job_id == job.id, UrlFinding.evidence_basis == "deterministic")
     .delete(synchronize_session=False))

    groups: dict[tuple, dict] = defaultdict(lambda: {"ids": [], "urls": [], "page_type": None})
    sizes: Counter = Counter()
    rows = (db.query(CrawlUrl.id, CrawlUrl.url, CrawlUrl.template_id, CrawlUrl.page_type,
                     CrawlUrl.issue_codes)
            .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED)
            .order_by(CrawlUrl.url, CrawlUrl.id)
            .yield_per(1000))
    for rid, url, template_id, page_type, codes in rows:
        t = template_id or "unclassified"
        sizes[t] += 1
        for code in codes or []:
            if code not in CATALOG:
                continue
            g = groups[(t, code)]
            g["ids"].append(rid); g["urls"].append(url); g["page_type"] = page_type

    created = []
    for (t, code), g in groups.items():
        spec, n, size = CATALOG[code], len(g["ids"]), sizes[t]
        sample = (db.query(CrawlUrl.url, CrawlUrl.evidence)
                  .filter(CrawlUrl.id.in_(g["ids"][:_OBSERVATION_SAMPLE])).all())
        limitation = (f"Checked on each of the {size} crawled page(s) in this group; "
                      f"{n} have this issue.")
        if spec["kind"] == "potential":
            limitation += " Whether it is a problem depends on intent the crawler can't see."
        created.append(UrlFinding(
            crawl_job_id=job.id, crawl_url_id=g["ids"][0], organization_id=job.organization_id,
            url=g["urls"][0], template_id=None if t == "unclassified" else t,
            affected_url_count=n, page_type=g["page_type"],
            finding_title=_title(code, n, size, g["page_type"]), category=spec["category"],
            evidence={
                "issue_code": code, "kind": spec["kind"], "affected_count": n, "group_size": size,
                "affected_urls": g["urls"][:_AFFECTED_SAMPLE],
                "observations": [{"url": u, "observed": observed_value(code, e or {})}
                                 for u, e in sample],
            },
            evidence_source="crawl_evidence", why_it_matters=spec["why"],
            recommended_fix=spec["fix"], expected_benefit=None, priority=spec["severity"],
            confidence="high" if spec["kind"] == "confirmed" else "medium",
            evidence_limitations=limitation, verification_procedure=spec["verify"],
            issue_key=f"det:{code}:{t}", evidence_basis="deterministic",
        ))
    db.add_all(created)
    db.commit()
    return len(created)


def previous_comparable_job(db: Session, job: CrawlJob) -> CrawlJob | None:
    """The latest completed crawl of the same site before `job`, of the same engine
    version — the only baseline it may be compared with or inherit status from."""
    if not job.engine_version:
        return None
    return (db.query(CrawlJob)
            .filter(CrawlJob.monitor_id == job.monitor_id,
                    CrawlJob.organization_id == job.organization_id,
                    CrawlJob.phase == CRAWL_COMPLETED, CrawlJob.id != job.id,
                    CrawlJob.engine_version == job.engine_version,
                    CrawlJob.created_at < job.created_at)
            .order_by(CrawlJob.created_at.desc())
            .first())


def carry_forward_status(db: Session, job: CrawlJob) -> int:
    prev = previous_comparable_job(db, job)
    if prev is None:
        return 0
    prev_by_key = {f.issue_key: f for f in (db.query(UrlFinding)
                                           .filter(UrlFinding.crawl_job_id == prev.id,
                                                   UrlFinding.issue_key.isnot(None)))}
    carried = 0
    for f in db.query(UrlFinding).filter(UrlFinding.crawl_job_id == job.id,
                                         UrlFinding.issue_key.isnot(None)):
        p = prev_by_key.get(f.issue_key)
        if not p:
            continue
        f.first_detected_at = p.first_detected_at
        if p.status in (FINDING_IN_PROGRESS, FINDING_WONT_FIX):
            f.status = p.status
        elif p.status == FINDING_FIXED:
            f.status, f.verification_status = FINDING_OPEN, VERIFY_UNCHANGED
        carried += 1
    db.commit()
    return carried
