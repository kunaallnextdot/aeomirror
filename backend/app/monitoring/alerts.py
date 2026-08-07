"""Alert engine (Phase 7).

Rule-based evaluation of a scan vs the previous one. Change-based alerts (score
drop, robots blocked, sitemap/schema removed, metadata/accessibility/performance
regressions) only fire when there is a previous scan; "critical issue" alerts also
fire on the first scan to surface anything critically wrong from the baseline.
"""
from __future__ import annotations

from app.config import settings
from app.db.models import Alert
from app.monitoring.changes import sections_by_id

# Max "critical issue" alerts to raise from a single scan (avoid first-scan spam).
_MAX_CRITICAL_ISSUE = 3


def _a(atype, severity, title, message, detail):
    return {"type": atype, "severity": severity, "title": title,
            "message": message, "detail": detail}


def _cat_delta(changes: dict, sid: str):
    for c in changes.get("categories", []):
        if c.get("id") == sid:
            return c.get("delta")
    return None


def evaluate_alerts(changes: dict, prev: dict | None, curr: dict) -> list[dict]:
    alerts: list[dict] = []
    curr_s = sections_by_id(curr)
    prev_s = sections_by_id(prev) if prev else {}
    first = changes.get("first_scan", prev is None)

    def sig(store, sid):
        return store.get(sid) or {}

    def ev(store, sid):
        return (sig(store, sid).get("evidence") or {})

    # ---- change-based alerts (need a baseline) ----
    if not first:
        overall = changes.get("overall", {})
        delta = overall.get("delta")
        if delta is not None and delta <= -settings.alert_score_drop_warning:
            sev = "critical" if delta <= -settings.alert_score_drop_critical else "warning"
            alerts.append(_a("score_drop", sev,
                f"AI visibility dropped {abs(delta)} points",
                f"Overall score fell from {overall.get('prev')} to {overall.get('curr')}.",
                {"prev": overall.get("prev"), "curr": overall.get("curr"), "delta": delta}))

        # robots.txt became blocked (an AI crawler newly disallowed, or blanket block)
        cur_bots = ev(curr_s, "robots").get("ai_crawlers") or {}
        prev_bots = ev(prev_s, "robots").get("ai_crawlers") or {}
        newly_blocked = [b for b, st in cur_bots.items()
                         if st == "blocked" and prev_bots.get(b) != "blocked"]
        prev_crawlable = ev(prev_s, "robots").get("crawlable", True)
        cur_crawlable = ev(curr_s, "robots").get("crawlable", True)
        if newly_blocked or (prev_crawlable and not cur_crawlable):
            msg = ("robots.txt now disallows all crawlers." if not cur_crawlable
                   else "Now blocked: " + ", ".join(newly_blocked))
            alerts.append(_a("robots_blocked", "critical",
                "robots.txt now blocks AI crawlers", msg,
                {"newly_blocked": newly_blocked, "crawlable": cur_crawlable}))

        # sitemap removed
        if ev(prev_s, "sitemap").get("present") and not ev(curr_s, "sitemap").get("present"):
            alerts.append(_a("sitemap_removed", "warning", "XML sitemap removed",
                "The sitemap is no longer reachable — discovery of new pages will slow.", {}))

        # schema removed
        prev_blocks = ev(prev_s, "schema").get("blocks", 0) or 0
        cur_blocks = ev(curr_s, "schema").get("blocks", 0) or 0
        if prev_blocks > 0 and cur_blocks == 0:
            alerts.append(_a("schema_removed", "warning", "Structured data removed",
                "JSON-LD schema is no longer present — AI engines lose machine-readable facts.",
                {"prev_blocks": prev_blocks}))

        # metadata problems (regressed to fail)
        if (sig(curr_s, "metadata").get("status") == "fail"
                and sig(prev_s, "metadata").get("status") in ("pass", "warn")):
            alerts.append(_a("metadata_problem", "warning", "Metadata problems detected",
                "Title, description or canonical metadata regressed.",
                {"curr": sig(curr_s, "metadata").get("score")}))

        # accessibility regression
        ad = _cat_delta(changes, "accessibility")
        if ad is not None and ad <= -settings.alert_category_regression:
            alerts.append(_a("accessibility_regression", "warning",
                f"Accessibility dropped {abs(ad)} points",
                "A major accessibility regression can hurt both users and AI extraction.",
                {"delta": ad}))

        # performance degradation
        pd = _cat_delta(changes, "performance")
        if pd is not None and pd <= -settings.alert_category_regression:
            alerts.append(_a("performance_degradation", "warning",
                f"Performance dropped {abs(pd)} points",
                "Slower delivery lets crawlers read less of your site per visit.",
                {"delta": pd}))

    # ---- critical issues (also on first scan; capped) ----
    newly_critical = []
    for sid, cs in curr_s.items():
        c = cs.get("score")
        p = (prev_s.get(sid) or {}).get("score")
        if c is not None and c <= settings.alert_critical_score and (p is None or p > settings.alert_critical_score):
            newly_critical.append((c, sid, cs.get("label")))
    newly_critical.sort()
    for c, sid, label in newly_critical[:_MAX_CRITICAL_ISSUE]:
        alerts.append(_a("critical_issue", "critical", f"Critical issue: {label}",
            f"{label} scored {c}/100 — this is critically low for AI visibility.",
            {"signal": sid, "score": c}))

    return alerts


def persist_alerts(db, monitor, scan_id: str, alert_dicts: list[dict]) -> list[Alert]:
    rows = []
    for a in alert_dicts:
        row = Alert(
            monitor_id=monitor.id, organization_id=monitor.organization_id,
            scan_id=scan_id, type=a["type"], severity=a["severity"],
            title=a["title"], message=a["message"], detail=a["detail"],
        )
        db.add(row)
        rows.append(row)
    if rows:
        db.commit()
        for r in rows:
            db.refresh(r)
    return rows
