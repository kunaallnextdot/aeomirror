"""AI Traffic & Conversion Intelligence — read models over the GA4 data AEOMirror has
already synced (Ga4TrafficSnapshot / Ga4LandingPageSnapshot / Ga4EventSnapshot). No
live Google call, no estimates.

What this data can and cannot say (repeated to the UI as `definitions`):
- GA4's Data API returns AGGREGATED counts. Attribution here is SESSION-level: an
  event is credited to the source that started the session it happened in. There is
  no user-level path (no "this person came from ChatGPT and bought a week later").
- A "conversion" is an EVENT COUNT for the events chosen as conversions — a session
  with two purchases counts twice. The rate is therefore conversions per 100 sessions,
  not "% of sessions that converted".
- Revenue is GA4's own totalRevenue in the property's currency, and only when GA4
  reported it and the currency is known.
- Google AI Overviews clicks are indistinguishable from ordinary Google organic clicks
  in GA4 and are counted as organic search, never AI.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    AI_ASSISTANT_LABELS, EVENT_ROLE_CONVERSION, EVENT_ROLE_FUNNEL, EVENT_ROLES,
    GA4_CONVERSION_TYPES, TRAFFIC_CLASSES, Ga4EventMapping, Ga4EventSnapshot,
    Ga4LandingPageSnapshot, Ga4TrafficSnapshot, GoogleAnalyticsConnection,
)

STALE_AFTER_HOURS = 48
# GA4 finalises a day's data over up to ~48h; the two most recent days can still move.
PROCESSING_LAG_DAYS = 2

CLASS_LABEL = {
    "chatgpt": "ChatGPT", "perplexity": "Perplexity", "gemini": "Gemini", "claude": "Claude",
    "copilot": "Microsoft Copilot", "other_ai": "Other AI assistants",
    "organic_search": "Organic search", "paid_search": "Paid search", "direct": "Direct",
    "referral": "Referral", "social": "Social", "other": "Other / unattributable",
}

# Well-known GA4 event names and what they usually mean — offered as SUGGESTIONS only.
# Nothing counts as a conversion until the business saves a mapping (or, failing that,
# has marked it a key event in GA4 itself).
STANDARD_EVENTS = {
    "view_item": (EVENT_ROLE_FUNNEL, None),
    "add_to_cart": (EVENT_ROLE_FUNNEL, None),
    "begin_checkout": (EVENT_ROLE_FUNNEL, None),
    "purchase": (EVENT_ROLE_CONVERSION, "purchase"),
    "generate_lead": (EVENT_ROLE_CONVERSION, "lead"),
    "form_submit": (EVENT_ROLE_CONVERSION, "lead"),
    "booking_completed": (EVENT_ROLE_CONVERSION, "booking"),
    "sign_up": (EVENT_ROLE_CONVERSION, "signup"),
}
FUNNEL = ("view_item", "add_to_cart", "begin_checkout", "purchase")
# Collected automatically by GA4 on every site; never a business conversion by default.
AUTO_EVENTS = {"page_view", "session_start", "first_visit", "user_engagement", "scroll", "click",
               "file_download", "view_search_results", "video_start", "video_progress",
               "video_complete", "form_start"}
_EVENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")


def _key(d: date) -> str:
    return d.strftime("%Y%m%d")


def _pct(a, b, digits=1):
    return round(a / b * 100, digits) if b else None


# ------------------------------------------------------------------ mappings

class MappingError(ValueError):
    pass


def validate_mappings(items: list[dict]) -> list[dict]:
    if len(items) > 50:
        raise MappingError("At most 50 event mappings.")
    seen, out = set(), []
    for it in items:
        name = (it.get("event_name") or "").strip()
        role = it.get("role")
        ctype = it.get("conversion_type")
        if not _EVENT_NAME_RE.match(name):
            raise MappingError(f"'{name}' is not a valid GA4 event name.")
        if name in seen:
            raise MappingError(f"'{name}' is mapped twice.")
        if role not in EVENT_ROLES:
            raise MappingError(f"role must be one of {EVENT_ROLES}.")
        if role == EVENT_ROLE_CONVERSION and ctype not in GA4_CONVERSION_TYPES:
            raise MappingError(f"conversion_type must be one of {GA4_CONVERSION_TYPES}.")
        seen.add(name)
        out.append({"event_name": name, "role": role,
                    "conversion_type": ctype if role == EVENT_ROLE_CONVERSION else None,
                    "label": (it.get("label") or "").strip()[:80] or None,
                    "counts_revenue": bool(it.get("counts_revenue")) and role == EVENT_ROLE_CONVERSION})
    return out


def replace_mappings(db: Session, conn: GoogleAnalyticsConnection, items: list[dict], user_id: str) -> list[dict]:
    clean = validate_mappings(items)
    db.query(Ga4EventMapping).filter(Ga4EventMapping.connection_id == conn.id).delete(synchronize_session=False)
    now = datetime.utcnow()
    for m in clean:
        db.add(Ga4EventMapping(organization_id=conn.organization_id, connection_id=conn.id,
                               property_id=conn.ga4_property_id, created_by_user_id=user_id,
                               created_at=now, updated_at=now, **m))
    db.commit()
    return clean


def _mappings(db: Session, conn: GoogleAnalyticsConnection) -> dict[str, Ga4EventMapping]:
    return {m.event_name: m for m in db.query(Ga4EventMapping).filter(
        Ga4EventMapping.connection_id == conn.id,
        Ga4EventMapping.property_id == conn.ga4_property_id)}


def conversion_definition(db: Session, conn: GoogleAnalyticsConnection) -> dict:
    """Which events count as conversions, and why:
    - "mapped": the business chose them in AEOMirror.
    - "ga4_key_events": nothing mapped, so the events marked as key events in GA4.
    - "none": neither — conversions are not measured (never shown as 0)."""
    maps = _mappings(db, conn)
    conv = {n: m for n, m in maps.items() if m.role == EVENT_ROLE_CONVERSION}
    if conv:
        return {"mode": "mapped", "events": sorted(conv),
                "types": {n: m.conversion_type for n, m in conv.items()},
                "revenue_events": sorted(n for n, m in conv.items() if m.counts_revenue)}
    keys = conn.key_event_names or []
    if keys:
        return {"mode": "ga4_key_events", "events": sorted(keys),
                "types": {n: STANDARD_EVENTS.get(n, (None, "custom"))[1] or "custom" for n in keys},
                "revenue_events": [n for n in keys if n == "purchase"]}
    return {"mode": "none", "events": [], "types": {}, "revenue_events": []}


# ------------------------------------------------------------------ event catalog

def event_catalog(db: Session, conn: GoogleAnalyticsConnection, start: date, end: date) -> dict:
    """Every event GA4 actually recorded in the range, with its mapping and a
    suggestion — plus, for each standard event, whether it is collected at all."""
    rows = (db.query(Ga4EventSnapshot.event_name, func.sum(Ga4EventSnapshot.event_count),
                     func.sum(Ga4EventSnapshot.key_events))
            .filter(Ga4EventSnapshot.connection_id == conn.id,
                    Ga4EventSnapshot.date >= _key(start), Ga4EventSnapshot.date <= _key(end))
            .group_by(Ga4EventSnapshot.event_name).all())
    counts = {n: (int(c or 0), int(k or 0)) for n, c, k in rows}
    maps = _mappings(db, conn)
    key_names = set(conn.key_event_names) if conn.key_event_names is not None else None
    names = sorted(set(counts) | set(maps), key=lambda n: (-counts.get(n, (0, 0))[0], n))
    events = []
    for n in names:
        m = maps.get(n)
        role, ctype = STANDARD_EVENTS.get(n, (None, None))
        events.append({
            "event_name": n, "event_count": counts.get(n, (0, 0))[0],
            "seen_in_range": n in counts,
            "is_key_event": None if key_names is None else n in key_names,
            "automatic": n in AUTO_EVENTS,
            "mapping": {"role": m.role, "conversion_type": m.conversion_type, "label": m.label,
                        "counts_revenue": m.counts_revenue} if m else None,
            "suggestion": ({"role": role, "conversion_type": ctype, "counts_revenue": n == "purchase"}
                           if role and not m else None),
        })
    standard = [{"event_name": n, "role": r, "conversion_type": t,
                 "status": "collected" if n in counts else "not_seen"}
                for n, (r, t) in STANDARD_EVENTS.items()]
    return {
        "synced": conn.events_synced_at is not None, "events_synced_at": conn.events_synced_at,
        "key_events_known": key_names is not None,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "events": events, "standard_events": standard,
        "conversion_definition": conversion_definition(db, conn),
        "note": ("These are the events your website already sends to GA4. AEOMirror adds no "
                 "tracking; an event that is 'not seen' must be added on the website (or in "
                 "Google Tag Manager) before it can be measured."),
    }


# ------------------------------------------------------------------ intelligence

def _group_of(cls: str) -> str:
    return "ai" if cls in AI_ASSISTANT_LABELS else cls


def build_intelligence(db: Session, conn: GoogleAnalyticsConnection, start: date, end: date,
                       now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    sk, ek = _key(start), _key(end)
    from app.services.integrations.classification import traffic_class

    # ---- sessions by class (traffic report) ----
    traffic = (db.query(Ga4TrafficSnapshot)
               .filter(Ga4TrafficSnapshot.connection_id == conn.id,
                       Ga4TrafficSnapshot.date >= sk, Ga4TrafficSnapshot.date <= ek).all())
    by_class = {c: {"sessions": 0, "engaged_sessions": 0, "views": 0, "revenue": None} for c in TRAFFIC_CLASSES}
    daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    revenue_rows = 0
    for r in traffic:
        c = traffic_class(r.channel_grouping, r.source, r.medium, r.ai_source_label)
        b = by_class[c]
        b["sessions"] += r.sessions; b["engaged_sessions"] += r.engaged_sessions; b["views"] += r.views
        if r.revenue is not None:
            revenue_rows += 1
            b["revenue"] = (b["revenue"] or 0.0) + r.revenue
        daily[r.date][_group_of(c)] += r.sessions
    total_sessions = sum(b["sessions"] for b in by_class.values())

    # ---- conversions by class (events report, chosen events only) ----
    definition = conversion_definition(db, conn)
    conv_by_class: dict[str, int] = defaultdict(int)
    conv_by_type: dict[str, int] = defaultdict(int)
    conv_revenue_by_class: dict[str, float] = defaultdict(float)
    funnel_counts: dict[str, int] = defaultdict(int)
    events_measured = conn.events_synced_at is not None
    if events_measured:
        ev_rows = (db.query(Ga4EventSnapshot.event_name, Ga4EventSnapshot.traffic_class,
                            func.sum(Ga4EventSnapshot.event_count), func.sum(Ga4EventSnapshot.revenue))
                   .filter(Ga4EventSnapshot.connection_id == conn.id,
                           Ga4EventSnapshot.date >= sk, Ga4EventSnapshot.date <= ek)
                   .group_by(Ga4EventSnapshot.event_name, Ga4EventSnapshot.traffic_class).all())
        conv_events = set(definition["events"])
        for name, cls, n, rev in ev_rows:
            n = int(n or 0)
            if name in FUNNEL:
                funnel_counts[name] += n
            if name in conv_events:
                conv_by_class[cls] += n
                conv_by_type[definition["types"].get(name) or "custom"] += n
                if name in definition["revenue_events"] and rev is not None:
                    conv_revenue_by_class[cls] += float(rev)
    conversions_measured = events_measured and definition["mode"] != "none"
    total_conversions = sum(conv_by_class.values()) if conversions_measured else None

    currency = conn.currency_code
    revenue_available = bool(currency) and revenue_rows > 0

    def row_for(c, sessions, engaged, conversions, revenue):
        return {
            "class": c, "label": CLASS_LABEL.get(c, c), "sessions": sessions,
            "share_pct": _pct(sessions, total_sessions),
            "engagement_rate_pct": _pct(engaged, sessions),
            "conversions": conversions if conversions_measured else None,
            "conversions_per_100_sessions": (_pct(conversions, sessions, 2) if conversions_measured else None),
            "revenue": round(revenue, 2) if revenue_available and revenue is not None else None,
        }

    comparison = [row_for(c, by_class[c]["sessions"], by_class[c]["engaged_sessions"],
                          conv_by_class.get(c, 0), by_class[c]["revenue"])
                  for c in TRAFFIC_CLASSES
                  if by_class[c]["sessions"] or conv_by_class.get(c)]
    comparison.sort(key=lambda r: (-r["sessions"], r["class"]))
    ai_sessions = sum(by_class[c]["sessions"] for c in AI_ASSISTANT_LABELS)
    ai_engaged = sum(by_class[c]["engaged_sessions"] for c in AI_ASSISTANT_LABELS)
    ai_conv = sum(conv_by_class.get(c, 0) for c in AI_ASSISTANT_LABELS)
    ai_rev = (sum(by_class[c]["revenue"] or 0.0 for c in AI_ASSISTANT_LABELS)
              if any(by_class[c]["revenue"] is not None for c in AI_ASSISTANT_LABELS) else None)
    groups = {
        "ai": row_for("ai", ai_sessions, ai_engaged, ai_conv, ai_rev) | {"label": "All AI assistants"},
        "organic_search": row_for("organic_search", by_class["organic_search"]["sessions"],
                                  by_class["organic_search"]["engaged_sessions"],
                                  conv_by_class.get("organic_search", 0), by_class["organic_search"]["revenue"]),
    }

    # ---- landing pages (key events = GA4 key events, per page) ----
    landing = (db.query(Ga4LandingPageSnapshot)
               .filter(Ga4LandingPageSnapshot.connection_id == conn.id,
                       Ga4LandingPageSnapshot.date >= sk, Ga4LandingPageSnapshot.date <= ek).all())
    pages: dict[str, dict] = {}
    unsplit = 0
    for r in landing:
        p = pages.setdefault(r.landing_page, {"landing_page": r.landing_page, "sessions": 0, "ai_sessions": 0,
                                              "organic_sessions": 0, "engaged_sessions": 0, "key_events": 0,
                                              "revenue": None})
        p["sessions"] += r.sessions; p["engaged_sessions"] += r.engaged_sessions; p["key_events"] += r.key_events
        if r.traffic_class is None:
            unsplit += r.sessions
        elif r.traffic_class in AI_ASSISTANT_LABELS:
            p["ai_sessions"] += r.sessions
        elif r.traffic_class == "organic_search":
            p["organic_sessions"] += r.sessions
        if r.revenue is not None and revenue_available:
            p["revenue"] = round((p["revenue"] or 0.0) + r.revenue, 2)
    top = sorted(pages.values(), key=lambda p: (-p["sessions"], p["landing_page"]))[:15]
    for p in top:
        p["engagement_rate_pct"] = _pct(p["engaged_sessions"], p["sessions"])
        p["key_events_per_100_sessions"] = _pct(p["key_events"], p["sessions"], 2)

    # ---- freshness / coverage / quality ----
    earliest = (db.query(func.min(Ga4TrafficSnapshot.date))
                .filter(Ga4TrafficSnapshot.connection_id == conn.id).scalar())
    last = conn.last_synced_at
    lag_from = max(start, end - timedelta(days=PROCESSING_LAG_DAYS - 1))

    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "currency": currency,
        "overview": {
            "total_sessions": total_sessions, "ai_sessions": ai_sessions,
            "ai_share_pct": _pct(ai_sessions, total_sessions),
            "organic_search_sessions": by_class["organic_search"]["sessions"],
            "total_conversions": total_conversions,
            "daily": [{"date": d, **{g: v.get(g, 0) for g in ("ai", "organic_search")},
                       "other": sum(n for g, n in v.items() if g not in ("ai", "organic_search"))}
                      for d, v in sorted(daily.items())],
        },
        "conversions": {
            "measured": conversions_measured,
            "status": ("not_synced" if not events_measured else
                       "not_configured" if definition["mode"] == "none" else "available"),
            "definition": definition, "total": total_conversions,
            "by_type": dict(conv_by_type) if conversions_measured else {},
            "groups": groups,
            "funnel": ([{"event_name": n, "event_count": funnel_counts.get(n, 0)} for n in FUNNEL]
                       if events_measured and any(funnel_counts.values()) else None),
            "revenue_status": ("available" if revenue_available else
                               "no_currency" if revenue_rows and not currency else "not_reported"),
        },
        "landing_pages": {"pages": top, "source_split_complete": unsplit == 0,
                          "unsplit_sessions": unsplit},
        "comparison": comparison,
        "freshness": {
            "last_synced_at": last, "events_synced_at": conn.events_synced_at,
            "landing_pages_synced_at": conn.landing_pages_synced_at,
            "stale": bool(last and now - last > timedelta(hours=STALE_AFTER_HOURS)),
            "covered_from": f"{earliest[:4]}-{earliest[4:6]}-{earliest[6:]}" if earliest else None,
            "partial_range": bool(earliest and earliest > sk),
            "processing_lag_from": lag_from.isoformat() if lag_from <= end else None,
            "data_quality": conn.data_quality or {},
        },
        "definitions": {
            "attribution": ("Session-level: each conversion is credited to the source that started the "
                            "session it happened in. GA4 provides aggregated data only, so there is no "
                            "user-level journey across sessions."),
            "conversions": "Event count of the events chosen as conversions (a session with two purchases counts twice).",
            "conversions_per_100_sessions": "Conversions ÷ sessions from the same source and period × 100.",
            "share_pct": "Sessions from the source ÷ all sessions in the period × 100.",
            "ai_traffic": ("Sessions whose referrer was a recognised AI assistant. Google AI Overviews "
                           "clicks look like ordinary Google organic clicks in GA4 and are counted as organic search."),
            "revenue": "GA4 totalRevenue in the property's currency, attributed to the session's source.",
            "landing_key_events": "Landing-page conversions are GA4 key events (the page report has no per-event split).",
        },
    }
