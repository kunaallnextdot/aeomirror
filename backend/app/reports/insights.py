"""Read-side AEO insights (Phase 1 — negative-first).

Pure, deterministic derivations over a stored scan's signal `sections`. No LLM, no
external calls, and — critically — NO change to `overall_score` or the scanner. Every
"problem" surfaced here is drawn from the scanner's own score / weight / issues /
evidence; nothing is invented, and no score is ever re-derived or written back.

The weighted-loss identity mirrors the aggregate scorer exactly. A signal with weight
``w`` and score ``s`` costs the overall score::

    points_lost = w * (100 - s) / 100

because ``overall = Σ(score·weight) / Σweight`` and the weights sum to 100. Restoring a
signal to a perfect 100 therefore recovers *exactly* its ``points_lost`` — which is why
the projection below is an exact arithmetic estimate, not a guess. It is still labelled
an ESTIMATE (assumes a full fix) and never mutates the real score.
"""
from __future__ import annotations

import re

from app.scanner.signals.base import GOOD, status_from_score


def _severity(score: float) -> str:
    """How bad the current state of a signal is. Mirrors reports.engine._severity so
    the two never diverge in the UI."""
    if score < 25:
        return "Critical"
    if score < 45:
        return "High"
    if score < 60:
        return "Medium"
    return "Low"


def points_lost(weight: float, score: float) -> float:
    """Weighted overall-score points this signal is currently costing (0 when perfect)."""
    return round((weight or 0) * (100 - (score or 0)) / 100.0, 2)


def _derive_overall(sections: list[dict]) -> int:
    """Weighted overall from sections — only used when the scan didn't store one
    (older rows). Mirrors reports.engine.build_scorecard so numbers match."""
    wsum = sum((s.get("weight") or 0) for s in sections) or 1
    return round(sum((s.get("score") or 0) * (s.get("weight") or 0) for s in sections) / wsum)


def score_loss_breakdown(sections: list[dict]) -> list[dict]:
    """Per-signal score-loss, worst-first. Each row preserves the scanner's own
    score / weight / issues / evidence verbatim (evidence is never fabricated)."""
    rows = []
    for s in sections or []:
        score = s.get("score", 0) or 0
        weight = s.get("weight", 0) or 0
        issues = list(s.get("issues") or [])
        rows.append({
            "signal_id": s.get("id", ""),
            "label": s.get("label") or s.get("id", ""),
            "score": score,
            "weight": weight,
            "points_lost": points_lost(weight, score),
            "status": s.get("status") or status_from_score(score),
            "severity": _severity(score),
            "issues": issues,
            "issue_count": len(issues),
            "evidence": s.get("evidence") or {},
        })
    # Biggest loss first; ties broken by the heavier-weighted signal.
    rows.sort(key=lambda r: (-r["points_lost"], -r["weight"]))
    return rows


def _explain(row: dict) -> str:
    """Deterministic one-liner for a single problem signal (no LLM)."""
    return (
        f"{row['label']} carries {row['weight']} of your 100 points and scored "
        f"{row['score']}/100, so it's costing you about {row['points_lost']} points."
    )


def why_score_low(sections: list[dict], recommendations: list[dict] | None = None,
                  limit: int = 3) -> list[dict]:
    """The biggest contributors to score loss — the answer to 'Why is my score low?'.

    Only signals that ACTUALLY lose points are returned (points_lost > 0), so a healthy
    site yields an empty list rather than an invented problem. For each, we surface the
    real issue + evidence + a short deterministic explanation + a recommendation preview
    drawn from the engine's recommendation for that signal (never a generated claim)."""
    recs_by_id = {r.get("id"): r for r in (recommendations or [])}
    problems = []
    for row in score_loss_breakdown(sections):
        if row["points_lost"] <= 0:
            continue                       # a signal that costs nothing is not a problem
        rec = recs_by_id.get(row["signal_id"])
        issue = (row["issues"][0] if row["issues"]
                 else f"Scored {row['score']}/100 — below a healthy AI-visibility level.")
        preview = None
        if rec:
            preview = rec.get("description") or (rec.get("fix_template") or {}).get("problem")
        problems.append({
            "signal_id": row["signal_id"],
            "signal": row["label"],
            "score": row["score"],
            "weight": row["weight"],
            "points_lost": row["points_lost"],
            "status": row["status"],
            "severity": row["severity"],
            "issue": issue,
            "issues": row["issues"],
            "evidence": row["evidence"],
            "explanation": _explain(row),
            "recommendation_preview": preview,
        })
        if len(problems) >= limit:
            break
    return problems


def projected_recovery(sections: list[dict], overall_score: float | None,
                       signal_ids=None) -> dict:
    """Deterministic score-recovery projection — an ESTIMATE, never a re-score.

    Assumes each considered signal is fixed to a healthy state and recovers exactly its
    currently-lost weighted points. `signal_ids` limits which signals are assumed fixed
    (e.g. only the top problems); None = every signal that is currently losing points.
    Never writes back into overall_score."""
    considered, total = [], 0.0
    for row in score_loss_breakdown(sections):
        if row["points_lost"] <= 0:
            continue
        if signal_ids is not None and row["signal_id"] not in signal_ids:
            continue
        considered.append({"signal_id": row["signal_id"], "label": row["label"],
                           "points_recoverable": row["points_lost"]})
        total += row["points_lost"]
    cur = round(overall_score if overall_score is not None else _derive_overall(sections), 1)
    total = round(total, 1)
    return {
        "current_score": cur,
        "recoverable_points": total,
        "projected_score": round(min(100.0, cur + total), 1),
        "signals": considered,
        "label": "Estimated impact — not a re-score",
        "disclaimer": ("Projection assumes each listed signal is fixed to a healthy "
                       "state. It never changes your actual score."),
    }


# ------------------------- Phase 2: entitlement gating (server-side) -------------------------
def gate_insights(insights: dict | None, unlocked: bool, *,
                  free_sim: int = 2, free_plan: int = 3) -> dict | None:
    """Trim the Phase 2 score-impact simulator + action plan to a free preview when the
    caller is NOT unlocked (Pro org or a $9 report purchase for this scan). This is the
    single source of truth for that trim — both `GET /reports/{scan_id}` (embedded) and
    `GET /reports/{scan_id}/insights` (dedicated) call it, so the two can never diverge
    and a free caller is never sent the full `items` / `week_*` arrays over the wire
    (client-side blur is UX only; this is the actual protection).

    Everything else in `insights` (score-loss breakdown, top problems, strongest/weakest
    signals, recovery projections) is Phase 1 trust-builder data and stays untrimmed on
    every tier — see build_insights_block."""
    if not insights or unlocked:
        return insights
    si = insights.get("score_impact") or {}
    ap = insights.get("action_plan") or {}

    sim_items = si.get("items", [])
    score_impact = {**si, "items": sim_items[:free_sim], "preview": True,
                    "locked_item_count": max(0, len(sim_items) - free_sim)}

    flat = [t for wk in ("week_1", "week_2", "week_3", "week_4", "backlog")
            for t in ap.get(wk, [])]
    action_plan = {"preview": True, "preview_tasks": flat[:free_plan],
                   "total_tasks": ap.get("total_tasks", len(flat)),
                   "locked_task_count": max(0, len(flat) - free_plan)}

    return {**insights, "score_impact": score_impact, "action_plan": action_plan}


def gate_recommendations(recommendations: list[dict] | None, unlocked: bool, *,
                         free_limit: int = 3) -> dict:
    """Trim the recommendation set to a free preview when the caller is NOT unlocked —
    the same server-side-trimming model as `gate_insights`, so a free caller's paid
    fix_template/evidence/affected-page detail for the LOCKED recommendations never
    leaves the server (no blurred-real-content in the DOM; the count alone tells the
    free caller how much more there is).

    FREE gets the top `free_limit` recommendations IN FULL (same shape/fields as paid —
    they are simply fewer of them, matching the existing negative-first teaser: real
    diagnosis for what's shown, nothing invented) plus a locked count. PAID gets every
    recommendation. Recommendation ORDER/CONTENT is untouched — this only changes how
    many cross the wire."""
    recs = recommendations or []
    if unlocked:
        return {"recommendations": recs, "recommendation_count": len(recs),
                "locked_recommendation_count": 0, "recommendations_preview": False}
    free = recs[:free_limit]
    return {"recommendations": free, "recommendation_count": len(recs),
            "locked_recommendation_count": max(0, len(recs) - free_limit),
            "recommendations_preview": True}


# ------------------------------ Phase 2: simulator ------------------------------
def build_score_impact(recommendations: list[dict], overall_score: float | None,
                       selected_ids=None) -> dict:
    """Deterministic before/after score-impact simulator over the recommendation set.

    Each opportunity's recoverable points = its signal's current points_lost
    (weight × (100 − score) / 100). Recovery is accounted PER SIGNAL, so selecting
    several recommendations that map to the same signal never recovers its points more
    than once — the maximum recovery from a signal is exactly its current points_lost.

    A projection only: never re-scores, never mutates overall_score or section.score.
    `projected_score` is clamped to 100 and labelled an estimate (full-fix assumption)."""
    by_signal: dict[str, dict] = {}
    for r in recommendations or []:
        sid = r.get("id")
        if sid in by_signal:            # one entry per signal — no double counting
            continue
        by_signal[sid] = {
            "id": sid,
            "signal_id": sid,
            "title": r.get("issue_title") or r.get("signal_label") or sid,
            "signal": r.get("signal_label") or sid,
            "current_score": r.get("score", 0) or 0,
            "recoverable_points": points_lost(r.get("weight", 0), r.get("score", 0)),
            "priority": r.get("priority"),
            "priority_score": r.get("priority_score"),
            "severity": r.get("severity"),
            "difficulty": r.get("difficulty"),
            "estimated_fix_time": r.get("estimated_fix_time"),
        }
    items = sorted(by_signal.values(),
                   key=lambda x: (-x["recoverable_points"], -(x["priority_score"] or 0), str(x["id"])))

    sel = set(selected_ids or [])
    selected = [it for it in items if it["signal_id"] in sel]
    selected_recovery = round(sum(it["recoverable_points"] for it in selected), 1)
    total = round(sum(it["recoverable_points"] for it in items), 1)
    cur = round(overall_score if overall_score is not None else 0, 1)
    return {
        "current_score": cur,
        "selected_recovery": selected_recovery,
        "projected_score": round(min(100.0, cur + selected_recovery), 1),
        "selected_signals": [it["signal_id"] for it in selected],
        "remaining_recoverable": round(max(0.0, total - selected_recovery), 1),
        "total_recoverable": total,
        "items": items,
        "label": "Estimated impact — not a re-score",
        "disclaimer": ("Projection assumes each selected signal is fixed to a perfect "
                       "score and recovers its full weighted points. It never changes "
                       "your actual score."),
    }


# ---------------------------- Phase 2: 30-day action plan ----------------------------
_PRIORITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
_DIFFICULTY_RANK = {"Easy": 0, "Moderate": 1, "Hard": 2}

# Category → phase routing. Together these cover all 9 report categories, so a
# categorised recommendation always has a natural home before falling to the backlog.
_AUTHORITY_CATS = {"Schema", "Internal Linking", "Content", "Metadata", "Indexability"}
_AI_CATS = {"AI Extractability", "Crawlability", "Performance", "Accessibility"}

_BIG_MINUTES = 10 ** 6   # sort sentinel for "no duration given" (kept last, not invented)


def _fix_minutes(text) -> int | None:
    """Lower-bound minutes parsed from an estimated_fix_time string, or None when there
    is no duration to parse. Used ONLY for deterministic ordering — never displayed, so a
    missing/duration is never invented, just sorted last."""
    if not text:
        return None
    m = re.search(r"(\d+)", str(text))
    if not m:
        return None
    n = int(m.group(1))
    t = str(text).lower()
    if "hour" in t or "hr" in t:
        return n * 60
    if "day" in t:
        return n * 480
    return n            # default unit is minutes


def _plan_sort_key(r: dict):
    """Deterministic schedule order: critical/high severity first, then higher
    priority_score, then lower difficulty, then shorter fix time, then id (stable)."""
    fm = _fix_minutes(r.get("estimated_fix_time"))
    return (
        _PRIORITY_RANK.get(r.get("priority"), 9),
        -(r.get("priority_score") or 0),
        _DIFFICULTY_RANK.get(r.get("difficulty"), 1),
        fm if fm is not None else _BIG_MINUTES,
        str(r.get("id")),
    )


def _plan_task(r: dict) -> dict:
    """One action-plan task, drawn verbatim from an existing recommendation. Nothing is
    invented: estimated_fix_time / impact stay None when the recommendation lacks them."""
    return {
        "id": r.get("id"),
        "recommendation": r.get("issue_title") or r.get("signal_label") or r.get("id"),
        "signal": r.get("signal_label"),
        "category": r.get("category"),
        "why_it_matters": r.get("business_impact") or r.get("description"),
        "ai_visibility_impact": r.get("ai_visibility_impact"),
        "priority": r.get("priority"),
        "severity": r.get("severity"),
        "priority_score": r.get("priority_score"),
        "difficulty": r.get("difficulty"),
        "estimated_fix_time": r.get("estimated_fix_time"),
    }


def build_action_plan(recommendations: list[dict], quick_wins=None,
                      per_week: int = 5) -> dict:
    """Deterministic 30-day plan assembled ENTIRELY from existing recommendations — no
    new tasks are invented. Each recommendation lands in exactly one bucket:

      Week 1 — Quick Wins            (engine quick_wins, else Easy difficulty)
      Week 2 — High Impact Fixes     (Critical / High priority)
      Week 3 — Authority & Structure (Schema/Links/Content/Metadata/Indexability)
      Week 4 — AI Visibility & Mon.  (AI Extractability/Crawlability/Perf/Accessibility)
      Later / Backlog                (overflow once a week hits `per_week`)

    Items are placed in the deterministic schedule order above; a full week overflows to
    the next eligible bucket and ultimately the backlog, so no single week is overloaded."""
    recs = list(recommendations or [])
    weeks: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
    backlog: list = []
    if not recs:
        return {"week_1": [], "week_2": [], "week_3": [], "week_4": [],
                "backlog": [], "total_tasks": 0}

    quick_ids = {q.get("id") for q in (quick_wins or [])}
    for r in sorted(recs, key=_plan_sort_key):
        task = _plan_task(r)
        cat, pri = r.get("category"), r.get("priority")
        is_quick = r.get("id") in quick_ids or r.get("difficulty") == "Easy"
        if is_quick and len(weeks[1]) < per_week:
            weeks[1].append(task)
        elif pri in ("Critical", "High") and len(weeks[2]) < per_week:
            weeks[2].append(task)
        elif cat in _AUTHORITY_CATS and len(weeks[3]) < per_week:
            weeks[3].append(task)
        elif cat in _AI_CATS and len(weeks[4]) < per_week:
            weeks[4].append(task)
        else:
            backlog.append(task)
    return {"week_1": weeks[1], "week_2": weeks[2], "week_3": weeks[3], "week_4": weeks[4],
            "backlog": backlog, "total_tasks": len(recs)}


def _headline_explanation(overall: int, issue_count: int, total_lost: float,
                          top: list[dict]) -> str:
    if not top:
        return f"Your site scored {overall}/100 with no significant issues holding it back."
    lead = ", ".join(p["signal"] for p in top[:3])
    return (
        f"Your AI-visibility score is {overall}/100. "
        f"{issue_count} issue{'' if issue_count == 1 else 's'} are costing you about "
        f"{total_lost} points. The biggest factors are {lead}."
    )


def build_insights_block(scan: dict, recommendations: list[dict] | None = None,
                         quick_wins: list[dict] | None = None) -> dict:
    """The additive `insights` block embedded in a report (all FREE trust-builder data;
    no locked fix content). Composes the score-loss breakdown, the 'why is my score low'
    top problems, strongest/weakest signals, two projections (fix-top vs fix-all), the
    Phase 2 score-impact simulator, and the deterministic 30-day action plan.

    Purely derived from the scan's own sections — adds NO scoring and mutates nothing."""
    sections = scan.get("sections") or []
    recs = recommendations or []
    overall = scan.get("overall_score")
    if overall is None:
        overall = _derive_overall(sections)

    breakdown = score_loss_breakdown(sections)
    top = why_score_low(sections, recs, limit=3)
    total_lost = round(sum(r["points_lost"] for r in breakdown), 1)
    issue_count = sum(r["issue_count"] for r in breakdown)

    severity_counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for r in breakdown:
        if r["points_lost"] > 0:            # only count signals that are real problems
            severity_counts[r["severity"]] = severity_counts.get(r["severity"], 0) + 1

    strongest = [
        {"signal_id": r["signal_id"], "label": r["label"], "score": r["score"]}
        for r in sorted(breakdown, key=lambda x: -x["score"]) if r["score"] >= GOOD
    ][:3]
    weakest = [
        {"signal_id": r["signal_id"], "label": r["label"], "score": r["score"],
         "points_lost": r["points_lost"]}
        for r in breakdown if r["points_lost"] > 0
    ][:3]

    return {
        "overall_score": overall,
        "total_points_lost": total_lost,
        "issue_count": issue_count,
        "severity_counts": severity_counts,
        "critical_count": severity_counts["Critical"],
        "high_count": severity_counts["High"],
        "score_breakdown": breakdown,
        "top_problems": top,
        "strongest_signals": strongest,
        "weakest_signals": weakest,
        # Fix your top problems → recover this much; fix everything found → recover up to this.
        "projected_recovery": projected_recovery(sections, overall, {p["signal_id"] for p in top}),
        "max_recovery": projected_recovery(sections, overall, None),
        "explanation": _headline_explanation(overall, issue_count, total_lost, top),
        # Phase 2: interactive score-impact simulator + deterministic 30-day action plan.
        "score_impact": build_score_impact(recs, overall),
        "action_plan": build_action_plan(recs, quick_wins),
    }


__all__ = [
    "points_lost", "score_loss_breakdown", "why_score_low",
    "projected_recovery", "build_score_impact", "build_action_plan",
    "build_insights_block", "gate_insights", "gate_recommendations",
]
