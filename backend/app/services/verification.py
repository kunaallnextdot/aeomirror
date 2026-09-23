"""Fix Verification — deterministic before/after comparison, ported 1:1 from the
existing frontend engine (frontend/src/dashboard/verification.js) so the PERSISTED
verification record is server-authoritative rather than trusting a client-computed
result, without inventing a second/divergent algorithm. Every rule, threshold and
field name below mirrors verification.js exactly — see its own docstring for the
full rationale; this module intentionally does not re-derive that reasoning.

Pure functions only: no DB session, no network call, no LLM, no scoring change.
Operates on the SAME `sections` list already stored in `Scan.result` (the identical
shape `build_scan_response()`/Compare/Scan Details already read)."""
from __future__ import annotations

_STATUS_RANK = {"fail": 0, "warn": 1, "pass": 2}

NOT_COMPARABLE = "not_comparable"
VERIFIED = "verified"
PARTIALLY_IMPROVED = "partially_improved"
UNCHANGED = "unchanged"
REGRESSED = "regressed"


def sections_by_id(scan_result: dict | None) -> dict:
    return {s.get("id"): s for s in (scan_result or {}).get("sections") or [] if s.get("id")}


def _issue_diff(before_issues: list[str] | None, after_issues: list[str] | None) -> dict:
    bi, ai = before_issues or [], after_issues or []
    b_set, a_set = set(bi), set(ai)
    return {
        "resolved_issues": [i for i in bi if i not in a_set],
        "remaining_issues": [i for i in ai if i in b_set],
        "new_issues": [i for i in ai if i not in b_set],
    }


def _evidence_diff(before_ev: dict | None, after_ev: dict | None) -> list[dict]:
    be, ae = before_ev or {}, after_ev or {}
    changes = []
    for k in sorted(set(be) | set(ae)):
        if k == "detected_types":
            continue
        bv, av = be.get(k), ae.get(k)
        if bv != av:
            changes.append({"key": k, "before": bv, "after": av})
    return changes


def verification_status(*, status_before: str | None, status_after: str | None,
                        resolved_count: int, remaining_count: int, new_count: int) -> str:
    """Same state machine as verification.js's verificationStatus() — see that
    module's comment for the full worked table. A status IMPROVEMENT only counts as
    VERIFIED when it actually lands on "pass" with nothing left outstanding; a
    same-tier improvement (even one that clears every current issue) never exceeds
    PARTIALLY_IMPROVED since the status word itself never reached pass; any tier drop
    is always REGRESSED regardless of any issues that also happened to resolve."""
    rb, ra = _STATUS_RANK.get(status_before), _STATUS_RANK.get(status_after)
    if rb is None or ra is None:
        return NOT_COMPARABLE
    if ra > rb:
        return VERIFIED if (status_after == "pass" and remaining_count == 0) else PARTIALLY_IMPROVED
    if ra == rb:
        if remaining_count == 0 and resolved_count > 0 and new_count == 0:
            return VERIFIED if status_after == "pass" else PARTIALLY_IMPROVED
        return PARTIALLY_IMPROVED if resolved_count > 0 else UNCHANGED
    return REGRESSED


def verify_signal(signal_id: str, before_scan_result: dict | None,
                  after_scan_result: dict | None, *,
                  before_status: str | None = "completed",
                  after_status: str | None = "completed") -> dict | None:
    """The single Fix Verification entry point — mirrors verification.js's
    verifySignal() exactly. `before_status`/`after_status` are each SCAN's own
    processing status (pending/running/completed/failed, NOT the signal's pass/warn/
    fail) — only a completed scan on both sides is ever comparable, matching the
    frontend's own guard. Returns None ("not comparable") rather than guessing when
    either scan isn't ready or the signal is absent from either side."""
    if before_scan_result is None or after_scan_result is None:
        return None
    if before_status and before_status != "completed":
        return None
    if after_status and after_status != "completed":
        return None

    before = sections_by_id(before_scan_result).get(signal_id)
    after = sections_by_id(after_scan_result).get(signal_id)
    if not before or not after:
        return None

    diff = _issue_diff(before.get("issues"), after.get("issues"))
    evidence_changes = _evidence_diff(before.get("evidence"), after.get("evidence"))
    score_before, score_after = before.get("score"), after.get("score")
    score_delta = round((score_after or 0) - (score_before or 0), 1)
    status = verification_status(
        status_before=before.get("status"), status_after=after.get("status"),
        resolved_count=len(diff["resolved_issues"]), remaining_count=len(diff["remaining_issues"]),
        new_count=len(diff["new_issues"]))

    return {
        "signal_id": signal_id, "label": after.get("label") or before.get("label"),
        "verification_status": status,
        "status_before": before.get("status"), "status_after": after.get("status"),
        "score_before": score_before, "score_after": score_after, "score_delta": score_delta,
        "status_changed": before.get("status") != after.get("status"),
        "resolved_issues": diff["resolved_issues"], "remaining_issues": diff["remaining_issues"],
        "new_issues": diff["new_issues"], "evidence_changes": evidence_changes,
    }
