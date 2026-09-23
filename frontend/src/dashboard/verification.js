/* Deterministic scan-comparison utilities used by Compare (`Compare.jsx`). Pure
   functions only: no network calls, no mutation of scans, no LLM/external API, no
   scoring changes. Operates entirely on the `sections` arrays two scan responses
   already carry (GET /api/scans/:id and POST /api/compare both call the SAME
   backend `build_scan_response()` — verified field-for-field identical, see
   routes_scan.py).

   Signal matching is ALWAYS by `section.id` (the scanner's own stable signal id,
   the same id `build_recommendations()` uses) — never by label, array position, or
   issue/recommendation text.

   Fix Verification (`ScanDetails.jsx`) now uses the server-authoritative
   `POST /api/verifications` endpoint (`backend/app/services/verification.py`,
   a 1:1 port of this same state machine) instead of computing verification
   client-side, so persisted results survive a reload. `verificationStatus()`
   below remains as the shared reference implementation of that state machine and
   stays directly tested. */

const STATUS_RANK = { fail: 0, warn: 1, pass: 2 };

export function sectionsById(report) {
  return Object.fromEntries((report?.sections || []).map((s) => [s.id, s]));
}

/* Cross-scan signal-level score/status deltas for every signal in `after` that also
   exists in `before` — the same primitive Compare's "Improved/Regressed signals"
   lists are built from. */
export function diffSignals(before, after) {
  const beforeById = sectionsById(before), afterById = sectionsById(after);
  return Object.keys(afterById).map((id) => {
    const b = beforeById[id], a = afterById[id];
    if (!b) return { signal_id: id, label: a.label, comparable: false };
    return {
      signal_id: id, label: a.label, comparable: true,
      score_before: b.score, score_after: a.score,
      status_before: b.status, status_after: a.status,
    };
  });
}

/* The state machine (see ticket table): a status IMPROVEMENT only counts as
   "verified" when it actually lands on pass with nothing left outstanding — a score
   increase or a raw status-tier bump alone is never enough (never "verified" just
   because a number went up). A same-tier WARN->WARN (or FAIL->FAIL) improvement is
   "partially_improved" at best, even if every currently-listed issue cleared — the
   status word itself didn't reach pass, so this stays honest rather than declaring
   victory early. A tier drop is always "regressed", even alongside some resolved
   issues (the overall signal got worse; the resolved issue still shows in the
   detail, just not as the headline verdict). */
export function verificationStatus({ status_before, status_after, resolvedCount, remainingCount, newCount }) {
  const rb = STATUS_RANK[status_before], ra = STATUS_RANK[status_after];
  if (rb == null || ra == null) return "not_comparable";
  if (ra > rb) return (status_after === "pass" && remainingCount === 0) ? "verified" : "partially_improved";
  if (ra === rb) {
    if (remainingCount === 0 && resolvedCount > 0 && newCount === 0) {
      return status_after === "pass" ? "verified" : "partially_improved";
    }
    return resolvedCount > 0 ? "partially_improved" : "unchanged";
  }
  return "regressed";
}
