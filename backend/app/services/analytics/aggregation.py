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

from sqlalchemy import case, func
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
                 AnalyticsConversion.created_at >= start, AnalyticsConversion.created_at < end))
    if verified_only:
        q = q.filter(AnalyticsConversion.verified.is_(True))
    if monitor_ids:
        q = q.filter(AnalyticsConversion.monitor_id.in_(monitor_ids))
    return q


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
    return [{"date": d, "by_channel": c} for d, c in sorted(buckets.items())]


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
            "channel": ch, "sessions": sessions, "conversions": conversions,
            "revenue_cents": revenue,
            "conversion_rate_pct": round(conversions / sessions * 100, 2) if sessions else 0.0,
        })
    out.sort(key=lambda r: -r["sessions"])
    return out


def _aeo_context(db: DbSession, org_id: str, landing_url: str) -> dict | None:
    """Best-effort link to this URL's most recent AEO scan, if one exists for this
    org — never fabricated, None when nothing matches."""
    scan = (db.query(Scan)
            .filter(Scan.organization_id == org_id, Scan.url == landing_url,
                    Scan.status == SCAN_COMPLETED)
            .order_by(Scan.created_at.desc()).first())
    if not scan or not scan.result:
        return None
    issues = scan.result.get("top_issues") or scan.result.get("issues") or []
    score = scan.result.get("overall_score", scan.result.get("ars"))
    return {
        "scan_id": scan.id,
        "score": score,
        "open_issue_count": len(issues),
        "top_issue": issues[0].get("label") if issues else None,
    }


def _prioritize(rows: list[dict]) -> list[dict]:
    """Explainable, evidence-based prioritization — every field a row is scored on is
    disclosed in `reason`; never a revenue-uplift estimate (not labeled/disclosed per
    product requirements, so simply not computed)."""
    for r in rows:
        sessions, ai_sessions = r["sessions"], r["ai_sessions"]
        conv_rate, revenue = r["conversion_rate_pct"], r["revenue_cents"]

        if sessions < _MIN_SAMPLE_SESSIONS:
            r.update(confidence="insufficient_data", pattern=None, priority="unknown",
                     recommendation="Not enough sessions yet for a reliable recommendation on this page.",
                     reason=f"Only {sessions} session(s) observed (need at least {_MIN_SAMPLE_SESSIONS}).")
            continue

        ai_share = ai_sessions / sessions if sessions else 0
        if ai_share >= 0.15 and conv_rate < 1.0:
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
            recommendation = "Already generating real revenue from AI traffic — a good candidate for further optimization."
            reason = f"${revenue / 100:.2f} in confirmed revenue from {ai_sessions} AI-referred sessions."
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


def landing_page_performance(db: DbSession, *, org_id: str, monitor_ids: list[str] | None,
                             start: datetime, end: datetime, limit: int = 50,
                             offset: int = 0, include_aeo: bool = True) -> list[dict]:
    ai_case = case((AnalyticsSession.channel.in_(AI_CHANNELS), 1), else_=0)
    rows = (_session_query(db, org_id, monitor_ids, start, end)
            .with_entities(AnalyticsSession.landing_url,
                           func.count(AnalyticsSession.id).label("sessions"),
                           func.coalesce(func.sum(ai_case), 0).label("ai_sessions"))
            .group_by(AnalyticsSession.landing_url)
            .order_by(func.count(AnalyticsSession.id).desc())
            .limit(limit).offset(offset).all())

    urls = [r.landing_url for r in rows]
    conv_map: dict[str, tuple[int, int]] = {}
    if urls:
        conv_rows = (_conversion_query(db, org_id, monitor_ids, start, end)
                    .filter(AnalyticsSession.landing_url.in_(urls))
                    .with_entities(AnalyticsSession.landing_url, func.count(AnalyticsConversion.id),
                                    func.coalesce(func.sum(
                                        case((AnalyticsConversion.event_type == "purchase",
                                              AnalyticsConversion.value_cents), else_=0)), 0))
                    .group_by(AnalyticsSession.landing_url).all())
        conv_map = {url: (int(cnt), int(rev)) for url, cnt, rev in conv_rows}

    results = []
    for r in rows:
        conv_count, revenue = conv_map.get(r.landing_url, (0, 0))
        sessions = r.sessions or 0
        entry = {
            "url": r.landing_url, "sessions": sessions, "ai_sessions": int(r.ai_sessions or 0),
            "conversions": conv_count,
            "conversion_rate_pct": round(conv_count / sessions * 100, 2) if sessions else 0.0,
            "revenue_cents": revenue,
        }
        if include_aeo:
            entry["aeo"] = _aeo_context(db, org_id, r.landing_url)
        results.append(entry)
    return _prioritize(results)
