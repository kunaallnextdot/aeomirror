"""Date-range aggregation for the AI Business Impact dashboard. SQL aggregates
(COUNT/SUM/GROUP BY) do the heavy lifting; only bounded result sets are ever
materialized in Python (mirrors the existing GA4 /traffic endpoint's own
fetch-then-aggregate-in-Python pattern for consistency, not a new convention).

Revenue/conversion metrics only ever count `verified=True` AnalyticsConversion rows —
a client-reported-but-unconfirmed purchase/booking never contributes to a number a
business owner would read as "real revenue" (see services/analytics/ingest.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session as DbSession

from app.db.models import SCAN_COMPLETED, AnalyticsConversion, AnalyticsSession, Scan

AI_CHANNELS = {"chatgpt", "perplexity", "gemini", "copilot", "claude", "other_identifiable_ai"}
_MIN_SAMPLE_SESSIONS = 20   # below this, a landing-page recommendation is "insufficient_data"

# Reserved landing-URL marker for a manual diagnostic/verification visit (e.g. "does
# a real request from the registered domain actually get collected and persisted?"
# — see ANALYTICS_TRACKING.md's own verification procedure). Any session whose
# landing_url contains this is excluded from every metric below — a test event must
# never be mistaken for real customer traffic, and this needs no schema change: the
# marker lives in the ordinary `url` the tracker already sends (as a query string,
# e.g. "?aeomirror_diagnostic=1"), the same field every real pageview already uses.
DIAGNOSTIC_URL_MARKER = "aeomirror_diagnostic"


def page_key(url: str | None) -> tuple[str, str]:
    """(normalized key, display path) for a raw landing URL. The tracker sends the full
    window.location.href, so without this "/pricing", "/pricing/",
    "/pricing?utm_source=x" and "/pricing#faq" were four different "pages", each too
    small to analyze. The key is host (lowercased, no "www.") + path (no trailing
    slash) — the same shape as Scan.normalized_url (routes_scan._normalize), so a page
    can be matched to its audit."""
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return raw.lower(), raw
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    key = host + ("" if path == "/" else path)
    return key, path


def _session_query(db: DbSession, org_id: str, monitor_ids: list[str] | None,
                   start: datetime, end: datetime):
    q = db.query(AnalyticsSession).filter(
        AnalyticsSession.organization_id == org_id,
        AnalyticsSession.started_at >= start, AnalyticsSession.started_at < end,
        ~AnalyticsSession.landing_url.contains(DIAGNOSTIC_URL_MARKER, autoescape=True),
    )
    if monitor_ids:
        q = q.filter(AnalyticsSession.monitor_id.in_(monitor_ids))
    return q


def _conversion_query(db: DbSession, org_id: str, monitor_ids: list[str] | None,
                      start: datetime, end: datetime, *, verified_only: bool = True):
    q = (db.query(AnalyticsConversion)
         .join(AnalyticsSession, AnalyticsConversion.analytics_session_id == AnalyticsSession.id,
               isouter=True)
         .filter(AnalyticsConversion.organization_id == org_id,
                 AnalyticsConversion.created_at >= start, AnalyticsConversion.created_at < end,
                 # Same diagnostic exclusion as _session_query — previously a test visit
                 # was excluded from sessions but its conversions still counted.
                 or_(AnalyticsSession.id.is_(None),
                     ~AnalyticsSession.landing_url.contains(DIAGNOSTIC_URL_MARKER, autoescape=True))))
    if verified_only:
        q = q.filter(AnalyticsConversion.verified.is_(True))
    if monitor_ids:
        q = q.filter(AnalyticsConversion.monitor_id.in_(monitor_ids))
    return q


def conversion_tracking_configured(db: DbSession, org_id: str, monitor_ids: list[str] | None) -> bool:
    """True once at least one server-confirmed conversion has EVER been recorded for
    these sites. Before that, a conversion count of 0 isn't evidence of "nothing
    converted" — it means the confirm integration (routes_tracking /tracking/confirm)
    isn't sending anything, so no conversion-based conclusion may be drawn."""
    q = db.query(AnalyticsConversion.id).filter(AnalyticsConversion.organization_id == org_id,
                                                AnalyticsConversion.verified.is_(True))
    if monitor_ids:
        q = q.filter(AnalyticsConversion.monitor_id.in_(monitor_ids))
    return q.first() is not None


def first_session_at(db: DbSession, org_id: str, monitor_ids: list[str] | None) -> datetime | None:
    q = db.query(func.min(AnalyticsSession.started_at)).filter(
        AnalyticsSession.organization_id == org_id,
        ~AnalyticsSession.landing_url.contains(DIAGNOSTIC_URL_MARKER, autoescape=True))
    if monitor_ids:
        q = q.filter(AnalyticsSession.monitor_id.in_(monitor_ids))
    return q.scalar()


def summary(db: DbSession, *, org_id: str, monitor_ids: list[str] | None,
           start: datetime, end: datetime) -> dict:
    sess_q = _session_query(db, org_id, monitor_ids, start, end)
    total_sessions = sess_q.count()
    ai_sessions = sess_q.filter(AnalyticsSession.channel.in_(AI_CHANNELS)).count()

    # Page views: summed from each session's own running count (set at ingest time —
    # see ingest.record_pageview) rather than a second query against
    # AnalyticsPageview, matching this module's own "SQL aggregates, bounded
    # materialization" convention.
    total_page_views = (sess_q.with_entities(
        func.coalesce(func.sum(AnalyticsSession.page_view_count), 0)).scalar()) or 0

    # Unique visitors: distinct first-party visitor_id. NULL when the visitor's
    # browser storage was unavailable (see ingest.get_or_create_session) — those
    # sessions still count toward total_sessions, just never toward this estimate,
    # so it is explicitly an estimate, never inflated by double-counting an unknown
    # visitor as several "unique" ones.
    unique_visitors = (sess_q.filter(AnalyticsSession.visitor_id.isnot(None))
                       .with_entities(func.count(func.distinct(AnalyticsSession.visitor_id))).scalar()) or 0
    ai_unique_visitors = (sess_q.filter(AnalyticsSession.channel.in_(AI_CHANNELS),
                                        AnalyticsSession.visitor_id.isnot(None))
                          .with_entities(func.count(func.distinct(AnalyticsSession.visitor_id))).scalar()) or 0

    # Server-confirmed only (verified=True) — the only conversions ever counted as
    # real revenue/bookings/leads (see ingest.record_conversion's own docstring).
    conv_q = _conversion_query(db, org_id, monitor_ids, start, end)
    ai_conv_q = conv_q.filter(AnalyticsSession.channel.in_(AI_CHANNELS))
    ai_conversions = ai_conv_q.count()
    ai_revenue_cents = (ai_conv_q.filter(AnalyticsConversion.event_type == "purchase")
                        .with_entities(func.coalesce(func.sum(AnalyticsConversion.value_cents), 0))
                        .scalar()) or 0
    confirmed_bookings = conv_q.filter(AnalyticsConversion.event_type == "booking_confirmed").count()
    leads = conv_q.filter(AnalyticsConversion.event_type == "lead_submitted").count()

    # Visitor-reported, NEVER server-confirmed (verified=False) — shown as a
    # separate, explicitly-unverified count, never folded into the numbers above or
    # counted as real revenue. A row that gets confirmed later moves from here into
    # the verified counts above (ingest.record_conversion upgrades the same row).
    unverified_q = _conversion_query(db, org_id, monitor_ids, start, end, verified_only=False) \
        .filter(AnalyticsConversion.verified.is_(False))
    unverified_bookings = unverified_q.filter(AnalyticsConversion.event_type == "booking_confirmed").count()
    unverified_leads = unverified_q.filter(AnalyticsConversion.event_type == "lead_submitted").count()

    return {
        "total_sessions": total_sessions,
        "total_page_views": int(total_page_views),
        "unique_visitors": unique_visitors,
        "ai_sessions": ai_sessions,
        "ai_unique_visitors": ai_unique_visitors,
        "ai_share_pct": round(ai_sessions / total_sessions * 100, 1) if total_sessions else None,
        "ai_conversions": ai_conversions,
        "ai_conversion_rate_pct": round(ai_conversions / ai_sessions * 100, 2) if ai_sessions else None,
        "ai_revenue_cents": int(ai_revenue_cents),
        "confirmed_bookings": confirmed_bookings,
        "unverified_bookings": unverified_bookings,
        "leads": leads,
        "unverified_leads": unverified_leads,
        "has_data": total_sessions > 0,
        "conversion_tracking_configured": conversion_tracking_configured(db, org_id, monitor_ids),
    }


def traffic_over_time(db: DbSession, *, org_id: str, monitor_ids: list[str] | None,
                      start: datetime, end: datetime, tz_offset_minutes: int = 0) -> list[dict]:
    """Daily session counts by channel. Bucketed in Python (not a DB date-trunc) so the
    same code path works identically on SQLite (tests) and Postgres (prod), and so the
    `tz_offset_minutes` shift (the organization's reporting timezone) applies uniformly."""
    rows = (_session_query(db, org_id, monitor_ids, start, end)
            .with_entities(AnalyticsSession.started_at, AnalyticsSession.channel).all())
    offset = timedelta(minutes=tz_offset_minutes)
    buckets: dict[str, dict[str, int]] = {}
    for started_at, channel in rows:
        day = (started_at + offset).date().isoformat()
        buckets.setdefault(day, {})
        buckets[day][channel] = buckets[day].get(channel, 0) + 1

    # Every day in the range, not just days with sessions — previously empty days were
    # dropped and the chart drew a smooth curve straight across them. A day is only a
    # real zero once tracking had started; before the site's first-ever session it is
    # "not tracked" (tracked=False), never a fabricated 0.
    first = first_session_at(db, org_id, monitor_ids)
    first_day = (first + offset).date() if first else None
    out = []
    day = (start + offset).date()
    last = (end + offset - timedelta(microseconds=1)).date()
    while day <= last:
        key = day.isoformat()
        by_channel = buckets.get(key, {})
        tracked = first_day is not None and day >= first_day
        out.append({"date": key, "by_channel": by_channel, "tracked": tracked,
                    "total": sum(by_channel.values()) if tracked else None})
        day += timedelta(days=1)
    return out


def channel_breakdown(db: DbSession, *, org_id: str, monitor_ids: list[str] | None,
                      start: datetime, end: datetime) -> list[dict]:
    session_rows = (_session_query(db, org_id, monitor_ids, start, end)
                    .with_entities(AnalyticsSession.channel, func.count(AnalyticsSession.id))
                    .group_by(AnalyticsSession.channel).all())
    sessions_by_channel = {c: n for c, n in session_rows}

    conv_rows = (_conversion_query(db, org_id, monitor_ids, start, end)
                .with_entities(AnalyticsSession.channel, func.count(AnalyticsConversion.id),
                                func.coalesce(func.sum(
                                    case((AnalyticsConversion.event_type == "purchase",
                                          AnalyticsConversion.value_cents), else_=0)), 0))
                .group_by(AnalyticsSession.channel).all())
    conv_by_channel = {c: (n, int(rev)) for c, n, rev in conv_rows}

    channels = set(sessions_by_channel) | set(conv_by_channel)
    out = []
    for ch in channels:
        sessions = sessions_by_channel.get(ch, 0)
        conversions, revenue = conv_by_channel.get(ch, (0, 0))
        out.append({
            # A confirmed conversion with no linked session has no channel — label it
            # honestly instead of emitting a null channel the UI can't render.
            "channel": ch if ch is not None else "unlinked",
            "sessions": sessions, "conversions": conversions, "revenue_cents": revenue,
            # No sessions -> no denominator -> no rate (was a fabricated 0.0).
            "conversion_rate_pct": round(conversions / sessions * 100, 2) if sessions else None,
        })
    out.sort(key=lambda r: -r["sessions"])
    return out


def _aeo_context(db: DbSession, org_id: str, key: str) -> dict | None:
    """This page's most recent completed AEOMirror audit, matched on the normalized
    URL (see page_key) — previously an exact match on the raw tracked href, which a
    query string, trailing slash, "www." or http/https difference always broke.
    Issue counts come from every signal's real issue list, not the 5-item
    `top_issues` preview. Display-only evidence: an audit never changes the
    traffic-based priority below, and None when nothing matches."""
    scan = (db.query(Scan)
            .filter(Scan.organization_id == org_id, Scan.normalized_url == key,
                    Scan.status == SCAN_COMPLETED)
            .order_by(Scan.created_at.desc()).first())
    if not scan or not scan.result:
        return None
    sections = scan.result.get("sections") or []
    failing = [s for s in sections if s.get("status") in ("fail", "warn")]
    return {
        "scan_id": scan.id,
        "score": scan.result.get("overall_score"),
        "scanned_at": scan.result.get("scanned_at") or (scan.created_at.isoformat() if scan.created_at else None),
        "issue_count": sum(len(s.get("issues") or []) for s in sections),
        "failing_signals": [s.get("label") or s.get("id") for s in failing if s.get("status") == "fail"],
        "warning_signals": [s.get("label") or s.get("id") for s in failing if s.get("status") == "warn"],
    }


def _prioritize(rows: list[dict], *, conversion_measured: bool) -> list[dict]:
    """Explainable, evidence-based prioritization — every field a row is scored on is
    disclosed in `reason`; never a revenue-uplift estimate.

    Conversion-based patterns are only evaluated when `conversion_measured` (at least
    one server-confirmed conversion has ever been recorded for the site). Before
    that, a 0% conversion rate is not evidence the page "converts poorly" — it means
    conversions aren't being reported — and the old rule flagged nearly every page
    with AI traffic as high priority on exactly that non-evidence."""
    for r in rows:
        sessions, ai_sessions = r["sessions"], r["ai_sessions"]
        conv_rate, revenue = r["conversion_rate_pct"], r["revenue_cents"]

        if sessions < _MIN_SAMPLE_SESSIONS:
            r.update(confidence="insufficient_data", pattern=None, priority="unknown",
                     recommendation="Not enough sessions yet for a reliable recommendation on this page.",
                     reason=f"Only {sessions} session(s) observed (need at least {_MIN_SAMPLE_SESSIONS}).")
            continue

        ai_share = ai_sessions / sessions if sessions else 0
        if not conversion_measured:
            if ai_share >= 0.15:
                pattern, priority = "ai_traffic_conversion_unmeasured", "medium"
                recommendation = ("Receives meaningful AI-referred traffic, but conversions aren't "
                                  "being confirmed yet — set up conversion confirmation to learn "
                                  "whether this traffic converts.")
                reason = (f"{round(ai_share * 100)}% of {sessions} sessions are AI-referred; "
                          "no confirmed conversions have ever been recorded for this site.")
            else:
                pattern, priority = "steady", "low"
                recommendation = "No traffic pattern stands out for this page in the selected period."
                reason = (f"{sessions} sessions, {ai_sessions} AI-referred. Conversion impact "
                          "can't be assessed until conversion confirmation is set up.")
        elif ai_share >= 0.15 and conv_rate < 1.0:
            pattern, priority = "high_ai_traffic_low_conversion", "high"
            recommendation = ("Gets meaningful AI-referred traffic but converts poorly — "
                              "review this page's call-to-action and content clarity.")
            reason = f"{round(ai_share * 100)}% of sessions are AI-referred; conversion rate is {conv_rate}%."
        elif conv_rate >= 3.0 and sessions < 100:
            pattern, priority = "high_conversion_low_traffic", "medium"
            recommendation = ("Converts well but gets limited traffic — improving this page's "
                              "AI/search visibility could compound an already-strong result.")
            reason = f"Conversion rate is {conv_rate}% on only {sessions} sessions."
        elif ai_share >= 0.1 and revenue > 0:
            pattern, priority = "strong_ai_business_outcome", "medium"
            recommendation = "Already generating confirmed revenue from AI traffic — a good candidate for further optimization."
            reason = f"{revenue / 100:.2f} in confirmed revenue from {ai_sessions} AI-referred sessions."
        else:
            pattern, priority = "steady", "low"
            recommendation = "No urgent pattern detected for this page in the selected period."
            reason = f"{sessions} sessions, {conv_rate}% conversion rate, {ai_sessions} AI-referred."

        r.update(
            confidence=("medium" if sessions < 100 else "high"),
            pattern=pattern, priority=priority, recommendation=recommendation, reason=reason,
        )

    order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    rows.sort(key=lambda r: (order.get(r["priority"], 3), -r["sessions"]))
    return rows


_MAX_RAW_URLS = 5000   # bounds the per-raw-URL fetch before merging into normalized pages


def landing_page_performance(db: DbSession, *, org_id: str, monitor_ids: list[str] | None,
                             start: datetime, end: datetime, limit: int = 50,
                             offset: int = 0, include_aeo: bool = True) -> list[dict]:
    ai_case = case((AnalyticsSession.channel.in_(AI_CHANNELS), 1), else_=0)
    raw_rows = (_session_query(db, org_id, monitor_ids, start, end)
                .with_entities(AnalyticsSession.landing_url,
                               func.count(AnalyticsSession.id).label("sessions"),
                               func.coalesce(func.sum(ai_case), 0).label("ai_sessions"))
                .group_by(AnalyticsSession.landing_url)
                .order_by(func.count(AnalyticsSession.id).desc())
                .limit(_MAX_RAW_URLS).all())

    # Merge raw hrefs into normalized pages (page_key) BEFORE thresholding/ranking.
    pages: dict[str, dict] = {}
    raw_to_key: dict[str, str] = {}
    for r in raw_rows:
        key, path = page_key(r.landing_url)
        raw_to_key[r.landing_url] = key
        p = pages.setdefault(key, {"key": key, "path": path, "url": None, "url_sessions": -1,
                                   "sessions": 0, "ai_sessions": 0, "variant_count": 0})
        p["sessions"] += r.sessions or 0
        p["ai_sessions"] += int(r.ai_sessions or 0)
        p["variant_count"] += 1
        if (r.sessions or 0) > p["url_sessions"]:   # display the most-visited variant
            p["url"], p["url_sessions"] = r.landing_url, r.sessions or 0

    conv_map: dict[str, list[int]] = {}
    if raw_to_key:
        conv_rows = (_conversion_query(db, org_id, monitor_ids, start, end)
                    .filter(AnalyticsSession.landing_url.in_(list(raw_to_key)))
                    .with_entities(AnalyticsSession.landing_url, func.count(AnalyticsConversion.id),
                                    func.coalesce(func.sum(
                                        case((AnalyticsConversion.event_type == "purchase",
                                              AnalyticsConversion.value_cents), else_=0)), 0))
                    .group_by(AnalyticsSession.landing_url).all())
        for url, cnt, rev in conv_rows:
            agg = conv_map.setdefault(raw_to_key[url], [0, 0])
            agg[0] += int(cnt)
            agg[1] += int(rev)

    conversion_measured = conversion_tracking_configured(db, org_id, monitor_ids)
    results = []
    for key, p in pages.items():
        conv_count, revenue = conv_map.get(key, (0, 0))
        sessions = p["sessions"]
        entry = {
            "url": p["url"], "path": p["path"], "page_key": key,
            "variant_count": p["variant_count"],
            "sessions": sessions, "ai_sessions": p["ai_sessions"],
            "conversions": conv_count if conversion_measured else None,
            "conversion_rate_pct": (round(conv_count / sessions * 100, 2) if sessions else None)
                                   if conversion_measured else None,
            "revenue_cents": revenue,
            "conversion_measured": conversion_measured,
        }
        results.append(entry)

    ranked = _prioritize(results, conversion_measured=conversion_measured)
    page_slice = ranked[offset: offset + limit]
    if include_aeo:   # only for the returned slice — bounded, never N+1 over every page
        for e in page_slice:
            e["aeo"] = _aeo_context(db, org_id, e["page_key"])
    return page_slice
