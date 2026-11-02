"""Site Intelligence read models: the overview, crawl comparison, URL detail, and
page-level traffic. Read-only, organization/website scoped by the caller (routes),
built only from persisted rows — nothing here fetches a page or calls a model.

Definitions used throughout (and returned to the UI, so they're never implied):
- discovered: every URL recorded for the crawl, including skipped ones.
- in scope: discovered minus skipped (robots-blocked, off-site, SSRF-blocked).
- crawl coverage: crawled ÷ in scope.
- analysis coverage: crawled pages whose template has been analyzed ÷ crawled.
- pending: in scope but not fetched yet (discovered or queued).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    CRAWL_ACTIVE_PHASES, CRAWL_COMPLETED, FINDING_IN_PROGRESS, FINDING_OPEN, URL_CRAWLED,
    URL_DISCOVERED, URL_ELIGIBLE, URL_FAILED, URL_SKIPPED, AnalyticsSession, CrawlJob, CrawlUrl,
    Ga4LandingPageSnapshot, GoogleAnalyticsConnection, Monitor, UrlFinding,
)
from app.services.url_intelligence.issues import CATALOG, observed_value, page_issues
from app.services.url_intelligence.prioritization import priority_score
from app.services.url_intelligence.recommendations import previous_comparable_job

STALL_MINUTES = 15          # no chunk progress for this long while active → "stalled"
TRAFFIC_WINDOW_DAYS = 28
LEGACY_PLACEHOLDER_TITLE = "No deterministic content issues detected"

STAGES = {
    "pending": ("Queued", "Waiting for the background worker to start this analysis."),
    "discovering": ("Discovering URLs", "Reading robots.txt and sitemaps to find this site's pages."),
    "crawling": ("Crawling pages", "Fetching each page, extracting its evidence and grouping it into page templates."),
    "analyzing": ("Analyzing representative pages", "Running AI analysis on one representative page per template."),
    "preparing": ("Preparing recommendations", "Turning every page's checked issues into prioritized recommendations."),
    "completed": ("Completed", "Analysis finished."),
    "failed": ("Failed", "The analysis stopped before finishing."),
    "cancelled": ("Cancelled", "The analysis was cancelled."),
}
SEVERITY_LABEL = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: None}


def is_placeholder(f: UrlFinding) -> bool:
    """Pre-v2 crawls stored a non-finding ("No deterministic content issues detected")
    as if it were one. It is never shown as something to fix."""
    return f.model_used is None and f.evidence_basis is None and f.finding_title == LEGACY_PLACEHOLDER_TITLE


def finding_kind(f: UrlFinding) -> str:
    """confirmed | potential | insufficient_evidence — never stronger than the evidence."""
    if f.confidence == "insufficient_evidence":
        return "insufficient_evidence"
    if f.evidence_basis == "deterministic":
        return (f.evidence or {}).get("kind") or "confirmed"
    return "potential"      # AI reading of a representative page, or a legacy row


def finding_dict(f: UrlFinding) -> dict:
    scored = priority_score(f)
    ev = f.evidence or {}
    det = f.evidence_basis == "deterministic"
    return {
        "id": f.id, "url": f.url, "template_id": f.template_id,
        "affected_url_count": f.affected_url_count, "page_type": f.page_type,
        "finding_title": f.finding_title, "category": f.category,
        "why_it_matters": f.why_it_matters, "recommended_fix": f.recommended_fix,
        "expected_benefit": f.expected_benefit, "priority": f.priority,
        "confidence": f.confidence, "evidence_limitations": f.evidence_limitations,
        "verification_procedure": f.verification_procedure, "status": f.status,
        "verification_status": f.verification_status, "model_used": f.model_used,
        "cache_hit": f.cache_hit, "first_detected_at": f.first_detected_at,
        "last_checked_at": f.last_checked_at,
        "priority_score": scored["score"], "priority_reason": scored["reason"],
        "issue_key": f.issue_key, "evidence_basis": f.evidence_basis, "kind": finding_kind(f),
        "issue_code": ev.get("issue_code") if det else None,
        "affected_urls": ev.get("affected_urls") if det else ([f.url] if f.url else []),
        "observations": ev.get("observations") if det else None,
        "group_size": ev.get("group_size") if det else None,
        "last_verification": ev.get("last_verification"),
        "representative_url": None if det else f.url,
    }


def actionable(query):
    """Findings that ask the user to do something: not legacy placeholders, not
    'insufficient evidence', not closed."""
    return query.filter(~((UrlFinding.model_used.is_(None)) & (UrlFinding.evidence_basis.is_(None))
                          & (UrlFinding.finding_title == LEGACY_PLACEHOLDER_TITLE)),
                        UrlFinding.confidence != "insufficient_evidence")


def job_summary(job: CrawlJob) -> dict:
    return {
        "id": job.id, "monitor_id": job.monitor_id, "seed_url": job.seed_url,
        "max_urls": job.max_urls, "phase": job.phase, "engine_version": job.engine_version,
        "discovered_count": job.discovered_count, "eligible_count": job.eligible_count,
        "crawled_count": job.crawled_count, "analyzed_count": job.analyzed_count,
        "failed_count": job.failed_count, "skipped_count": job.skipped_count,
        "ai_calls_total": job.ai_calls_total, "ai_calls_cached": job.ai_calls_cached,
        "ai_tokens_input_total": job.ai_tokens_input_total,
        "ai_tokens_output_total": job.ai_tokens_output_total,
        "error": job.error, "created_at": job.created_at, "started_at": job.started_at,
        "finished_at": job.finished_at, "updated_at": job.updated_at,
    }


def url_counts(db: Session, job: CrawlJob) -> dict:
    rows = dict(db.query(CrawlUrl.status, func.count(CrawlUrl.id))
                .filter(CrawlUrl.crawl_job_id == job.id).group_by(CrawlUrl.status).all())
    analyzed = (db.query(func.count(CrawlUrl.id))
                .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED,
                        CrawlUrl.analyzed.is_(True)).scalar() or 0)
    with_issues = (db.query(func.count(CrawlUrl.id))
                   .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.issue_count > 0).scalar() or 0)
    evaluated = (db.query(func.count(CrawlUrl.id))
                 .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.issue_count.isnot(None)).scalar() or 0)
    discovered = sum(rows.values())
    skipped, crawled, failed = rows.get(URL_SKIPPED, 0), rows.get(URL_CRAWLED, 0), rows.get(URL_FAILED, 0)
    in_scope = discovered - skipped
    pending = rows.get(URL_DISCOVERED, 0) + rows.get(URL_ELIGIBLE, 0)
    pct = lambda a, b: round(a / b * 100, 1) if b else None   # noqa: E731
    return {
        "discovered": discovered, "in_scope": in_scope, "skipped": skipped, "pending": pending,
        "crawled": crawled, "failed": failed, "analyzed": analyzed,
        "with_issues": with_issues, "issues_evaluated": evaluated,
        "crawl_coverage_pct": pct(crawled, in_scope),
        "analysis_coverage_pct": pct(analyzed, crawled),
        "definitions": {
            "crawl_coverage_pct": "Crawled ÷ in-scope URLs (discovered minus skipped).",
            "analysis_coverage_pct": "Crawled pages whose template has been analyzed ÷ crawled pages.",
        },
    }


def _progress(job: CrawlJob, counts: dict) -> dict | None:
    """Only what the stored counters actually say — never an estimate."""
    if job.phase == "discovering":
        return {"done": counts["discovered"], "total": None, "unit": "URLs found so far"}
    if job.phase == "crawling":
        return {"done": counts["crawled"] + counts["failed"], "total": counts["in_scope"],
                "unit": "pages fetched", "total_is_final": False,
                "note": "The total grows as links on crawled pages are discovered."}
    if job.phase == "analyzing":
        return {"done": counts["analyzed"], "total": counts["crawled"], "unit": "pages analyzed",
                "total_is_final": True}
    return None


def _warnings(db: Session, job: CrawlJob, counts: dict) -> list[str]:
    out = []
    if job.engine_version is None:
        out.append("This analysis predates per-page issue checks; run a new analysis to see them.")
    if counts["failed"]:
        out.append(f"{counts['failed']} page(s) could not be fetched.")
    templates = (db.query(func.count(func.distinct(CrawlUrl.template_id)))
                 .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED,
                         CrawlUrl.template_id.isnot(None)).scalar() or 0)
    ai = (db.query(func.count(UrlFinding.id))
          .filter(UrlFinding.crawl_job_id == job.id, UrlFinding.model_used.isnot(None)).scalar() or 0)
    if templates and job.ai_calls_total == 0 and job.ai_calls_cached == 0:
        out.append("AI analysis did not run (not configured or daily limit reached); "
                   "recommendations come from deterministic page checks only.")
    elif templates and ai < templates:
        out.append(f"{templates - ai} of {templates} page template(s) got no usable AI analysis; "
                   "their deterministic page checks are still included.")
    return out


def status_block(db: Session, job: CrawlJob, counts: dict, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    label, description = STAGES.get(job.phase, (job.phase, ""))
    active = job.phase in CRAWL_ACTIVE_PHASES
    last = job.updated_at or job.started_at or job.created_at
    stalled = bool(active and last and now - last > timedelta(minutes=STALL_MINUTES))
    warnings = _warnings(db, job, counts) if job.phase == CRAWL_COMPLETED else []
    outcome = ("completed_with_warnings" if warnings else "completed") if job.phase == CRAWL_COMPLETED else job.phase
    return {
        "phase": job.phase, "outcome": outcome, "label": label, "description": description,
        "active": active, "progress": _progress(job, counts) if active else None,
        "last_progress_at": last, "stalled": stalled,
        "stalled_note": (f"No progress for over {STALL_MINUTES} minutes. The background worker may "
                         "not be running; you can cancel and start again.") if stalled else None,
        "warnings": warnings, "can_cancel": active,
    }


def latest_jobs(db: Session, org_id: str, monitor_id: str) -> tuple[CrawlJob | None, CrawlJob | None]:
    base = db.query(CrawlJob).filter(CrawlJob.monitor_id == monitor_id, CrawlJob.organization_id == org_id)
    latest = base.order_by(CrawlJob.created_at.desc()).first()
    completed = (base.filter(CrawlJob.phase == CRAWL_COMPLETED)
                 .order_by(CrawlJob.created_at.desc()).first())
    return latest, completed


def finding_counts(db: Session, job: CrawlJob) -> dict:
    rows = actionable(db.query(UrlFinding).filter(UrlFinding.crawl_job_id == job.id)).all()
    open_rows = [f for f in rows if f.status in (FINDING_OPEN, FINDING_IN_PROGRESS)]
    by_priority = {p: sum(1 for f in open_rows if f.priority == p) for p in ("critical", "high", "medium", "low")}
    insufficient = (db.query(func.count(UrlFinding.id))
                    .filter(UrlFinding.crawl_job_id == job.id,
                            UrlFinding.confidence == "insufficient_evidence").scalar() or 0)
    return {"open": len(open_rows), "total": len(rows), "by_priority": by_priority,
            "confirmed": sum(1 for f in open_rows if finding_kind(f) == "confirmed"),
            "potential": sum(1 for f in open_rows if finding_kind(f) == "potential"),
            "insufficient_evidence": insufficient}


def top_priorities(db: Session, job: CrawlJob, limit: int) -> list[dict]:
    rows = actionable(db.query(UrlFinding).filter(
        UrlFinding.crawl_job_id == job.id,
        UrlFinding.status.in_((FINDING_OPEN, FINDING_IN_PROGRESS)))).all()
    rows.sort(key=lambda f: (-priority_score(f)["score"], f.id))
    return [finding_dict(f) for f in rows[:limit]]


# ------------------------------------------------------------------ comparison

def _host(url: str | None) -> str:
    from urllib.parse import urlparse
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def comparability(current: CrawlJob, baseline: CrawlJob | None) -> dict:
    if baseline is None:
        return {"available": False, "reason": "no_baseline",
                "message": "There is no earlier completed analysis of this website to compare with."}
    if not current.engine_version or current.engine_version != baseline.engine_version:
        return {"available": False, "reason": "incompatible_engine",
                "message": "The earlier analysis used a different version of the page checks, so the two can't be compared fairly."}
    if _host(current.normalized_seed_url) != _host(baseline.normalized_seed_url):
        return {"available": False, "reason": "different_site",
                "message": "The earlier analysis was of a different address for this website."}
    return {"available": True}


def compare_findings(db: Session, current: CrawlJob, baseline: CrawlJob) -> dict:
    def keyed(job):
        rows = actionable(db.query(UrlFinding).filter(UrlFinding.crawl_job_id == job.id,
                                                       UrlFinding.issue_key.isnot(None))).all()
        return {f.issue_key: f for f in rows}
    cur, base = keyed(current), keyed(baseline)
    brief = lambda f: {"id": f.id, "title": f.finding_title, "priority": f.priority,   # noqa: E731
                       "affected_url_count": f.affected_url_count}
    new = [cur[k] for k in cur.keys() - base.keys()]
    resolved = [base[k] for k in base.keys() - cur.keys()]
    recurring = [cur[k] for k in cur.keys() & base.keys()]
    order = lambda fs: sorted(fs, key=lambda f: (-priority_score(f)["score"], f.id))   # noqa: E731
    return {
        "new": len(new), "resolved": len(resolved), "recurring": len(recurring),
        "new_sample": [brief(f) for f in order(new)[:10]],
        "resolved_sample": [brief(f) for f in order(resolved)[:10]],
    }


def compare_jobs(db: Session, current: CrawlJob, baseline: CrawlJob | None, *, include_pages: bool) -> dict:
    gate = comparability(current, baseline)
    if not gate["available"]:
        return gate
    cc, bc = url_counts(db, current), url_counts(db, baseline)
    out = {
        "available": True,
        "current": {"job_id": current.id, "finished_at": current.finished_at, **{k: cc[k] for k in ("discovered", "crawled", "analyzed", "failed", "with_issues")}},
        "baseline": {"job_id": baseline.id, "finished_at": baseline.finished_at, **{k: bc[k] for k in ("discovered", "crawled", "analyzed", "failed", "with_issues")}},
        "findings": compare_findings(db, current, baseline),
    }
    if include_pages:
        def pages(job):
            return {k: (u, n) for k, u, n in db.query(CrawlUrl.normalized_key, CrawlUrl.url, CrawlUrl.issue_count)
                    .filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.status == URL_CRAWLED)}
        cur_p, base_p = pages(current), pages(baseline)
        both = cur_p.keys() & base_p.keys()
        worse = sorted((k for k in both if (cur_p[k][1] or 0) > (base_p[k][1] or 0)), key=lambda k: cur_p[k][0])
        better = sorted((k for k in both if (cur_p[k][1] or 0) < (base_p[k][1] or 0)), key=lambda k: cur_p[k][0])
        added = sorted(cur_p.keys() - base_p.keys(), key=lambda k: cur_p[k][0])
        removed = sorted(base_p.keys() - cur_p.keys(), key=lambda k: base_p[k][0])
        row = lambda k: {"url": cur_p[k][0], "before": base_p[k][1], "after": cur_p[k][1]}   # noqa: E731
        out["pages"] = {
            "added": len(added), "removed": len(removed), "more_issues": len(worse), "fewer_issues": len(better),
            "added_sample": [cur_p[k][0] for k in added[:20]],
            "removed_sample": [base_p[k][0] for k in removed[:20]],
            "more_issues_sample": [row(k) for k in worse[:20]],
            "fewer_issues_sample": [row(k) for k in better[:20]],
        }
    return out


# ------------------------------------------------------------------ URL detail

def page_issue_details(row: CrawlUrl) -> tuple[bool, list[dict]]:
    """(evaluated, issues). Legacy rows without stored codes are evaluated from their
    evidence if they have any; a row with no evidence was never checked."""
    evidence = row.evidence or {}
    if row.issue_codes is not None:
        codes = row.issue_codes
    elif evidence:
        codes = page_issues(evidence, row.page_type)
    else:
        return False, []
    out = []
    for c in codes:
        spec = CATALOG.get(c)
        if not spec:
            continue
        out.append({"code": c, "title": spec["title"], "kind": spec["kind"], "severity": spec["severity"],
                    "category": spec["category"], "observed": observed_value(c, evidence),
                    "why_it_matters": spec["why"], "recommended_fix": spec["fix"],
                    "verification_procedure": spec["verify"]})
    return True, out


def applicable_findings(db: Session, job: CrawlJob, row: CrawlUrl, issues: list[dict], *,
                        is_pro: bool, free_preview: int) -> list[dict]:
    """The Action Center findings that apply to one page — one card per finding, never
    a per-page copy: deterministic findings whose issue was checked on this page, and
    AI findings on its template. Free plan: only the findings in the free preview."""
    keys = {f"det:{c['code']}:{row.template_id or 'unclassified'}" for c in issues}
    if not (keys or row.template_id):
        return []
    cands = actionable(db.query(UrlFinding).filter(UrlFinding.crawl_job_id == job.id)).all()
    visible = None
    if not is_pro:
        ranked = sorted(cands, key=lambda f: (-priority_score(f)["score"], f.id))
        visible = {f.id for f in ranked[:free_preview]}
    out = []
    for f in cands:
        on_page = f.issue_key in keys
        via_template = (f.evidence_basis != "deterministic" and row.template_id
                        and f.template_id == row.template_id)
        if not (on_page or via_template) or (visible is not None and f.id not in visible):
            continue
        out.append({**finding_dict(f), "applies_because": (
            "checked on this page" if on_page else
            "AI analysis of this template's representative page — not checked on this page itself")})
    return out


def page_traffic(db: Session, monitor: Monitor, url: str, is_pro: bool, now: datetime | None = None) -> dict:
    """Stored traffic for one page: AEOMirror's own tracker, and GA4 only when its
    property is explicitly linked to this website. Never a live Google call."""
    from app.services.analytics.aggregation import DIAGNOSTIC_URL_MARKER, page_key
    now = now or datetime.utcnow()
    since = now - timedelta(days=TRAFFIC_WINDOW_DAYS)
    key, path = page_key(url)
    out = {"window_days": TRAFFIC_WINDOW_DAYS}

    first_seen = (db.query(func.min(AnalyticsSession.started_at))
                  .filter(AnalyticsSession.monitor_id == monitor.id).scalar())
    if first_seen is None:
        out["first_party"] = {"status": "not_configured", "source": "first_party",
                              "reason": "The AEOMirror tracking snippet hasn't recorded a visit on this website."}
    else:
        rows = (db.query(AnalyticsSession.landing_url, func.count(AnalyticsSession.id))
                .filter(AnalyticsSession.monitor_id == monitor.id, AnalyticsSession.started_at >= since,
                        ~AnalyticsSession.landing_url.contains(DIAGNOSTIC_URL_MARKER, autoescape=True))
                .group_by(AnalyticsSession.landing_url).limit(5000).all())
        sessions = sum(n for u, n in rows if page_key(u)[0] == key)
        out["first_party"] = {"status": "available" if sessions else "zero", "source": "first_party",
                              "sessions": sessions, "partial_window": first_seen > since,
                              "tracking_since": first_seen}

    conn = (db.query(GoogleAnalyticsConnection)
            .filter(GoogleAnalyticsConnection.organization_id == monitor.organization_id).first())
    if conn is None or not conn.ga4_property_id:
        out["ga4"] = {"status": "not_configured", "source": "ga4", "reason": "Google Analytics isn't connected."}
    elif conn.linked_monitor_id != monitor.id:
        out["ga4"] = {"status": "not_configured", "source": "ga4",
                      "reason": "The GA4 property isn't linked to this website."}
    elif not is_pro:
        out["ga4"] = {"status": "unavailable", "source": "ga4", "reason": "Google Analytics data is a Pro feature."}
    elif conn.landing_pages_synced_at is None:
        out["ga4"] = {"status": "not_configured", "source": "ga4",
                      "reason": "GA4's landing-page report hasn't synced yet."}
    else:
        variants = {path, path.rstrip("/") or "/", (path.rstrip("/") + "/") if path != "/" else "/"}
        sessions = (db.query(func.coalesce(func.sum(Ga4LandingPageSnapshot.sessions), 0))
                    .filter(Ga4LandingPageSnapshot.connection_id == conn.id,
                            Ga4LandingPageSnapshot.landing_page.in_(variants),
                            Ga4LandingPageSnapshot.date >= since.strftime("%Y%m%d")).scalar() or 0)
        stale = now - conn.landing_pages_synced_at > timedelta(hours=48)
        out["ga4"] = {"status": "stale" if stale else ("available" if sessions else "zero"), "source": "ga4",
                      "sessions": int(sessions), "as_of": conn.landing_pages_synced_at}
    return out
