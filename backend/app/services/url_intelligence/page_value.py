"""Page Opportunities: a transparent Business Value Score for every URL of a website's
latest completed crawl, from data AEOMirror already stores.

Inputs (all read from the database — no page fetch, no Google call, no AI call):
- the crawl's own evidence → business page type (classification.classify_business_page)
  and the deterministic per-page issues (issues.py), plus inbound internal links;
- GA4 landing-page snapshots, only when the GA4 property is explicitly linked to this
  website → AI sessions, organic sessions, key events, revenue (session-scoped);
- AEOMirror's own server-verified conversions (first-party tracker), when the site
  uses them — they take precedence over GA4 key events, never added to them;
- Search Console page rows, only when the connected property covers this website.

Missing data is never zero. A signal whose source isn't available for this website is
left out of the score's denominator (and its column stays NULL = "not measured"); a
source that IS available and reports nothing for a page is a measured zero. Measured
and inferred components are labelled as such, and every score carries its breakdown.

Scale: a PageValueRun processes `CHUNK_SIZE` URLs per job on the shared ScheduledJob
queue, persisting a keyset cursor after each chunk, so a 100k-URL site is many short,
resumable, retry-safe jobs — never one request. The run's rows only replace the
previous run's when it completes, so readers always see one consistent run.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.billing import entitlements
from app.db.models import (
    CRAWL_COMPLETED, PV_ACTIVE, PV_CANCELLED, PV_COMPLETED, PV_FAILED, PV_PENDING, PV_RUNNING,
    URL_CRAWLED, URL_SKIPPED, AnalyticsConversion, AnalyticsSession, CrawlJob, CrawlUrl,
    GA4_STATUS_ACTIVE, Ga4LandingPageSnapshot, GoogleAnalyticsConnection, GscPageSnapshot, Monitor,
    PageValueRun, PageValueScore, SearchConsoleConnection,
)
from app.services.url_intelligence.classification import (
    BP_UNKNOWN, COMMERCIAL_TYPES, LOW_PRIORITY_TYPES, STATUS_CONFIRMED, STATUS_LIKELY,
    classify_business_page,
)
from app.services.url_intelligence.issues import CATALOG, SEVERITY_RANK

CHUNK_SIZE = 1000
WINDOW_DAYS = 28
MAX_FIRST_PARTY_PAGES = 20000
# Off-site and SSRF-blocked URLs were found in links but aren't this website's pages.
_EXCLUDED_SKIPS = ("off_site", "ssrf_blocked")
AI_CLASSES = ("chatgpt", "perplexity", "gemini", "claude", "copilot", "other_ai")

# How directly a page type can produce a sale, lead or reservation (inferred, not measured).
INTENT = {
    "product": 1.0, "booking": 1.0, "hotel_property": 0.95, "service": 0.9, "landing": 0.85,
    "category": 0.8, "homepage": 0.8, "blog": 0.4, "unknown": 0.3, "utility": 0.1,
    "legal": 0.05, "login_account": 0.05,
}
_STATUS_FACTOR = {STATUS_CONFIRMED: 1.0, STATUS_LIKELY: 0.85}   # uncertain → 0.6
MAX_POINTS = {"purpose": 25, "conversions": 25, "revenue": 10, "ai_sessions": 10,
              "organic_sessions": 10, "search": 10, "links": 10}
PRIORITY_RANK = {"high": 4, "medium": 3, "low": 2, "none": 1, "not_evaluated": 0}

# Opportunity signals derived from measured data (not crawl rules). Thresholds are
# deliberately conservative so a handful of visits never produces a recommendation.
DATA_ISSUES = {
    "low_search_ctr": {
        "title": "Few Google searchers click through", "severity": "medium", "kind": "potential",
        "why": "The page is shown in Google Search often, but clicked far less than this site's pages on average.",
        "fix": "Rewrite the title and meta description so they state what the page offers and who it is for.",
    },
    "traffic_without_conversions": {
        "title": "Traffic but no recorded conversions", "severity": "medium", "kind": "potential",
        "why": "This commercial page receives visits, but no conversion was recorded on sessions that started on it.",
        "fix": "Check that the page's call to action is visible and works, and that the conversion is tracked.",
    },
}
LOW_CTR_MIN_IMPRESSIONS = 200
NO_CONVERSION_MIN_SESSIONS = 100


def _norm(value: float | None, top: float | None) -> float:
    """Log-scaled share of the site's top value, 0–1 (so one outlier page doesn't
    flatten every other page to ~0)."""
    if not value or not top or value <= 0 or top <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(top))


# --------------------------------------------------------------------------- scoring

def score_page(cls: dict, signals: dict, sources: dict) -> dict:
    """Pure: the Business Value Score of one page.

    `cls` is classify_business_page's result. `signals` holds this page's measured
    values (None = not measured). `sources` says which sources are available for the
    website and the site maxima used for normalisation. Returns score (0–100), the
    component breakdown, and how many measured signals were available."""
    comps: list[dict] = []
    factor = _STATUS_FACTOR.get(cls["status"], 0.6)
    intent = INTENT.get(cls["page_type"], INTENT["unknown"])
    comps.append({
        "key": "purpose", "label": "Page purpose", "kind": "inferred", "available": True,
        "value": cls["page_type"], "max_points": MAX_POINTS["purpose"],
        "points": round(MAX_POINTS["purpose"] * intent * factor, 1),
        "note": (f"{cls['page_type'].replace('_', ' ').capitalize()} page ({cls['status']} classification): "
                 f"commercial intent {intent:g}" + ("" if factor == 1.0 else f" x{factor:g} for {cls['status']}")),
    })

    def measured(key: str, label: str, available: bool, value, points: float, note: str) -> None:
        comps.append({"key": key, "label": label, "kind": "measured", "available": available,
                      "value": value if available else None, "max_points": MAX_POINTS[key],
                      "points": round(points, 1) if available else None, "note": note})

    mx = sources.get("maxima") or {}
    conv_src = sources.get("conversion_source")
    conv = signals.get("conversions")
    measured("conversions", "Conversions", conv_src is not None and conv is not None, conv,
             MAX_POINTS["conversions"] * _norm(conv, mx.get("conversions")),
             {"first_party_verified": "Server-verified conversions from sessions that started on this page",
              "ga4_key_events": "GA4 key events from sessions that started on this page"}.get(
                 conv_src, "Not measured: no conversion tracking is available for this website"))
    rev = signals.get("revenue")
    measured("revenue", "Revenue", bool(sources.get("revenue_available")) and rev is not None, rev,
             MAX_POINTS["revenue"] * _norm(rev, mx.get("revenue")),
             f"Revenue reported for sessions that started on this page ({sources.get('revenue_currency')})"
             if sources.get("revenue_available") else "Not measured: no revenue with a known currency")
    ga4 = bool(sources.get("ga4_available"))
    measured("ai_sessions", "AI assistant sessions", ga4 and signals.get("ai_sessions") is not None,
             signals.get("ai_sessions"), MAX_POINTS["ai_sessions"] * _norm(signals.get("ai_sessions"), mx.get("ai_sessions")),
             "GA4 sessions referred by an AI assistant" if ga4 else "Not measured: GA4 isn't linked to this website")
    measured("organic_sessions", "Organic search sessions", ga4 and signals.get("organic_sessions") is not None,
             signals.get("organic_sessions"),
             MAX_POINTS["organic_sessions"] * _norm(signals.get("organic_sessions"), mx.get("organic_sessions")),
             "GA4 sessions from organic search" if ga4 else "Not measured: GA4 isn't linked to this website")
    gsc_ok = bool(sources.get("gsc_available")) and signals.get("gsc_clicks") is not None
    measured("search", "Google Search visibility", gsc_ok,
             {"clicks": signals.get("gsc_clicks"), "impressions": signals.get("gsc_impressions")} if gsc_ok else None,
             MAX_POINTS["search"] * (0.7 * _norm(signals.get("gsc_clicks"), mx.get("gsc_clicks"))
                                     + 0.3 * _norm(signals.get("gsc_impressions"), mx.get("gsc_impressions"))),
             "Search Console clicks (70%) and impressions (30%)" if sources.get("gsc_available")
             else "Not measured: Search Console isn't connected for this website")
    links = signals.get("inbound_links")
    measured("links", "Internal links", links is not None, links,
             MAX_POINTS["links"] * _norm(links, mx.get("inbound_links")),
             "Crawled pages on this site that link here" if links is not None
             else "Not measured: this crawl predates internal-link counting")

    avail = [c for c in comps if c["available"]]
    total_max = sum(c["max_points"] for c in avail)
    score = round(100 * sum(c["points"] for c in avail) / total_max, 1) if total_max else 0.0
    return {"score": score, "components": comps,
            "measured_signals": sum(1 for c in avail if c["kind"] == "measured")}


def data_issues(cls: dict, signals: dict, sources: dict) -> list[str]:
    out = []
    imp, clicks = signals.get("gsc_impressions"), signals.get("gsc_clicks")
    site_ctr = sources.get("site_ctr")
    if imp is not None and imp >= LOW_CTR_MIN_IMPRESSIONS and site_ctr and (clicks or 0) / imp < 0.5 * site_ctr:
        out.append("low_search_ctr")
    if (cls["page_type"] in COMMERCIAL_TYPES and signals.get("conversions") == 0
            and (signals.get("total_sessions") or 0) >= NO_CONVERSION_MIN_SESSIONS):
        out.append("traffic_without_conversions")
    return out


def issue_spec(code: str) -> dict | None:
    return CATALOG.get(code) or DATA_ISSUES.get(code)


def main_issue(codes: list[str]) -> str | None:
    """Highest-severity issue; crawl-checked issues win ties over data signals."""
    ranked = [(SEVERITY_RANK.get((issue_spec(c) or {}).get("severity"), 0), c not in DATA_ISSUES, c)
              for c in codes if issue_spec(c)]
    return max(ranked)[2] if ranked else None


def priority_for(score: float, cls: dict, codes: list[str], evaluated: bool, signals: dict) -> tuple[str, str]:
    """(priority, reason). Issues decide whether there is something to fix; the score
    decides how much fixing it matters. Utility, legal and account pages stay low
    unless measured conversions or revenue show they matter commercially."""
    if not codes:
        return ("none", "No issue found on this page.") if evaluated else \
               ("not_evaluated", "The page wasn't crawled, so it couldn't be checked.")
    sev = max(SEVERITY_RANK.get((issue_spec(c) or {}).get("severity"), 0) for c in codes)
    if (score >= 60 and sev >= 2) or (sev == 4 and score >= 40):
        prio = "high"
    elif (score >= 35 and sev >= 2) or score >= 60:
        prio = "medium"
    else:
        prio = "low"
    reason = f"Business Value Score {score:g} with a {['', 'low', 'medium', 'high', 'critical'][sev]}-severity issue."
    outcome = (signals.get("conversions") or 0) > 0 or (signals.get("revenue") or 0) > 0
    if cls["page_type"] in LOW_PRIORITY_TYPES and prio != "low" and not outcome:
        prio = "low"
        reason += " Kept low: a utility, legal or account page with no measured conversions or revenue."
    return prio, reason


# --------------------------------------------------------------------------- run lifecycle

def latest_completed_crawl(db: Session, org_id: str, monitor_id: str) -> CrawlJob | None:
    return (db.query(CrawlJob)
            .filter(CrawlJob.organization_id == org_id, CrawlJob.monitor_id == monitor_id,
                    CrawlJob.phase == CRAWL_COMPLETED)
            .order_by(CrawlJob.finished_at.desc(), CrawlJob.created_at.desc()).first())


def active_run(db: Session, monitor_id: str) -> PageValueRun | None:
    return (db.query(PageValueRun)
            .filter(PageValueRun.monitor_id == monitor_id, PageValueRun.status.in_(PV_ACTIVE))
            .order_by(PageValueRun.created_at.desc()).first())


def latest_run(db: Session, monitor_id: str, status: str | None = None) -> PageValueRun | None:
    q = db.query(PageValueRun).filter(PageValueRun.monitor_id == monitor_id)
    if status:
        q = q.filter(PageValueRun.status == status)
    return q.order_by(PageValueRun.created_at.desc(), PageValueRun.id.desc()).first()


def request_run(db: Session, *, org_id: str, monitor_id: str, trigger: str,
                user_id: str | None = None) -> PageValueRun | None:
    """Queue a scoring run for the website's latest completed crawl. Deduplicated: an
    active run is returned instead of starting a second one. None when there is no
    completed crawl to score."""
    from app.monitoring import scheduler

    existing = active_run(db, monitor_id)
    if existing:
        return existing
    crawl = latest_completed_crawl(db, org_id, monitor_id)
    if crawl is None:
        return None
    # An older run still analysing pages is superseded: its rows will be replaced, and
    # the new run re-uses every analysis whose inputs haven't changed.
    (db.query(PageValueRun)
     .filter(PageValueRun.monitor_id == monitor_id, PageValueRun.analysis_status.in_(ANALYSIS_ACTIVE))
     .update({PageValueRun.analysis_status: "cancelled"}, synchronize_session=False))
    now = datetime.utcnow()
    run = PageValueRun(organization_id=org_id, monitor_id=monitor_id, crawl_job_id=crawl.id,
                       status=PV_PENDING, trigger=trigger, requested_by_user_id=user_id,
                       processed_urls=0, created_at=now, updated_at=now)
    db.add(run)
    db.commit()
    scheduler.enqueue_page_value_chunk(db, run.id, org_id)
    return run


def request_runs_after_sync(db: Session, org_id: str, trigger: str) -> int:
    """After a GA4 or Search Console sync: rescore the websites whose data changed.
    Pro only (the data sources are Pro features); best-effort."""
    if not entitlements.is_pro(db, org_id):
        return 0
    if trigger == "ga4_sync":
        conn = db.query(GoogleAnalyticsConnection).filter(
            GoogleAnalyticsConnection.organization_id == org_id).first()
        monitor_ids = [conn.linked_monitor_id] if conn and conn.linked_monitor_id else []
    else:
        from app.services.integrations.search_console import site_covers
        conn = db.query(SearchConsoleConnection).filter(
            SearchConsoleConnection.organization_id == org_id).first()
        monitors = db.query(Monitor).filter(Monitor.organization_id == org_id).all() if conn else []
        monitor_ids = [m.id for m in monitors if site_covers(conn.site_url, m.url)]
    n = 0
    for mid in monitor_ids:
        if request_run(db, org_id=org_id, monitor_id=mid, trigger=trigger):
            n += 1
    return n


def cancel_run(db: Session, run: PageValueRun) -> None:
    if run.status == PV_COMPLETED and run.analysis_status in ANALYSIS_ACTIVE:
        # Scores stay (they're complete); only the remaining page analyses are skipped.
        run.analysis_status = "cancelled"
        run.updated_at = datetime.utcnow()
        db.commit()
        return
    if run.status in PV_ACTIVE:
        run.status = PV_CANCELLED
        run.finished_at = run.updated_at = datetime.utcnow()
        db.query(PageValueScore).filter(PageValueScore.run_id == run.id).delete(synchronize_session=False)
        db.commit()


def is_terminal(status: str) -> bool:
    return status in (PV_COMPLETED, PV_FAILED, PV_CANCELLED)


ANALYSIS_ACTIVE = ("pending", "running")


def needs_work(run: PageValueRun) -> bool:
    """Scoring still running, or scoring done and the Page Intelligence stage isn't."""
    return not is_terminal(run.status) or (run.status == PV_COMPLETED and run.analysis_status in ANALYSIS_ACTIVE)


def _cancelled(db: Session, run: PageValueRun) -> bool:
    db.refresh(run, attribute_names=["status"])
    return run.status == PV_CANCELLED


# --------------------------------------------------------------------------- data sources

def _window(today: date | None = None) -> tuple[date, date]:
    end = today or date.today()
    return end - timedelta(days=WINDOW_DAYS - 1), end


def _path_variants(path: str) -> set[str]:
    base = path.rstrip("/") or "/"
    return {base, base + "/"} if base != "/" else {"/"}


def _ga4_rows(db: Session, conn_id: str, start_key: str, end_key: str, paths: set[str] | None):
    q = (db.query(Ga4LandingPageSnapshot.landing_page, Ga4LandingPageSnapshot.traffic_class,
                  func.sum(Ga4LandingPageSnapshot.sessions), func.sum(Ga4LandingPageSnapshot.key_events),
                  func.sum(Ga4LandingPageSnapshot.revenue), func.count(Ga4LandingPageSnapshot.revenue))
         .filter(Ga4LandingPageSnapshot.connection_id == conn_id,
                 Ga4LandingPageSnapshot.date >= start_key, Ga4LandingPageSnapshot.date <= end_key))
    if paths is not None:
        q = q.filter(Ga4LandingPageSnapshot.landing_page.in_(list(paths)))
    return q.group_by(Ga4LandingPageSnapshot.landing_page, Ga4LandingPageSnapshot.traffic_class)


def _ga4_by_path(rows) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for page, cls, sessions, key_events, revenue, revenue_rows in rows:
        p = out.setdefault((page or "").rstrip("/") or "/",
                           {"ai": 0, "organic": 0, "total": 0, "key_events": 0, "revenue": None})
        p["total"] += int(sessions or 0)
        p["key_events"] += int(key_events or 0)
        if cls in AI_CLASSES:
            p["ai"] += int(sessions or 0)
        elif cls == "organic_search":
            p["organic"] += int(sessions or 0)
        if revenue_rows:
            p["revenue"] = (p["revenue"] or 0.0) + float(revenue or 0.0)
    return out


def _gsc_by_key(rows) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, clicks, impressions, weighted_pos in rows:
        out[key] = {"clicks": int(clicks or 0), "impressions": int(impressions or 0),
                    "position": (float(weighted_pos) / impressions) if impressions and weighted_pos is not None else None}
    return out


def _gsc_query(db: Session, conn_id: str, keys: list[str] | None):
    q = (db.query(GscPageSnapshot.page_key, func.sum(GscPageSnapshot.clicks), func.sum(GscPageSnapshot.impressions),
                  func.sum(GscPageSnapshot.position * GscPageSnapshot.impressions))
         .filter(GscPageSnapshot.connection_id == conn_id))
    if keys is not None:
        q = q.filter(GscPageSnapshot.page_key.in_(keys))
    return q.group_by(GscPageSnapshot.page_key)


def prepare_inputs(db: Session, run: PageValueRun, today: date | None = None) -> dict:
    """Decide which sources are available for this website and compute the site-level
    maxima once, so every chunk scores against the same reference."""
    from app.services.analytics.aggregation import page_key
    from app.services.integrations.search_console import site_covers

    monitor = db.get(Monitor, run.monitor_id)
    is_pro = entitlements.is_pro(db, run.organization_id)
    start, end = _window(today)
    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    inputs: dict = {"window": {"start": start.isoformat(), "end": end.isoformat(), "days": WINDOW_DAYS},
                    "maxima": {}, "sources": {}}
    mx = inputs["maxima"]

    # --- GA4 (linked to THIS website only)
    conn = (db.query(GoogleAnalyticsConnection)
            .filter(GoogleAnalyticsConnection.organization_id == run.organization_id).first())
    ga4 = {"available": False}
    if conn is None or not conn.ga4_property_id:
        ga4["reason"] = "Google Analytics isn't connected."
    elif conn.linked_monitor_id != run.monitor_id:
        ga4["reason"] = "The GA4 property isn't linked to this website."
    elif not is_pro:
        ga4["reason"] = "Google Analytics data is a Pro feature."
    elif conn.landing_pages_synced_at is None:
        ga4["reason"] = "GA4's landing-page report hasn't synced yet."
    else:
        by_path = _ga4_by_path(_ga4_rows(db, conn.id, start_key, end_key, None))
        unsplit = (db.query(func.count(Ga4LandingPageSnapshot.id))
                   .filter(Ga4LandingPageSnapshot.connection_id == conn.id,
                           Ga4LandingPageSnapshot.date >= start_key, Ga4LandingPageSnapshot.date <= end_key,
                           Ga4LandingPageSnapshot.traffic_class.is_(None)).scalar() or 0)
        ga4 = {"available": True, "connection_id": conn.id, "start_key": start_key, "end_key": end_key,
               "as_of": conn.landing_pages_synced_at.isoformat(), "property_name": conn.ga4_property_name,
               "source_split_complete": unsplit == 0,
               "key_events_configured": bool(conn.key_event_names) or bool(conn.key_events_configured),
               "currency": conn.currency_code}
        mx["ai_sessions"] = max((p["ai"] for p in by_path.values()), default=0)
        mx["organic_sessions"] = max((p["organic"] for p in by_path.values()), default=0)
        mx["ga4_key_events"] = max((p["key_events"] for p in by_path.values()), default=0)
        mx["ga4_revenue"] = max((p["revenue"] or 0 for p in by_path.values()), default=0)
        ga4["revenue_reported"] = any(p["revenue"] is not None for p in by_path.values())
    inputs["sources"]["ga4"] = ga4

    # --- First-party server-verified conversions (AEOMirror tracker)
    fp = {"available": False}
    uses_verified = (db.query(AnalyticsConversion.id)
                     .filter(AnalyticsConversion.monitor_id == run.monitor_id,
                             AnalyticsConversion.verified.is_(True)).first()) is not None
    if not uses_verified:
        fp["reason"] = "No server-verified conversion has been recorded for this website."
    elif not is_pro:
        fp["reason"] = "Conversion data is a Pro feature."
    else:
        since = datetime.combine(start, datetime.min.time())
        rows = (db.query(AnalyticsSession.landing_url, func.count(AnalyticsConversion.id),
                         func.sum(AnalyticsConversion.value_cents), AnalyticsConversion.currency)
                .join(AnalyticsSession, AnalyticsSession.id == AnalyticsConversion.analytics_session_id)
                .filter(AnalyticsConversion.monitor_id == run.monitor_id, AnalyticsConversion.verified.is_(True),
                        AnalyticsConversion.created_at >= since)
                .group_by(AnalyticsSession.landing_url, AnalyticsConversion.currency).all())
        by_key: dict[str, list] = {}
        currencies = set()
        for landing, n, cents, cur in rows:
            k = page_key(landing)[0]
            e = by_key.setdefault(k, [0, 0])
            e[0] += int(n or 0)
            e[1] += int(cents or 0)
            if cents:
                currencies.add(cur)
        if len(by_key) > MAX_FIRST_PARTY_PAGES:
            fp["reason"] = "Too many converting landing pages to score in one run."
        else:
            fp = {"available": True, "by_page": by_key,
                  "currency": next(iter(currencies)) if len(currencies) == 1 else None,
                  "mixed_currencies": len(currencies) > 1}
            mx["fp_conversions"] = max((v[0] for v in by_key.values()), default=0)
            mx["fp_revenue"] = max((v[1] / 100 for v in by_key.values()), default=0)
    inputs["sources"]["first_party"] = fp

    # Which conversion source counts — one, never both added together.
    if fp.get("available"):
        inputs["conversion_source"] = "first_party_verified"
        mx["conversions"], mx["revenue"] = mx.get("fp_conversions", 0), mx.get("fp_revenue", 0)
        inputs["revenue_available"] = bool(fp.get("currency"))
        inputs["revenue_currency"] = fp.get("currency")
    elif ga4.get("available") and ga4.get("key_events_configured"):
        inputs["conversion_source"] = "ga4_key_events"
        mx["conversions"], mx["revenue"] = mx.get("ga4_key_events", 0), mx.get("ga4_revenue", 0)
        inputs["revenue_available"] = bool(ga4.get("currency")) and bool(ga4.get("revenue_reported"))
        inputs["revenue_currency"] = ga4.get("currency")
    else:
        inputs["conversion_source"] = None
        inputs["revenue_available"] = False
        inputs["revenue_currency"] = None

    # --- Search Console (property must cover this website)
    gconn = (db.query(SearchConsoleConnection)
             .filter(SearchConsoleConnection.organization_id == run.organization_id).first())
    gsc = {"available": False}
    if gconn is None or not gconn.site_url:
        gsc["reason"] = "Search Console isn't connected."
    elif not site_covers(gconn.site_url, monitor.url if monitor else None):
        gsc["reason"] = "The Search Console property doesn't cover this website."
    elif not is_pro:
        gsc["reason"] = "Search Console data is a Pro feature."
    elif gconn.last_synced_at is None:
        gsc["reason"] = "Search Console hasn't synced yet."
    else:
        agg = _gsc_by_key(_gsc_query(db, gconn.id, None))
        clicks = sum(v["clicks"] for v in agg.values())
        impressions = sum(v["impressions"] for v in agg.values())
        gsc = {"available": True, "connection_id": gconn.id, "site_url": gconn.site_url,
               "window": {"start": gconn.window_start, "end": gconn.window_end},
               "as_of": gconn.last_synced_at.isoformat(), "truncated": bool(gconn.truncated)}
        mx["gsc_clicks"] = max((v["clicks"] for v in agg.values()), default=0)
        mx["gsc_impressions"] = max((v["impressions"] for v in agg.values()), default=0)
        inputs["site_ctr"] = (clicks / impressions) if impressions else None
    inputs["sources"]["gsc"] = gsc

    # --- Internal links (crawl structure)
    mx["inbound_links"] = (db.query(func.max(CrawlUrl.inbound_link_count))
                           .filter(CrawlUrl.crawl_job_id == run.crawl_job_id).scalar())
    inputs["sources"]["links"] = {"available": mx["inbound_links"] is not None}
    return inputs


def _score_sources(inputs: dict) -> dict:
    return {"maxima": inputs["maxima"], "conversion_source": inputs.get("conversion_source"),
            "revenue_available": inputs.get("revenue_available"), "revenue_currency": inputs.get("revenue_currency"),
            "ga4_available": inputs["sources"]["ga4"].get("available"),
            "gsc_available": inputs["sources"]["gsc"].get("available"), "site_ctr": inputs.get("site_ctr")}


# --------------------------------------------------------------------------- chunks

def _score_chunk(db: Session, run: PageValueRun, rows: list[CrawlUrl]) -> list[PageValueScore]:
    from app.services.analytics.aggregation import page_key

    inputs = run.inputs
    srcs = _score_sources(inputs)
    ga4, gsc, fp = inputs["sources"]["ga4"], inputs["sources"]["gsc"], inputs["sources"]["first_party"]
    keyed = [(r, *page_key(r.url)) for r in rows]

    ga4_map: dict = {}
    if ga4.get("available"):
        paths = set().union(*(_path_variants(p) for _, _, p in keyed)) if keyed else set()
        ga4_map = _ga4_by_path(_ga4_rows(db, ga4["connection_id"], ga4["start_key"], ga4["end_key"], paths).all())
    gsc_map: dict = {}
    if gsc.get("available"):
        gsc_map = _gsc_by_key(_gsc_query(db, gsc["connection_id"], [k for _, k, _ in keyed]).all())

    out = []
    now = datetime.utcnow()
    for row, key, path in keyed:
        crawled = row.status == URL_CRAWLED
        cls = classify_business_page(row.url, row.evidence if crawled else None, row.page_type)
        s: dict = {"inbound_links": row.inbound_link_count}
        if ga4.get("available"):
            g = ga4_map.get(path.rstrip("/") or "/") or {"ai": 0, "organic": 0, "total": 0, "key_events": 0, "revenue": None}
            s.update(ai_sessions=g["ai"], organic_sessions=g["organic"], total_sessions=g["total"])
            if srcs["conversion_source"] == "ga4_key_events":
                s["conversions"] = g["key_events"]
                s["revenue"] = (g["revenue"] or 0.0) if srcs["revenue_available"] else None
        if srcs["conversion_source"] == "first_party_verified":
            n, cents = fp["by_page"].get(key, [0, 0])
            s["conversions"] = n
            s["revenue"] = cents / 100 if srcs["revenue_available"] else None
        if gsc.get("available"):
            g = gsc_map.get(key)
            if g is not None:
                s.update(gsc_clicks=g["clicks"], gsc_impressions=g["impressions"], gsc_position=g["position"])
            elif not gsc.get("truncated"):
                # Search Console lists every page it showed in results; absent = no impressions.
                s.update(gsc_clicks=0, gsc_impressions=0, gsc_position=None)
            # truncated and absent: unknown (the page may be beyond the rows fetched)

        result = score_page(cls, s, srcs)
        evaluated = crawled and row.issue_codes is not None
        codes = list(row.issue_codes or []) + data_issues(cls, s, srcs)
        prio, prio_reason = priority_for(result["score"], cls, codes, evaluated or bool(codes), s)
        out.append(PageValueScore(
            run_id=run.id, organization_id=run.organization_id, monitor_id=run.monitor_id,
            crawl_job_id=run.crawl_job_id, crawl_url_id=row.id, url=row.url, page_title=row.page_title,
            business_page_type=cls["page_type"], classification_confidence=cls["confidence"],
            classification_status=cls["status"], classification_evidence=cls["evidence"],
            is_commercial=cls["page_type"] in COMMERCIAL_TYPES and cls["status"] != "uncertain"
                          and cls["page_type"] != BP_UNKNOWN,
            ai_sessions=s.get("ai_sessions"), organic_sessions=s.get("organic_sessions"),
            total_sessions=s.get("total_sessions"), conversions=s.get("conversions"),
            conversion_source=srcs["conversion_source"] if s.get("conversions") is not None else None,
            revenue=s.get("revenue"), revenue_currency=srcs["revenue_currency"] if s.get("revenue") is not None else None,
            gsc_clicks=s.get("gsc_clicks"), gsc_impressions=s.get("gsc_impressions"), gsc_position=s.get("gsc_position"),
            inbound_links=s.get("inbound_links"), issue_count=len(codes) if (evaluated or codes) else None,
            main_issue_code=main_issue(codes), score=result["score"], measured_signals=result["measured_signals"],
            score_components={"components": result["components"], "priority_reason": prio_reason,
                              "issue_codes": codes},
            priority=prio, priority_rank=PRIORITY_RANK[prio], created_at=now,
        ))
    return out


def _url_query(db: Session, run: PageValueRun):
    return (db.query(CrawlUrl)
            .filter(CrawlUrl.crawl_job_id == run.crawl_job_id,
                    ~((CrawlUrl.status == URL_SKIPPED) & CrawlUrl.skip_reason.in_(_EXCLUDED_SKIPS))))


def run_chunk(db: Session, run: PageValueRun, chunk_size: int = CHUNK_SIZE) -> None:
    """Advance `run` by one bounded unit of work. Safe to re-run after a crash: a
    chunk's rows are deleted and rewritten before the cursor moves past them."""
    if run.status == PV_COMPLETED and run.analysis_status in ANALYSIS_ACTIVE:
        from app.services.url_intelligence import page_intelligence
        page_intelligence.run_analysis_chunk(db, run)
        return
    if is_terminal(run.status):
        return
    now = datetime.utcnow()
    if run.status == PV_PENDING:
        crawl = db.get(CrawlJob, run.crawl_job_id)
        if crawl is None or crawl.phase != CRAWL_COMPLETED:
            run.status, run.error, run.finished_at = PV_FAILED, "The crawl to score is no longer available.", now
            db.commit()
            return
        db.query(PageValueScore).filter(PageValueScore.run_id == run.id).delete(synchronize_session=False)
        run.inputs = prepare_inputs(db, run)
        run.total_urls = _url_query(db, run).count()
        run.processed_urls = 0
        run.cursor = None
        run.started_at = run.started_at or now
        if _cancelled(db, run):
            return
        run.status = PV_RUNNING
        run.updated_at = now
        db.commit()
        return

    q = _url_query(db, run)
    if run.cursor:
        q = q.filter(CrawlUrl.id > run.cursor)
    rows = q.order_by(CrawlUrl.id.asc()).limit(chunk_size).all()
    if rows:
        ids = [r.id for r in rows]
        (db.query(PageValueScore)
         .filter(PageValueScore.run_id == run.id, PageValueScore.crawl_url_id.in_(ids))
         .delete(synchronize_session=False))
        db.add_all(_score_chunk(db, run, rows))
    if _cancelled(db, run):
        db.rollback()
        return
    if rows:
        run.cursor = rows[-1].id
        run.processed_urls = (run.processed_urls or 0) + len(rows)
    run.updated_at = datetime.utcnow()
    if len(rows) < chunk_size:
        run.status = PV_COMPLETED
        run.finished_at = run.updated_at
        run.analysis_status = "pending"   # Page Intelligence for the highest-value pages next
        # Keep only this website's newest completed run.
        old = [r for (r,) in db.query(PageValueRun.id)
               .filter(PageValueRun.monitor_id == run.monitor_id, PageValueRun.id != run.id,
                       PageValueRun.status == PV_COMPLETED)]
        if old:
            db.query(PageValueScore).filter(PageValueScore.run_id.in_(old)).delete(synchronize_session=False)
    db.commit()


# --------------------------------------------------------------------------- read models

def public_sources(inputs: dict | None) -> dict:
    """The run's data-source block for the UI, without per-page payloads."""
    if not inputs:
        return {}
    src = inputs.get("sources") or {}
    fp = {k: v for k, v in (src.get("first_party") or {}).items() if k != "by_page"}
    ga4 = {k: v for k, v in (src.get("ga4") or {}).items() if k not in ("connection_id", "start_key", "end_key")}
    gsc = {k: v for k, v in (src.get("gsc") or {}).items() if k != "connection_id"}
    return {"window": inputs.get("window"), "ga4": ga4, "gsc": gsc, "first_party": fp,
            "links": src.get("links") or {}, "conversion_source": inputs.get("conversion_source"),
            "revenue_available": inputs.get("revenue_available"),
            "revenue_currency": inputs.get("revenue_currency")}


def why_it_matters(row: PageValueScore) -> list[str]:
    """Plain statements, each tied to a stored value — strongest measured evidence
    first, then the inferred page purpose. Never a revenue promise."""
    out = []
    if row.conversions:
        src = "server-verified conversions" if row.conversion_source == "first_party_verified" else "GA4 key events"
        out.append(f"Sessions that started on this page produced {row.conversions} {src} in the last {WINDOW_DAYS} days.")
    if row.revenue:
        out.append(f"Those sessions reported {row.revenue:,.2f} {row.revenue_currency} in revenue.")
    if row.ai_sessions:
        out.append(f"{row.ai_sessions} sessions arrived from AI assistants.")
    if row.organic_sessions:
        out.append(f"{row.organic_sessions} sessions arrived from organic search.")
    if row.gsc_clicks or row.gsc_impressions:
        out.append(f"Google Search showed it {row.gsc_impressions or 0:,} times and it was clicked {row.gsc_clicks or 0:,} times.")
    if row.inbound_links:
        out.append(f"{row.inbound_links} crawled pages on the site link to it.")
    label = row.business_page_type.replace("_", " ")
    if row.business_page_type in COMMERCIAL_TYPES and row.classification_status != "uncertain":
        out.append(f"It is a {label} page ({row.classification_status} classification), a page type that can directly "
                   "lead to a sale, lead or booking.")
    elif row.business_page_type in LOW_PRIORITY_TYPES:
        out.append(f"It is a {label} page, which rarely drives business outcomes directly.")
    if not out:
        out.append("No measured traffic, search or conversion data was found for this page.")
    return out


def recommended_action(row: PageValueScore, issues: list[dict], findings: list[dict]) -> dict:
    """The single next step, always tied to evidence: an existing Action Center
    finding if one applies (linked, never duplicated), else the page's top checked
    issue, else a measured-data opportunity, else nothing to do."""
    open_findings = [f for f in findings if f.get("status") in ("open", "in_progress")]
    if open_findings:
        f = sorted(open_findings, key=lambda x: (-(x.get("priority_score") or 0), x["id"]))[0]
        return {"kind": "finding", "title": f["finding_title"], "action": f.get("recommended_fix"),
                "evidence": f"{f.get('applies_because', '').capitalize()}.", "finding_id": f["id"]}
    codes = (row.score_components or {}).get("issue_codes") or []
    top = row.main_issue_code
    crawl = next((i for i in issues if i["code"] == top), None)
    if crawl:
        return {"kind": "page_issue", "title": crawl["title"], "action": crawl["recommended_fix"],
                "evidence": f"Observed on this page: {crawl['observed']}" if crawl.get("observed") else
                            "Checked on this page during the latest crawl."}
    if top in DATA_ISSUES:
        spec = DATA_ISSUES[top]
        if top == "low_search_ctr":
            ctr = (row.gsc_clicks or 0) / row.gsc_impressions * 100 if row.gsc_impressions else 0
            ev = f"{row.gsc_impressions:,} Google impressions, {row.gsc_clicks or 0:,} clicks ({ctr:.1f}% click-through)."
        else:
            ev = f"{row.total_sessions:,} sessions and 0 recorded conversions in the last {WINDOW_DAYS} days."
        return {"kind": "data_signal", "title": spec["title"], "action": spec["fix"], "evidence": ev}
    if codes:
        return {"kind": "none", "title": "Review this page", "action": None,
                "evidence": "An issue was recorded but has no recommendation in the catalog."}
    return {"kind": "none", "title": "No action needed right now", "action": None,
            "evidence": "No checked issue or measured-data opportunity was found for this page."}


def score_dict(r: PageValueScore) -> dict:
    spec = issue_spec(r.main_issue_code) if r.main_issue_code else None
    return {
        "crawl_url_id": r.crawl_url_id, "url": r.url, "page_title": r.page_title,
        "page_type": r.business_page_type, "classification_status": r.classification_status,
        "classification_confidence": r.classification_confidence, "is_commercial": r.is_commercial,
        "score": r.score, "measured_signals": r.measured_signals, "priority": r.priority,
        "ai_sessions": r.ai_sessions, "organic_sessions": r.organic_sessions, "total_sessions": r.total_sessions,
        "conversions": r.conversions, "conversion_source": r.conversion_source,
        "revenue": r.revenue, "revenue_currency": r.revenue_currency,
        "gsc_clicks": r.gsc_clicks, "gsc_impressions": r.gsc_impressions, "gsc_position": r.gsc_position,
        "inbound_links": r.inbound_links, "issue_count": r.issue_count,
        "main_issue": {"code": r.main_issue_code, "title": spec["title"], "severity": spec["severity"],
                       "source": "data" if r.main_issue_code in DATA_ISSUES else "crawl"} if spec else None,
    }
