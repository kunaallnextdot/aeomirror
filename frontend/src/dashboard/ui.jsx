/* Reusable dashboard primitives + formatting helpers (Phase 4). */
import React from "react";
import { AlertTriangle, RefreshCw, Radar } from "lucide-react";

/* ---------- helpers ---------- */
export function band(v) {
  if (v == null) return "na";
  return v >= 75 ? "good" : v >= 45 ? "warn" : "bad";
}
export function scoreColor(v) {
  return v == null ? "var(--txt-dim)" : v >= 75 ? "var(--good)" : v >= 45 ? "var(--warn)" : "var(--bad)";
}
const STATUS_LABEL = { pass: "HEALTHY", warn: "NEEDS WORK", fail: "AT RISK", complete: "COMPLETE" };
export function statusLabel(s) { return STATUS_LABEL[s] || String(s || "").toUpperCase(); }
export function statusColor(s) {
  return s === "pass" ? "var(--good)" : s === "warn" ? "var(--warn)" : s === "fail" ? "var(--bad)" : "var(--txt-mid)";
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

/* ---------- components ---------- */
export function ScoreRing({ value, size = 44, stroke = 5 }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value)) / 100;
  const col = scoreColor(value);
  return (
    <div className="d-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={col} strokeWidth={stroke}
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct)}
          transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: "stroke-dashoffset .6s ease" }} />
      </svg>
      <span className="n" style={{ color: col, fontSize: size * 0.3 }}>{value == null ? "-" : value}</span>
    </div>
  );
}

export function StatusBadge({ status }) {
  const col = statusColor(status);
  return <span className="d-badge" style={{ color: col, borderColor: col }}>{statusLabel(status)}</span>;
}

export function StatCard({ label, value, sub, icon: Icon }) {
  return (
    <div className="d-card">
      <div className="d-stat-label">{Icon && <Icon size={13} />} {label}</div>
      <div className="d-stat-value">{value}</div>
      {sub != null && <div className="d-stat-sub">{sub}</div>}
    </div>
  );
}

export function Skeleton({ w = "100%", h = 16, style }) {
  return <div className="d-skel" style={{ width: w, height: h, ...style }} />;
}

export function TableSkeleton({ rows = 6 }) {
  return (
    <div className="d-table-wrap" style={{ padding: 12 }}>
      {Array.from({ length: rows }).map((_, i) => <div key={i} className="d-skel d-skel-row" />)}
    </div>
  );
}

export function StatsSkeleton({ n = 4 }) {
  return (
    <div className="d-stats">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="d-card"><Skeleton w="60%" h={12} /><Skeleton w="40%" h={26} style={{ marginTop: 12 }} /></div>
      ))}
    </div>
  );
}

export function EmptyState({ onRun }) {
  return (
    <div className="d-empty">
      <div className="d-empty-ill"><Radar size={44} /></div>
      <h3>No scans yet</h3>
      <p>
        AEOMirror checks whether AI systems like ChatGPT, Claude, Gemini and Perplexity
        can reach, read and understand any website — then scores it across 10 signals.
        Run your first scan to populate this dashboard.
      </p>
      <button className="dash-newscan" onClick={onRun}><Radar size={16} /> Run first scan</button>
    </div>
  );
}

/* Compact, reusable empty state — centered icon + one line + optional primary CTA.
   Shares the dark-theme tokens with the full-page EmptyState. */
export function MiniEmpty({ icon: Icon = Radar, line, cta, onCta }) {
  return (
    <div className="d-mini-empty">
      <div className="d-mini-empty-ill">{Icon && <Icon size={26} />}</div>
      <div className="d-mini-empty-line">{line}</div>
      {cta && onCta && <button className="dash-newscan" onClick={onCta}>{cta}</button>}
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="d-error" role="alert">
      <AlertTriangle size={16} />
      <span>{message || "Something went wrong."}</span>
      {onRetry && <button className="d-retry" onClick={onRetry}><RefreshCw size={12} /> Retry</button>}
    </div>
  );
}
