/* Dashboard formatting helpers (Phase 4).
   The dark state/primitive COMPONENTS that once lived here — ScoreRing, StatusBadge,
   StatCard, Skeleton, TableSkeleton, StatsSkeleton, EmptyState, MiniEmpty, ErrorState
   (+ band/statusLabel/statusColor) — were superseded by the Aurora primitives
   (aurora.jsx) and the route-scoped Aurora states, and removed in the #14 cleanup.
   Only the still-consumed helpers remain. `scoreColor` is dark-token-based and used by
   the still-dark charts.jsx dark branch + ContentInsights. */
export function scoreColor(v) {
  return v == null ? "var(--txt-dim)" : v >= 75 ? "var(--good)" : v >= 45 ? "var(--warn)" : "var(--bad)";
}
export function fmtDate(iso) {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return "-"; }
}
export function fmtDuration(ms) {
  if (ms == null) return "-";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}
