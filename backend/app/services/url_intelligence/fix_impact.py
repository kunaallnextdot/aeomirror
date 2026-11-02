"""Before-and-after performance for one verified fix (URL-wise optimization).

Dated snapshots per source, stored in url_performance_snapshots so the comparison
survives later syncs:

- GA4 (AI-assistant, organic and all sessions, key events, revenue) and server-verified
  conversions: computed from the stored DAILY rows over two equal 28-day windows, one
  ending the day before work on the fix started and one starting the day after the fix
  was verified. The days in between are left out, so neither window mixes "before" and
  "after". A window is used only once it is over AND past the source's reporting delay.
- Search Console: the stored data is one rolling 28-day total (no daily rows), so the
  "before" is the stored window captured while it still ended before work started, and
  the "after" is the first stored window that starts after the fix was verified.
- Answer Tracking: the latest real (non-simulator) run before work started vs the first
  one after verification — tracked prompts only.

Nothing is inferred: a source that isn't connected, doesn't cover the window or didn't
report this page is "unavailable" with the reason, never zero. Changes are observed
associations, never attributed to the fix.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    AnalyticsConversion, AnalyticsSession, Ga4LandingPageSnapshot, GoogleAnalyticsConnection, Monitor, PromptResultAnalysis,
    PromptRun, SearchConsoleConnection, UrlAction, UrlActionEvent, UrlPerformanceSnapshot,
)

WINDOW_DAYS = 28
GA4_LAG_DAYS = 2            # GA4 numbers for a day keep changing for about two days
TRACK_DAYS = 150            # stop waiting for "after" data this long after verification
SOURCES = ("ga4", "first_party", "gsc", "answer_tracking")
SOURCE_LABEL = {"ga4": "Google Analytics 4", "first_party": "Server-verified conversions",
                "gsc": "Google Search Console", "answer_tracking": "Answer Tracking (tracked prompts)"}
DISCLAIMER = ("These are changes observed between the two periods, not proof that the fix caused them. Seasonality, "
              "other changes to the site, campaigns and search-engine updates move these numbers too.")
AI_OVERVIEW_NOTE = ("Google Search Console doesn't separate AI Overview clicks from other Google Search clicks, so "
                    "none are reported as AI traffic.")


def _d(v) -> date | None:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def windows(action: UrlAction) -> dict:
    """Equal-length windows around the change. `excluded` is the work period between
    them, left out of both."""
    # Work start; for a fix found by a crawl with no recorded work, when the action was
    # first tracked (the issue was still present then).
    start = _d(action.work_started_at or action.ready_at or action.created_at or action.verified_at)
    if start is None:
        return {}
    b_end = start - timedelta(days=1)
    out = {"before": (b_end - timedelta(days=WINDOW_DAYS - 1), b_end)}
    v = _d(action.verified_at)
    if v is not None:
        a_start = v + timedelta(days=1)
        out["after"] = (a_start, a_start + timedelta(days=WINDOW_DAYS - 1))
        out["excluded"] = (start, v)
    return out


def _snap(status, start=None, end=None, metrics=None, reason=None) -> dict:
    return {"status": status, "window_start": start.isoformat() if isinstance(start, date) else start,
            "window_end": end.isoformat() if isinstance(end, date) else end, "metrics": metrics, "reason": reason}


# --------------------------------------------------------------------------- sources

def _ga4(db: Session, action: UrlAction, start: date, end: date, today: date) -> dict:
    from app.services.analytics.aggregation import page_key
    from app.services.url_intelligence.page_value import AI_CLASSES, _path_variants

    conn = (db.query(GoogleAnalyticsConnection)
            .filter(GoogleAnalyticsConnection.organization_id == action.organization_id).first())
    if conn is None or not conn.ga4_property_id:
        return _snap("unavailable", start, end, reason="Google Analytics isn't connected.")
    if conn.linked_monitor_id != action.monitor_id:
        return _snap("unavailable", start, end, reason="The GA4 property isn't linked to this website.")
    if conn.landing_pages_synced_at is None:
        return _snap("unavailable", start, end, reason="GA4's landing-page report hasn't synced yet.")
    final_through = min(today, conn.landing_pages_synced_at.date()) - timedelta(days=GA4_LAG_DAYS)
    if end > final_through:
        return _snap("pending", start, end, reason=(
            f"Waiting until GA4's numbers for {end.isoformat()} are final (about {GA4_LAG_DAYS} days after the "
            "window ends, and after the next sync)."))
    earliest = (db.query(func.min(Ga4LandingPageSnapshot.date))
                .filter(Ga4LandingPageSnapshot.connection_id == conn.id).scalar())
    s_key, e_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    if earliest is None or earliest > s_key:
        return _snap("unavailable", start, end, reason="GA4 data stored in AEOMirror doesn't cover this whole window.")
    variants = _path_variants(page_key(action.url)[1])
    rows = (db.query(Ga4LandingPageSnapshot.traffic_class, func.sum(Ga4LandingPageSnapshot.sessions),
                     func.sum(Ga4LandingPageSnapshot.key_events), func.sum(Ga4LandingPageSnapshot.revenue),
                     func.count(Ga4LandingPageSnapshot.revenue))
            .filter(Ga4LandingPageSnapshot.connection_id == conn.id, Ga4LandingPageSnapshot.date >= s_key,
                    Ga4LandingPageSnapshot.date <= e_key, Ga4LandingPageSnapshot.landing_page.in_(list(variants)))
            .group_by(Ga4LandingPageSnapshot.traffic_class).all())
    total = sum(int(r[1] or 0) for r in rows)
    split = all(r[0] is not None for r in rows)
    kev_configured = bool(conn.key_event_names) or bool(conn.key_events_configured)
    revenue_rows = sum(int(r[4] or 0) for r in rows)
    m = {
        "sessions": total,
        "ai_sessions": sum(int(r[1] or 0) for r in rows if r[0] in AI_CLASSES) if split else None,
        "organic_sessions": sum(int(r[1] or 0) for r in rows if r[0] == "organic_search") if split else None,
        "key_events": sum(int(r[2] or 0) for r in rows) if kev_configured else None,
        "revenue": round(sum(float(r[3] or 0) for r in rows), 2) if revenue_rows else None,
        "currency": conn.currency_code if revenue_rows else None,
    }
    notes = []
    if not split:
        notes.append("Part of this window was synced before AEOMirror split sessions by source, so AI and "
                     "organic sessions aren't shown.")
    if not kev_configured:
        notes.append("No GA4 key events are configured, so conversions aren't measured here.")
    return _snap("complete", start, end, m, " ".join(notes) or None)


def _first_party(db: Session, action: UrlAction, start: date, end: date, today: date) -> dict:
    from app.services.analytics.aggregation import page_key

    uses = (db.query(AnalyticsConversion.id)
            .filter(AnalyticsConversion.monitor_id == action.monitor_id, AnalyticsConversion.verified.is_(True))
            .first())
    if uses is None:
        return _snap("unavailable", start, end, reason="No server-verified conversion has been recorded for this website.")
    if end >= today:
        return _snap("pending", start, end, reason=f"The window ends {end.isoformat()}.")
    key = page_key(action.url)[0]
    since = datetime.combine(start, datetime.min.time())
    until = datetime.combine(end + timedelta(days=1), datetime.min.time())
    rows = (db.query(AnalyticsSession.landing_url, AnalyticsConversion.value_cents, AnalyticsConversion.currency)
            .join(AnalyticsSession, AnalyticsSession.id == AnalyticsConversion.analytics_session_id)
            .filter(AnalyticsConversion.monitor_id == action.monitor_id, AnalyticsConversion.verified.is_(True),
                    AnalyticsConversion.created_at >= since, AnalyticsConversion.created_at < until).all())
    mine = [(c, cur) for landing, c, cur in rows if landing and page_key(landing)[0] == key]
    currencies = {cur for c, cur in mine if c}
    return _snap("complete", start, end, {
        "verified_conversions": len(mine),
        "value": round(sum(c or 0 for c, _ in mine) / 100, 2) if len(currencies) == 1 else None,
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
    }, "Values are in more than one currency, so no total is shown." if len(currencies) > 1 else None)


def _gsc(db: Session, action: UrlAction, period: str, win: dict, today: date) -> dict:
    from app.services.analytics.aggregation import page_key
    from app.services.integrations.search_console import DATA_LAG_DAYS, site_covers
    from app.services.url_intelligence.page_value import _gsc_by_key, _gsc_query

    conn = (db.query(SearchConsoleConnection)
            .filter(SearchConsoleConnection.organization_id == action.organization_id).first())
    monitor = db.get(Monitor, action.monitor_id)
    if conn is None or not conn.site_url:
        return _snap("unavailable", reason="Search Console isn't connected.")
    if not site_covers(conn.site_url, monitor.url if monitor else None):
        return _snap("unavailable", reason="The Search Console property doesn't cover this website.")
    if conn.last_synced_at is None or not conn.window_start or not conn.window_end:
        return _snap("pending" if period == "after" else "unavailable",
                     reason="Search Console hasn't synced yet.")
    w_start, w_end = date.fromisoformat(conn.window_start), date.fromisoformat(conn.window_end)
    if period == "before":
        if w_end > win["before"][1]:
            return _snap("unavailable", reason=(
                "Search Console keeps only its latest 28 days, and its stored window already overlaps the work "
                "period, so there is no clean 'before' window for this fix."))
    else:
        v = _d(action.verified_at)
        if w_start <= v:
            expected = v + timedelta(days=WINDOW_DAYS + DATA_LAG_DAYS + 1)
            return _snap("pending", reason=(
                f"Waiting for a Search Console window that starts after the fix was verified ({v.isoformat()}); "
                f"expected around {expected.isoformat()}."))
    key = page_key(action.url)[0]
    agg = _gsc_by_key(_gsc_query(db, conn.id, [key]).all()).get(key)
    if agg is None:
        return _snap("unavailable", w_start, w_end, reason=(
            "This page wasn't among the rows Search Console returned for this window"
            + (" (the sync stopped at its row limit)." if conn.truncated else ".")))
    ctr = round(100 * agg["clicks"] / agg["impressions"], 2) if agg["impressions"] else None
    return _snap("complete", w_start, w_end, {
        "clicks": agg["clicks"], "impressions": agg["impressions"], "ctr_pct": ctr,
        "position": round(agg["position"], 1) if agg["position"] is not None else None,
    }, AI_OVERVIEW_NOTE)


def _answer_runs(db: Session, action: UrlAction):
    from app.db.models import EXTRACTION_COMPLETE, RUN_COMPLETED, RUN_PARTIAL
    return (db.query(PromptRun)
            .filter(PromptRun.organization_id == action.organization_id, PromptRun.monitor_id == action.monitor_id,
                    PromptRun.run_mode == "provider_tracking", PromptRun.status.in_((RUN_COMPLETED, RUN_PARTIAL)),
                    PromptRun.extraction_status == EXTRACTION_COMPLETE))


def _answer_tracking(db: Session, action: UrlAction, period: str) -> dict:
    from app.services.analytics.aggregation import page_key

    base = _answer_runs(db, action)
    if period == "before":
        cut = action.work_started_at or action.ready_at or action.created_at
        run = base.filter(PromptRun.created_at < cut).order_by(PromptRun.created_at.desc()).first()
        if run is None:
            return _snap("unavailable", reason="No completed Answer Tracking run before work on this fix started.")
    else:
        run = base.filter(PromptRun.created_at > action.verified_at).order_by(PromptRun.created_at.asc()).first()
        if run is None:
            return _snap("pending", reason="Waiting for the next completed Answer Tracking run after the fix.")
    key = page_key(action.url)[0]
    cited = (db.query(PromptResultAnalysis.brand_urls_cited)
             .filter(PromptResultAnalysis.run_id == run.id, PromptResultAnalysis.extraction_failed.is_(False)).all())
    n = sum(1 for (urls,) in cited if any(isinstance(u, str) and page_key(u)[0] == key for u in (urls or [])))
    day = (run.completed_at or run.created_at).date()
    return _snap("complete", day, day, {"answers": len(cited), "answers_citing_page": n},
                 "Your tracked prompts only; the prompt list may differ between the two runs.")


# --------------------------------------------------------------------------- capture

def _compute(db: Session, action: UrlAction, source: str, period: str, win: dict, today: date) -> dict:
    if source == "gsc":
        return _gsc(db, action, period, win, today)
    if source == "answer_tracking":
        return _answer_tracking(db, action, period)
    start, end = win[period]
    return (_ga4 if source == "ga4" else _first_party)(db, action, start, end, today)


def capture(db: Session, action: UrlAction, today: date | None = None, periods=("before", "after")) -> None:
    """Fill in whatever snapshots can be taken now. A complete snapshot is final and never
    recomputed; pending/unavailable ones are retried until the after-window tracking ends.
    Sets `action.next_snapshot_at` (None once nothing is left to wait for)."""
    today = today or date.today()
    win = windows(action)
    if not win:
        return
    existing = {(s.period, s.source): s for s in db.query(UrlPerformanceSnapshot)
                .filter(UrlPerformanceSnapshot.action_id == action.id).all()}
    waiting = False
    for period in periods:
        if period not in win:
            continue
        for source in SOURCES:
            row = existing.get((period, source))
            if row is not None and row.status == "complete":
                continue
            snap = _compute(db, action, source, period, win, today)
            if (period == "after" and snap["status"] == "pending" and action.verified_at
                    and (today - _d(action.verified_at)).days > TRACK_DAYS):
                snap = {**snap, "status": "unavailable",
                        "reason": f"No complete data arrived within {TRACK_DAYS} days of the fix."}
            if row is None:
                row = UrlPerformanceSnapshot(organization_id=action.organization_id, monitor_id=action.monitor_id,
                                             action_id=action.id, normalized_key=action.normalized_key,
                                             period=period, source=source)
                db.add(row)
            row.status, row.metrics, row.reason = snap["status"], snap["metrics"], snap["reason"]
            row.window_start, row.window_end = snap["window_start"], snap["window_end"]
            row.captured_at = datetime.utcnow()
            if snap["status"] == "complete":
                db.add(UrlActionEvent(organization_id=action.organization_id, monitor_id=action.monitor_id,
                                      normalized_key=action.normalized_key, action_id=action.id, code=action.code,
                                      kind="snapshot", detail={"source": source, "period": period,
                                                               "window": [snap["window_start"], snap["window_end"]]}))
            elif snap["status"] == "pending":
                waiting = True
    action.next_snapshot_at = datetime.combine(today + timedelta(days=1), datetime.min.time()) if waiting else None
    db.commit()


def _change(b, a):
    if not isinstance(b, (int, float)) or not isinstance(a, (int, float)) or isinstance(b, bool):
        return None
    return {"before": b, "after": a, "delta": round(a - b, 2),
            "pct": round(100 * (a - b) / b, 1) if b else None}


def comparison(db: Session, action: UrlAction) -> dict | None:
    """The stored before/after snapshots of one action, side by side per source. A
    change is given only where both windows are complete."""
    snaps = db.query(UrlPerformanceSnapshot).filter(UrlPerformanceSnapshot.action_id == action.id).all()
    if not snaps:
        return None
    by = {(s.period, s.source): s for s in snaps}
    win = windows(action)
    out = []
    for source in SOURCES:
        b, a = by.get(("before", source)), by.get(("after", source))
        if b is None and a is None:
            continue
        side = lambda s: None if s is None else {  # noqa: E731
            "status": s.status, "window": [s.window_start, s.window_end], "metrics": s.metrics, "reason": s.reason}
        changes = {}
        if b is not None and a is not None and b.status == a.status == "complete":
            for k, v in (b.metrics or {}).items():
                c = _change(v, (a.metrics or {}).get(k))
                if c is not None:
                    changes[k] = c
        out.append({"source": source, "label": SOURCE_LABEL[source], "before": side(b), "after": side(a),
                    "changes": changes})
    ex = win.get("excluded")
    return {"sources": out, "excluded_period": [ex[0].isoformat(), ex[1].isoformat()] if ex else None,
            "window_days": WINDOW_DAYS, "disclaimer": DISCLAIMER}
