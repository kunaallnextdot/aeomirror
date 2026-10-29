"""Website Intelligence Command Center — one website's (Monitor's) evidence, gathered
from features that already exist, never recomputed or invented here:

  technical      latest AEOMirror scan (Monitor.latest_scan_id) + reports.engine
                 recommendations
  ai_visibility  latest real Answer Tracking run (run_mode="provider_tracking"; the
                 Answer Simulator shares the prompt set and is excluded)
  traffic        first-party tracker (services/analytics/aggregation)
  ga4            the org's GA4 connection, ONLY when explicitly linked to this site
  conversions    whether any measurement source is configured
  action_center  open URL Intelligence findings from the latest completed crawl

Every indicator carries a `status` from STATUSES, its `source`, and an `as_of`
timestamp. Every finding is built only from values present in those indicators, and
each sentence in it is typed: observed (read from a source), calculated (a stated
formula over observed values) or recommendation. Nothing here makes a causal claim;
when evidence is missing the finding is simply not produced and the indicator says
why. Keep AI *referral traffic* (visits) and AI *answer visibility* (mentions in
tracked answers) separate — they measure different things.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from app.db.models import (
    AI_SOURCE_LABELS, CRAWL_COMPLETED, EXTRACTION_COMPLETE, FINDING_IN_PROGRESS, FINDING_OPEN,
    GA4_STATUS_REVOKED, RUN_COMPLETED, RUN_PARTIAL, AnalyticsSession, CrawlJob, Ga4TrafficSnapshot,
    GoogleAnalyticsConnection, Monitor, PromptRun, PromptSet, Scan, UrlFinding,
)
from app.reports.engine import build_recommendations
from app.services.analytics import aggregation

log = logging.getLogger("app.command_center")

STATUSES = ("available", "zero", "unavailable", "not_configured", "insufficient_data",
            "stale", "partial", "error")

# Freshness policies (also reported to the UI so the rule is visible, not hidden).
SCAN_STALE_DAYS = 30
ANSWER_RUN_STALE_DAYS = 30
GA4_STALE_HOURS = 48          # syncs run every 24h (settings.ga4_sync_interval_hours)
TRACKING_INACTIVE_DAYS = 7    # mirrors routes_tracking._STALE_AFTER_DAYS

# Minimum evidence for a finding (per analysis, not one global threshold).
MIN_SESSIONS_FOR_TREND = 20       # each period, for a period-over-period change
MIN_CHANGE_PCT_FOR_TREND = 20.0
MIN_ANSWER_SAMPLES_HIGH_CONF = 20
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
# Named AI assistants only. "other_identifiable" is a non-AI referrer and
# "unattributable" includes Google organic search — neither is AI traffic.
_GA4_AI_LABELS = tuple(l for l in AI_SOURCE_LABELS if l not in ("other_identifiable", "unattributable"))


def _iso(dt):
    return dt.isoformat() if dt else None


def _age_days(dt):
    return (datetime.utcnow() - dt).days if dt else None


def _pct_change(cur, prev):
    """None (never a fabricated number) when there's no valid previous value."""
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def _first_fix(rec: dict) -> str:
    fix = (rec.get("fix_template") or {}).get("recommended_fix")
    if isinstance(fix, list):
        return str(fix[0]) if fix else ""
    return str(fix or "")


STATEMENT_TYPES = ("observed", "calculated", "hypothesis", "recommendation", "unavailable")

# Every derived number a finding states comes from one of these. The validator
# recomputes each one from its declared inputs, so a finding can't carry a result
# its inputs don't produce.
FORMULAS = {
    "pct_change": {
        "expression": "(current − previous) ÷ previous × 100",
        "denominator": "previous", "constants": (100,),
        "fn": lambda i: round((i["current"] - i["previous"]) / i["previous"] * 100, 1),
    },
    "share_pct": {
        "expression": "part ÷ whole × 100",
        "denominator": "whole", "constants": (100,),
        "fn": lambda i: round(i["part"] / i["whole"] * 100, 1),
    },
}

# Observed and calculated statements report what the data shows; they never assert
# a cause. (A hypothesis may suggest one, and is labeled as unproven.)
_CAUSAL = re.compile(r"\b(caus(e|ed|es|ing)|because of|due to|result(ed|s)? in|led to|drove|driven by)\b", re.I)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(T[\d:.]+)?")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text) -> set[float]:
    """Absolute numeric values in a text, ISO dates removed (direction is carried
    by words like up/down, so −50% and "down 50%" are the same number)."""
    text = _DATE.sub(" ", str(text))
    return {round(abs(float(n.replace(",", ""))), 4) for n in _NUMBER.findall(text)}


def calculation(formula: str, **inputs) -> dict | None:
    """A reproducible calculation block, or None when the denominator is zero or an
    input is missing — never a manufactured number."""
    spec = FORMULAS[formula]
    if any(v is None for v in inputs.values()) or not inputs.get(spec["denominator"]):
        return None
    return {"formula": formula, "expression": spec["expression"], "inputs": inputs,
            "denominator": spec["denominator"], "result": spec["fn"](inputs)}


def _allowed_numbers(f: dict) -> set[float]:
    allowed: set[float] = set()
    for e in f.get("evidence") or []:
        allowed |= _numbers(e.get("value"))
    if (f.get("sample") or {}).get("size") is not None:
        allowed |= _numbers(f["sample"]["size"])
    calc = f.get("calculation")
    if calc:
        for v in list(calc.get("inputs", {}).values()) + [calc.get("result")]:
            allowed |= _numbers(v)
        allowed |= {float(c) for c in FORMULAS.get(calc.get("formula"), {}).get("constants", ())}
    for s in f.get("statements") or []:
        if s.get("quote"):            # verbatim source text is itself evidence
            allowed |= _numbers(s.get("text"))
    return allowed


def validation_errors(f: dict) -> list[str]:
    """Why a finding may not be shown (empty = valid). A finding needs: evidence
    items that each name a source and a value; at least one observed statement;
    every statement typed; a calculation block that reproduces exactly whenever a
    calculated statement exists; no number in the title or statements that isn't in
    its evidence, sample, calculation or quoted source text; and no causal claim
    in an observed or calculated statement."""
    errors = []
    statements = f.get("statements") or []
    evidence = f.get("evidence") or []
    if not evidence:
        errors.append("no evidence")
    if any(not e.get("source") or e.get("value") in (None, "") for e in evidence):
        errors.append("evidence item without source or value")
    if not any(s.get("type") == "observed" and s.get("text") for s in statements):
        errors.append("no observed statement")
    if any(s.get("type") not in STATEMENT_TYPES for s in statements):
        errors.append("untyped statement")
    calc = f.get("calculation")
    if any(s.get("type") == "calculated" for s in statements):
        spec = FORMULAS.get((calc or {}).get("formula"))
        if not spec:
            errors.append("calculated statement without a known formula")
        else:
            try:
                if spec["fn"](calc["inputs"]) != calc["result"]:
                    errors.append("calculation does not reproduce")
            except (KeyError, TypeError, ZeroDivisionError):
                errors.append("calculation inputs invalid")
    allowed = _allowed_numbers(f)
    texts = [f.get("title", "")] + [s.get("text", "") for s in statements
                                     if s.get("type") in ("observed", "calculated") and not s.get("quote")]
    stray = set().union(*(_numbers(t) for t in texts)) - allowed
    if stray:
        errors.append(f"ungrounded numbers: {sorted(stray)}")
    if any(_CAUSAL.search(s.get("text", "")) for s in statements
           if s.get("type") in ("observed", "calculated")):
        errors.append("causal claim in an observed/calculated statement")
    return errors


def valid_finding(f: dict) -> bool:
    return not validation_errors(f)


def validate_narrative(text: str, finding: dict) -> list[str]:
    """The gate for any generated (e.g. LLM-written) explanation of a finding: it
    may only restate numbers the validated finding already carries, and may not
    assert a cause. Returns the problems; empty means safe to show. Nothing in the
    product generates narratives today — this exists so one can't bypass the rule."""
    errors = []
    stray = _numbers(text) - _allowed_numbers(finding)
    if stray:
        errors.append(f"ungrounded numbers: {sorted(stray)}")
    if _CAUSAL.search(text or ""):
        errors.append("causal claim")
    return errors


# ------------------------------------------------------------------ indicators

def _technical(db: DbSession, org_id: str, monitor: Monitor) -> tuple[dict, list[dict]]:
    scan = db.get(Scan, monitor.latest_scan_id) if monitor.latest_scan_id else None
    if scan is None or scan.organization_id != org_id or not scan.result:
        return {"status": "not_configured", "source": "aeomirror_scan", "as_of": None,
                "reason": "This website hasn't been audited yet."}, []
    sections = scan.result.get("sections") or []
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for s in sections:
        if s.get("status") in counts:
            counts[s["status"]] += 1
    age = _age_days(scan.created_at)
    ind = {
        "status": "stale" if age is not None and age > SCAN_STALE_DAYS else "available",
        "source": "aeomirror_scan", "as_of": _iso(scan.created_at), "age_days": age,
        "scan_id": scan.id, "url": scan.url,
        "score": scan.result.get("overall_score"),
        "score_definition": ("AI Readiness Score: weighted aggregate of the 10 technical "
                             "and content signals in this audit (0–100). It measures what AI "
                             "crawlers can reach and read — not traffic or AI answer visibility."),
        "signals": counts, "issue_count": sum(len(s.get("issues") or []) for s in sections),
    }
    findings = []
    for rec in build_recommendations(sections, monitor.normalized_url or "", url=scan.url)[:3]:
        if rec["priority"] not in ("Critical", "High"):
            continue
        issues = (rec.get("evidence") or {}).get("issues") or []
        findings.append({
            "id": f"tech:{scan.id}:{rec['id']}", "category": "technical_seo",
            "title": f"{rec['signal_label']}: {rec['issue_title']}",
            "priority": "high" if rec["priority"] == "Critical" else "medium",
            "priority_rationale": (f"Audit rated this {rec['priority']} (signal score "
                                   f"{rec['score']}/100, weight {rec['weight']})."),
            "statements": [
                {"type": "observed", "text": f"The {ind['as_of'][:10]} audit scored {rec['signal_label']} "
                                             f"{rec['score']}/100 ({rec['status']})."},
                *[{"type": "observed", "text": i, "quote": True} for i in issues[:2]],
                {"type": "recommendation", "text": _first_fix(rec)},
            ],
            "evidence": [{"label": "Signal score", "value": f"{rec['score']}/100", "source": "aeomirror_scan"},
                         {"label": "Signal", "value": f"{rec['signal_label']}: {rec['issue_title']}",
                          "source": "aeomirror_scan"}],
            "sample": None, "confidence": rec.get("confidence") or "high",
            "date_range": {"as_of": ind["as_of"]},
            "link": f"/app/scans/{scan.id}",
        })
    for f in findings:
        f["statements"] = [s for s in f["statements"] if s["text"]]
    return ind, findings


def _latest_real_answer_run(db: DbSession, org_id: str, monitor: Monitor) -> PromptRun | None:
    return (db.query(PromptRun)
            .filter(PromptRun.organization_id == org_id, PromptRun.monitor_id == monitor.id,
                    PromptRun.run_mode == "provider_tracking",
                    PromptRun.status.in_((RUN_COMPLETED, RUN_PARTIAL)),
                    PromptRun.extraction_status == EXTRACTION_COMPLETE)
            .order_by(PromptRun.created_at.desc()).first())


def _ai_visibility(db: DbSession, org_id: str, monitor: Monitor) -> tuple[dict, list[dict]]:
    from app.services.answer_tracking.aggregation import run_metrics
    has_set = db.query(PromptSet.id).filter(PromptSet.organization_id == org_id,
                                            PromptSet.monitor_id == monitor.id).first()
    run = _latest_real_answer_run(db, org_id, monitor)
    if run is None:
        return {"status": "not_configured", "source": "answer_tracking", "as_of": None,
                "reason": ("No completed Answer Tracking run for this website yet."
                           if has_set else "Answer Tracking isn't set up for this website."),
                "link": f"/app/answer-tracking/{monitor.id}"}, []
    m = run_metrics(db, run)
    analyzed = m.get("analyzed_count") or 0
    when = run.completed_at or run.created_at
    age = _age_days(when)
    providers = [p["provider"] for p in m.get("per_provider") or []]
    status = ("insufficient_data" if analyzed == 0
              else "stale" if age is not None and age > ANSWER_RUN_STALE_DAYS
              else "partial" if run.status == RUN_PARTIAL else "available")
    prompts = m.get("per_prompt") or []
    ind = {
        "status": status, "source": "answer_tracking", "as_of": _iso(when), "age_days": age,
        "run_id": run.id, "providers": providers, "query_count": len(prompts),
        "samples_analyzed": analyzed,
        "excluded_extraction_failures": m.get("excluded_extraction_failures"),
        "mention_rate_pct": m.get("mention_rate"), "mentions": m.get("mentions"),
        "citation_count": m.get("citation_count"),
        "definition": ("Share of analyzed answers from the tracked queries, on the listed AI "
                       "platforms, that mention this brand. A sample of tracked queries — not "
                       "all AI answers."),
        "link": f"/app/answer-tracking/{monitor.id}/runs/{run.id}",
    }
    findings = []
    gaps = [p for p in prompts if p.get("is_gap") and (p.get("samples") or 0) > 0]
    if analyzed and gaps:
        low = analyzed < MIN_ANSWER_SAMPLES_HIGH_CONF
        findings.append({
            "id": f"aiv:{run.id}:gaps", "category": "ai_visibility",
            "title": f"Not mentioned in {len(gaps)} of {len(prompts)} tracked AI queries",
            "priority": "high" if len(gaps) * 2 >= len(prompts) else "medium",
            "priority_rationale": (f"Absent from {len(gaps)}/{len(prompts)} tracked queries "
                                   "(high when absent from at least half)."),
            "statements": [
                {"type": "observed", "text": (f"In the run completed {ind['as_of'][:10]} on "
                                              f"{', '.join(providers) or 'the tracked platforms'}, "
                                              f"{len(gaps)} tracked queries produced no mention of this brand.")},
                *[{"type": "observed", "text": f"“{g['text']}”", "quote": True} for g in gaps[:3] if g.get("text")],
                {"type": "recommendation", "text": "Review these queries in Answer Tracking to see who was recommended instead."},
                *([{"type": "unavailable", "text": (f"{analyzed} answers is a small sample; it shows these tracked "
                                                     "queries only, not AI answers in general.")}] if low else []),
            ],
            "evidence": [{"label": "Queries with no mention", "value": f"{len(gaps)}/{len(prompts)}", "source": "answer_tracking"},
                         {"label": "Answers analyzed", "value": str(analyzed), "source": "answer_tracking"},
                         {"label": "Platforms", "value": ", ".join(providers) or "—", "source": "answer_tracking"}],
            "sample": {"size": analyzed, "unit": "answers"},
            "confidence": "low" if low else "medium",
            "date_range": {"as_of": ind["as_of"]},
            "link": ind["link"],
        })
    return ind, findings


def _traffic(db: DbSession, org_id: str, monitor: Monitor, start, end, prev_start, prev_end,
             is_pro: bool) -> tuple[dict, dict, list[dict]]:
    first_seen = (db.query(func.min(AnalyticsSession.started_at))
                  .filter(AnalyticsSession.monitor_id == monitor.id).scalar())
    last_seen = (db.query(func.max(AnalyticsSession.last_seen_at))
                 .filter(AnalyticsSession.monitor_id == monitor.id).scalar())
    ids = [monitor.id]
    conv_configured = aggregation.conversion_tracking_configured(db, org_id, ids)
    if first_seen is None:
        return ({"status": "not_configured", "source": "first_party", "as_of": None,
                 "reason": "The AEOMirror tracking snippet hasn't recorded a visit on this website yet."},
                {"first_party": "not_configured", "configured": conv_configured}, [])

    cur = aggregation.summary(db, org_id=org_id, monitor_ids=ids, start=start, end=end)
    # The previous period is only comparable if tracking covered all of it.
    prev_comparable = first_seen <= prev_start
    prev = (aggregation.summary(db, org_id=org_id, monitor_ids=ids, start=prev_start, end=prev_end)
            if prev_comparable else None)
    inactive = (datetime.utcnow() - last_seen) > timedelta(days=TRACKING_INACTIVE_DAYS) if last_seen else True
    status = ("stale" if inactive else "zero" if cur["total_sessions"] == 0 else "available")

    def pick(s):
        if s is None:
            return None
        keys = ("total_sessions", "total_page_views", "unique_visitors", "ai_sessions",
                "ai_unique_visitors", "ai_share_pct")
        return {k: s[k] for k in keys}

    ind = {
        "status": status, "source": "first_party", "as_of": _iso(last_seen),
        "first_event_at": _iso(first_seen),
        "current": pick(cur), "previous": pick(prev),
        "previous_comparable": prev_comparable,
        "change_pct": {k: _pct_change(cur[k], prev[k]) for k in ("total_sessions", "ai_sessions")} if prev else None,
        "change_note": (
            f"Tracking started {first_seen.date().isoformat()}, after the previous period began, so there's "
            "no complete previous period to compare against." if not prev_comparable
            else "The previous period had 0 sessions, so a percentage change can't be calculated."
            if prev["total_sessions"] == 0 else None),
        "provenance": {
            "kind": "measured", "scope": f"website {monitor.normalized_url}",
            "query": "AEOMirror sessions with started_at in [start, end) for this website",
            "aggregation": ("Sessions and page views are counts; unique visitors are distinct visitor "
                            "IDs over the whole range, never summed by day."),
        },
        "definition": ("Visits recorded by the AEOMirror tracking snippet on this website. Counts "
                       "can differ from GA4: separate tags, consent and ad-blocker behavior, and "
                       "different session rules."),
    }
    conv = {"first_party": "configured" if conv_configured else "not_configured",
            "configured": conv_configured}
    if is_pro and conv_configured:
        conv.update(confirmed_ai_conversions=cur["ai_conversions"],
                    ai_conversion_rate_pct=cur["ai_conversion_rate_pct"])

    findings = []
    if prev and cur["total_sessions"] >= MIN_SESSIONS_FOR_TREND and prev["total_sessions"] >= MIN_SESSIONS_FOR_TREND:
        calc = calculation("pct_change", current=cur["total_sessions"], previous=prev["total_sessions"])
        ch = calc["result"] if calc else None
        if ch is not None and abs(ch) >= MIN_CHANGE_PCT_FOR_TREND:
            findings.append({
                "id": f"traffic:{monitor.id}:sessions", "category": "acquisition_engagement",
                "title": f"Website sessions {'up' if ch > 0 else 'down'} {abs(ch)}% vs the previous period",
                "priority": "high" if ch <= -30 else "medium" if ch < 0 else "low",
                "priority_rationale": "Declines of 30%+ rank high; smaller declines medium; increases low.",
                "statements": [
                    {"type": "observed", "text": f"{cur['total_sessions']} sessions this period; {prev['total_sessions']} in the previous equal-length period."},
                    {"type": "calculated", "text": f"Change = ({cur['total_sessions']} − {prev['total_sessions']}) ÷ {prev['total_sessions']} = {ch}%."},
                    {"type": "recommendation", "text": "Compare channels and landing pages between the two periods to see where the change happened."},
                ],
                "evidence": [{"label": "Sessions (current / previous)", "value": f"{cur['total_sessions']} / {prev['total_sessions']}", "source": "first_party"}],
                "calculation": calc,
                "sample": {"size": cur["total_sessions"] + prev["total_sessions"], "unit": "sessions"},
                "confidence": "medium",
                "date_range": {"start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat(),
                               "previous_start": prev_start.date().isoformat(),
                               "previous_end": (prev_end - timedelta(days=1)).date().isoformat()},
                "link": None,
            })
    share = calculation("share_pct", part=cur["ai_sessions"], whole=cur["total_sessions"])
    if cur["ai_sessions"] and share:
        findings.append({
            "id": f"traffic:{monitor.id}:ai", "category": "ai_referral",
            "title": f"{cur['ai_sessions']} visits came from identifiable AI assistants",
            "priority": "low",
            "priority_rationale": "Informational: confirms measurable AI referral traffic.",
            "statements": [
                {"type": "observed", "text": (f"{cur['ai_sessions']} of {cur['total_sessions']} sessions arrived "
                                              "with a recognized AI-assistant referrer.")},
                {"type": "calculated", "text": f"AI traffic share = {cur['ai_sessions']} ÷ {cur['total_sessions']} × 100 = {share['result']}%."},
            ],
            "evidence": [{"label": "AI-referred sessions", "value": str(cur["ai_sessions"]), "source": "first_party"}],
            "calculation": share,
            "sample": {"size": cur["total_sessions"], "unit": "sessions"},
            "confidence": "high",
            "date_range": {"start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat()},
            "link": None,
        })
    if not conv_configured and cur["total_sessions"] > 0:
        findings.append({
            "id": f"conv:{monitor.id}:unmeasured", "category": "conversion_measurement",
            "title": "Conversions aren't being measured on this website",
            "priority": "medium",
            "priority_rationale": "Without confirmed conversions, no page or channel can be judged on business outcome.",
            "statements": [
                {"type": "observed", "text": "No server-confirmed conversion has ever been recorded for this website."},
                {"type": "recommendation", "text": ("Generate a tracking API key and confirm purchases, leads or "
                                                    "bookings from your server (Business Impact → setup).")},
            ],
            "evidence": [{"label": "Confirmed conversions ever", "value": "0", "source": "first_party"}],
            "sample": None, "confidence": "high", "date_range": None, "link": None,
        })
    if is_pro:
        pages = aggregation.landing_page_performance(db, org_id=org_id, monitor_ids=ids,
                                                     start=start, end=end, limit=10)
        for p in pages:
            aeo = p.get("aeo")
            if p["sessions"] < aggregation._MIN_SAMPLE_SESSIONS or not aeo or not aeo.get("failing_signals"):
                continue
            findings.append({
                "id": f"page:{monitor.id}:{p['page_key']}", "category": "landing_page",
                "title": f"{p['path']} gets traffic and has failing audit signals",
                "priority": "medium",
                "priority_rationale": "Combines measured reach with an existing audit finding on the same page.",
                "statements": [
                    {"type": "observed", "text": f"{p['sessions']} sessions landed on this page in the period."},
                    {"type": "observed", "text": (f"Its latest audit ({(aeo.get('scanned_at') or '')[:10]}) has failing "
                                                  f"signals: {', '.join(aeo['failing_signals'])}.")},
                    {"type": "recommendation", "text": "Fixing these affects a page people already visit. This is an opportunity, not a measured cause of anything."},
                ],
                "evidence": [{"label": "Page", "value": p["path"], "source": "first_party"},
                             {"label": "Sessions", "value": str(p["sessions"]), "source": "first_party"},
                             {"label": "Audit score", "value": f"{aeo.get('score')}/100", "source": "aeomirror_scan"},
                             {"label": "Failing signals", "value": ", ".join(aeo["failing_signals"]),
                              "source": "aeomirror_scan"}],
                "sample": {"size": p["sessions"], "unit": "sessions"},
                "confidence": "medium" if p["sessions"] < 100 else "high",
                "date_range": {"start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat()},
                "link": f"/app/scans/{aeo['scan_id']}",
            })
            if len([f for f in findings if f["category"] == "landing_page"]) >= 3:
                break
    return ind, conv, findings


def _ga4(db: DbSession, org_id: str, monitor: Monitor, start, end, is_pro: bool) -> dict:
    conn = (db.query(GoogleAnalyticsConnection)
            .filter(GoogleAnalyticsConnection.organization_id == org_id).first())
    if conn is None or not conn.ga4_property_id:
        return {"status": "not_configured", "source": "ga4", "reason": "Google Analytics isn't connected."}
    if not conn.linked_monitor_id:
        return {"status": "not_configured", "source": "ga4", "link_required": True,
                "property_name": conn.ga4_property_name,
                "reason": (f"GA4 property “{conn.ga4_property_name}” isn't linked to a website. "
                           "Link it to show its data here.")}
    if conn.linked_monitor_id != monitor.id:
        return {"status": "unavailable", "source": "ga4", "linked_elsewhere": True,
                "reason": "The connected GA4 property is linked to a different website."}
    if not is_pro:
        return {"status": "unavailable", "source": "ga4", "reason": "Google Analytics data is a Pro feature."}
    if conn.status == GA4_STATUS_REVOKED:
        return {"status": "not_configured", "source": "ga4", "property_name": conn.ga4_property_name,
                "reason": "Google Analytics was disconnected. Reconnect it to show its data here."}
    sync_error = conn.last_sync_error_code if conn.last_sync_status == "error" else None
    if conn.last_synced_at is None:
        if sync_error:
            return {"status": "error", "source": "ga4", "property_name": conn.ga4_property_name,
                    "sync_error_code": sync_error,
                    "reason": f"GA4 sync failed ({sync_error}) before any data was retrieved."}
        return {"status": "not_configured", "source": "ga4", "reason": "GA4 hasn't completed a sync yet.",
                "property_name": conn.ga4_property_name}
    age_h = (datetime.utcnow() - conn.last_synced_at).total_seconds() / 3600
    s, e = start.strftime("%Y%m%d"), (end - timedelta(days=1)).strftime("%Y%m%d")
    snap = Ga4TrafficSnapshot
    earliest = db.query(func.min(snap.date)).filter(snap.connection_id == conn.id).scalar()
    base = {"source": "ga4", "as_of": _iso(conn.last_synced_at), "property_name": conn.ga4_property_name,
            "key_events_configured": conn.key_events_configured, "sync_error_code": sync_error,
            "link": "/app/analytics/ai-traffic",
            "provenance": {
                "kind": "measured", "scope": f"GA4 property linked to {monitor.normalized_url}",
                "query": "Stored daily GA4 runReport rows (date × channel × source × medium) in range",
                "aggregation": ("Sum of daily sessions and keyEvents rows — both are additive across days "
                                "and channels. Users are never summed (not additive)."),
            }}
    if earliest is None:
        return {**base, "status": "insufficient_data",
                "reason": "GA4 has synced but returned no traffic rows for this property yet."}
    if earliest > e:
        return {**base, "status": "unavailable",
                "reason": f"GA4 history synced so far starts {earliest[:4]}-{earliest[4:6]}-{earliest[6:]}, after this period."}
    in_range = (snap.connection_id == conn.id, snap.date >= s, snap.date <= e)
    rows = (db.query(func.coalesce(func.sum(snap.sessions), 0), func.coalesce(func.sum(snap.key_events), 0))
            .filter(*in_range).first())
    ai = (db.query(func.coalesce(func.sum(snap.sessions), 0))
          .filter(*in_range, snap.ai_source_label.in_(_GA4_AI_LABELS)).scalar())
    sessions, key_events = int(rows[0]), int(rows[1])
    partial = earliest > s
    status = ("error" if sync_error
              else "stale" if age_h > GA4_STALE_HOURS
              else "partial" if partial
              else "zero" if sessions == 0 else "available")
    reason = (f"The latest GA4 sync failed ({sync_error}); showing data from the last successful sync."
              if sync_error else
              f"GA4 history starts {earliest[:4]}-{earliest[4:6]}-{earliest[6:]}; earlier days in this period aren't included."
              if partial else None)
    return {
        **base, "status": status, "reason": reason, "covered_from": f"{earliest[:4]}-{earliest[4:6]}-{earliest[6:]}",
        "sessions": sessions, "ai_assistant_sessions": int(ai or 0),
        "key_events": key_events if conn.key_events_configured else None,
    }


def _action_center(db: DbSession, org_id: str, monitor: Monitor) -> tuple[dict, list[dict]]:
    job = (db.query(CrawlJob)
           .filter(CrawlJob.organization_id == org_id, CrawlJob.monitor_id == monitor.id,
                   CrawlJob.phase == CRAWL_COMPLETED)
           .order_by(CrawlJob.created_at.desc()).first())
    if job is None:
        return {"status": "not_configured", "source": "url_intelligence", "as_of": None,
                "reason": "No completed site crawl yet.",
                "link": f"/app/monitoring/{monitor.id}/intelligence"}, []
    rows = (db.query(UrlFinding.priority, func.count(UrlFinding.id))
            .filter(UrlFinding.crawl_job_id == job.id, UrlFinding.organization_id == org_id,
                    UrlFinding.status.in_((FINDING_OPEN, FINDING_IN_PROGRESS)))
            .group_by(UrlFinding.priority).all())
    by_priority = {p or "unknown": n for p, n in rows}
    total = sum(by_priority.values())
    ind = {"status": "zero" if total == 0 else "available", "source": "url_intelligence",
           "as_of": _iso(job.created_at), "crawl_job_id": job.id, "open_by_priority": by_priority,
           "open_total": total, "link": f"/app/monitoring/{monitor.id}/intelligence"}
    urgent = by_priority.get("critical", 0) + by_priority.get("high", 0)
    findings = []
    if urgent:
        findings.append({
            "id": f"ac:{job.id}:urgent", "category": "technical_seo",
            "title": f"{urgent} critical/high site-crawl findings are still open",
            "priority": "high",
            "priority_rationale": "Open findings the site crawl itself rated critical or high.",
            "statements": [
                {"type": "observed", "text": f"The crawl from {ind['as_of'][:10]} has {urgent} open critical/high findings ({total} open in total)."},
                {"type": "recommendation", "text": "Work through them in Site Intelligence and mark each fixed or won't-fix."},
            ],
            "evidence": [{"label": "Open critical / high", "value": f"{by_priority.get('critical', 0)} / {by_priority.get('high', 0)}", "source": "url_intelligence"},
                         {"label": "Open critical + high", "value": str(urgent), "source": "url_intelligence"},
                         {"label": "Open in total", "value": str(total), "source": "url_intelligence"}],
            "sample": None, "confidence": "high", "date_range": {"as_of": ind["as_of"]},
            "link": ind["link"],
        })
    return ind, findings


# ------------------------------------------------------------------ entry point

# How each indicator is measured, for indicators that don't set their own.
_PROVENANCE = {
    "technical": {"kind": "measured", "query": "This website's latest AEOMirror audit (Monitor.latest_scan_id)",
                  "aggregation": "Score is the audit's own weighted aggregate; signal counts are by status."},
    "ai_visibility": {"kind": "calculated",
                      "query": ("Latest completed real-provider Answer Tracking run for this website "
                                "(simulator runs excluded)"),
                      "aggregation": "Mention rate = answers mentioning the brand ÷ answers analyzed × 100."},
    "conversions": {"kind": "measured",
                    "query": "Whether any server-confirmed conversion or GA4 key event is configured"},
    "action_center": {"kind": "measured",
                      "query": "Open/in-progress findings from this website's latest completed crawl",
                      "aggregation": "Count by priority."},
    "ga4": {"kind": "measured", "query": "The organization's GA4 connection and its website link"},
}

def build(db: DbSession, *, org_id: str, monitor: Monitor, start: datetime, end: datetime,
          is_pro: bool) -> dict:
    span = end - start
    prev_end, prev_start = start, start - span
    technical, f_tech = _technical(db, org_id, monitor)
    visibility, f_vis = _ai_visibility(db, org_id, monitor)
    traffic, conversions, f_traffic = _traffic(db, org_id, monitor, start, end, prev_start, prev_end, is_pro)
    ga4 = _ga4(db, org_id, monitor, start, end, is_pro)
    conversions["ga4_key_events_configured"] = ga4.get("key_events_configured")
    conversions["status"] = ("available" if conversions["configured"] or ga4.get("key_events_configured")
                             else "not_configured")
    action, f_ac = _action_center(db, org_id, monitor)

    indicators = {"technical": technical, "ai_visibility": visibility, "traffic": traffic,
                  "ga4": ga4, "conversions": conversions, "action_center": action}
    for key, prov in _PROVENANCE.items():
        indicators[key].setdefault("provenance", prov)

    now = datetime.utcnow()
    findings = []
    for f in f_tech + f_ac + f_vis + f_traffic:
        errors = validation_errors(f)
        if errors:
            # Never shown. Logged with the finding id and reasons only — no metric
            # values or visitor data.
            log.warning("command center: suppressed finding %s: %s", f.get("id"), "; ".join(errors))
            continue
        f.update(website_id=monitor.id, generated_at=_iso(now),
                 verification={"status": "validated",
                               "checks": ["evidence_sourced", "statements_typed", "numbers_grounded",
                                          "calculation_reproduced", "no_causal_claims"]})
        findings.append(f)
    findings.sort(key=lambda f: _PRIORITY_RANK.get(f["priority"], 3))
    return {
        "website": {"id": monitor.id, "name": monitor.name, "domain": monitor.normalized_url, "url": monitor.url},
        "range": {"start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat()},
        "previous_range": {"start": prev_start.date().isoformat(),
                           "end": (prev_end - timedelta(days=1)).date().isoformat()},
        "generated_at": _iso(now),
        "indicators": indicators,
        "findings": findings,
        "freshness_policy": {"scan_stale_days": SCAN_STALE_DAYS, "answer_run_stale_days": ANSWER_RUN_STALE_DAYS,
                             "ga4_stale_hours": GA4_STALE_HOURS, "tracking_inactive_days": TRACKING_INACTIVE_DAYS},
        "is_pro": is_pro,
    }
