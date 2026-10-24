"""Change detection (Phase 7).

Compares a scan's signal sections with the previous scan and produces a structured
diff: overall score delta, per-category (signal) deltas + status transitions, issue
count change, and improvement/regression lists. Pure functions over the stored scan
`result` dicts (each has `overall_score` and `sections`)."""
from __future__ import annotations

# A per-signal delta of at least this magnitude counts as an improvement/regression.
_MOVED = 5


def sections_by_id(result: dict) -> dict:
    return {s.get("id"): s for s in (result.get("sections") or []) if s.get("id")}


def issue_count(result: dict) -> int:
    return sum(len(s.get("issues") or []) for s in (result.get("sections") or []))


def _num(v):
    return v if isinstance(v, (int, float)) else None


def detect_changes(prev: dict | None, curr: dict) -> dict:
    """Return the diff of `curr` vs `prev` (None on the first scan).

    `rubric_version_changed` is True when `curr`'s scoring rubric differs from
    `prev`'s (both are the raw stored scan-result dicts, which carry their own
    `rubric_version` — see routes_scan.py's scan payload). A rubric change can move
    a signal's score purely because the SCORING FORMULA changed, not because the
    site did — so when this is True, score deltas are not a reliable "did the site
    change" signal. `improvements`/`regressions` are left empty in that case (never
    fabricated from a delta that isn't real-world-comparable); `categories` still
    lists the raw before/after scores for transparency, each carrying the same flag
    so a caller can label them "not directly comparable" instead of hiding them."""
    curr_s = sections_by_id(curr)
    curr_issues = issue_count(curr)
    overall_curr = _num(curr.get("overall_score"))

    if not prev:
        return {
            "first_scan": True,
            "rubric_version_changed": False,
            "overall": {"prev": None, "curr": overall_curr, "delta": None},
            "issue_count": {"prev": None, "curr": curr_issues, "delta": None},
            "categories": [
                {"id": s.get("id"), "label": s.get("label"),
                 "prev": None, "curr": _num(s.get("score")), "delta": None,
                 "prev_status": None, "curr_status": s.get("status")}
                for s in curr.get("sections") or []
            ],
            "improvements": [],
            "regressions": [],
        }

    prev_s = sections_by_id(prev)
    overall_prev = _num(prev.get("overall_score"))
    overall_delta = (overall_curr - overall_prev
                     if overall_curr is not None and overall_prev is not None else None)

    rubric_changed = bool(prev.get("rubric_version")) and bool(curr.get("rubric_version")) \
        and prev.get("rubric_version") != curr.get("rubric_version")

    categories, improvements, regressions = [], [], []
    for sid, cs in curr_s.items():
        ps = prev_s.get(sid)
        c = _num(cs.get("score"))
        p = _num(ps.get("score")) if ps else None
        delta = (c - p) if (c is not None and p is not None) else None
        entry = {
            "id": sid, "label": cs.get("label"), "prev": p, "curr": c,
            # Null out the delta itself (not just the improvement/regression bucket)
            # when the rubric changed — any alert or caller reading entry["delta"]
            # directly (e.g. app.monitoring.alerts's category-regression checks)
            # must never see a formula-driven number mistaken for a real change.
            "delta": None if rubric_changed else delta,
            "prev_status": ps.get("status") if ps else None,
            "curr_status": cs.get("status"),
        }
        categories.append(entry)
        if rubric_changed:
            continue
        if delta is not None and delta >= _MOVED:
            improvements.append(entry)
        elif delta is not None and delta <= -_MOVED:
            regressions.append(entry)

    categories.sort(key=lambda e: (e["delta"] if e["delta"] is not None else 0))
    regressions.sort(key=lambda e: e["delta"])
    improvements.sort(key=lambda e: -e["delta"])
    prev_issues = issue_count(prev)

    return {
        "first_scan": False,
        "rubric_version_changed": rubric_changed,
        "overall": {"prev": overall_prev, "curr": overall_curr,
                   "delta": None if rubric_changed else overall_delta},
        "issue_count": {"prev": prev_issues, "curr": curr_issues,
                        "delta": curr_issues - prev_issues},
        "categories": categories,
        "improvements": improvements,
        "regressions": regressions,
    }
