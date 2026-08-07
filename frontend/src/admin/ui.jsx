/* Shared admin UI primitives (Phase 8): async data hook, stat cards, badges,
   search input, pagination, and simple loading/error/empty states. */
import React, { useCallback, useEffect, useState } from "react";
import { Search, Loader2, AlertTriangle, ChevronLeft, ChevronRight } from "lucide-react";

export function useAdminData(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setData(await fn()); }
    catch (e) { setError(e?.message || "Could not load data."); }
    finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { load(); }, [load]);
  return { data, error, loading, reload: load, setData };
}

export function AdminStat({ label, value, sub, tone }) {
  return (
    <div className="ad-stat">
      <div className="ad-stat-l">{label}</div>
      <div className="ad-stat-v" style={tone ? { color: tone } : undefined}>{value}</div>
      {sub != null && <div className="ad-stat-s">{sub}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }) {
  return <span className={`ad-badge ad-${tone}`}>{children}</span>;
}

export function SearchInput({ value, onChange, placeholder = "Search…" }) {
  return (
    <div className="ad-search">
      <Search size={15} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} spellCheck={false} />
    </div>
  );
}

export function Pager({ page, pages, total, onPage }) {
  if (!total) return null;
  return (
    <div className="ad-pager">
      <span className="ad-dim">{total} total · page {page} / {pages || 1}</span>
      <div className="ad-pager-btns">
        <button className="ad-btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={13} /></button>
        <button className="ad-btn sm" disabled={page >= (pages || 1)} onClick={() => onPage(page + 1)}><ChevronRight size={13} /></button>
      </div>
    </div>
  );
}

export function Loading({ label = "Loading…" }) {
  return <div className="ad-loading"><Loader2 size={16} className="spin-slow" /> {label}</div>;
}

export function ErrorBox({ message, onRetry }) {
  return (
    <div className="ad-error">
      <AlertTriangle size={15} /> <span>{message}</span>
      {onRetry && <button className="ad-btn sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function EmptyRow({ cols, label = "Nothing here yet." }) {
  return <tr><td colSpan={cols} className="ad-empty-row">{label}</td></tr>;
}

export function statusTone(status) {
  return { active: "good", suspended: "bad", paused: "warn", healthy: "good",
    degraded: "bad", ok: "good", down: "bad", not_configured: "neutral",
    enabled: "good", disabled: "warn", completed: "good", failed: "bad",
    pending: "warn", running: "accent" }[status] || "neutral";
}

export function fmtDateTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return "—"; }
}

export function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"]; let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}
