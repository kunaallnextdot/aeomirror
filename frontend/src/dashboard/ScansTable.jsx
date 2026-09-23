/* Recent Scans table + filters + row actions — MIGRATED to the Aurora design system.
   Filtering/sorting/selection logic, hooks and handlers are byte-for-byte unchanged; only
   JSX + class names changed (dark d-* -> Aurora primitives + au-sc-*). The route-level
   loading / empty / error states (owned by the shared <Gated> for 5 routes) are re-skinned
   here as ScansLoading / ScansEmpty / ScansError and used by ScansRoute ONLY, so the shared
   Gated / EmptyState / ErrorState primitives stay untouched for the other routes. */
import React, { useMemo, useState } from "react";
import { Search, Eye, RefreshCw, Trash2, GitCompare, X, Radar, AlertTriangle } from "lucide-react";
import { fmtDate, fmtDuration } from "./ui.jsx";
import { Shell, Cell, Tag, Ring, Skeleton } from "./aurora.jsx";
import "./ScansTable.aurora.css";

const DAY = 86400000;
const STATUS_VARIANT = { pass: "ok", warn: "warning", fail: "critical" };
const scoreStatus = (v) => (v >= 75 ? "pass" : v >= 45 ? "warn" : "fail");

export default function ScansTable({ scans = [], busyId, onView, onRerun, onDelete,
                                    canRun = true, canDelete = true, onCompareSelected }) {
  const [q, setQ] = useState("");
  const [score, setScore] = useState("all");
  const [when, setWhen] = useState("all");
  const [sort, setSort] = useState("newest");
  const [selected, setSelected] = useState([]);   // scan ids ticked for comparison

  const toggleSel = (id) => setSelected((prev) =>
    prev.includes(id) ? prev.filter((x) => x !== id)
      : prev.length >= 2 ? [prev[1], id]   // keep at most 2 (drop the oldest pick)
      : [...prev, id]);

  const rows = useMemo(() => {
    let r = [...scans];
    if (q.trim()) { const ql = q.toLowerCase(); r = r.filter((s) => (s.url || "").toLowerCase().includes(ql)); }
    if (score !== "all") {
      r = r.filter((s) => {
        const v = s.overall_score ?? -1;
        return score === "high" ? v >= 75 : score === "mid" ? v >= 45 && v < 75 : v >= 0 && v < 45;
      });
    }
    if (when !== "all") {
      const cutoff = Date.now() - (when === "7" ? 7 : 30) * DAY;
      r = r.filter((s) => s.scan_time && new Date(s.scan_time).getTime() >= cutoff);
    }
    const key = (s) => s.overall_score ?? -1;
    const t = (s) => (s.scan_time ? new Date(s.scan_time).getTime() : 0);
    if (sort === "newest") r.sort((a, b) => t(b) - t(a));
    else if (sort === "oldest") r.sort((a, b) => t(a) - t(b));
    else if (sort === "highest") r.sort((a, b) => key(b) - key(a));
    else if (sort === "lowest") r.sort((a, b) => key(a) - key(b));
    return r;
  }, [scans, q, score, when, sort]);

  // A monitor-triggered scan (scheduled check / "Run Now") shows up here — it's a
  // real scan — but never counts against the sidebar's "Billable scans" quota (see
  // entitlements.scans_this_month). Only surfaced when it's actually true for this
  // list, so the common case (every scan is billable) shows nothing extra.
  const billableCount = scans.filter((s) => s.billable !== false).length;
  const hasNonBillable = billableCount < scans.length;

  return (
    <div className="aurora-screen">
      <Shell>
        {hasNonBillable && (
          <div className="au-dim" style={{ fontSize: 12.5, margin: "0 2px 10px" }}>
            {scans.length} scan{scans.length === 1 ? "" : "s"} · {billableCount} counted toward your monthly scan quota
            {" "}(scans tagged <b>Monitor</b> below were triggered automatically and don&apos;t use your quota)
          </div>
        )}
        <div className="au-sc-toolbar">
          <div className="au-sc-search">
            <Search size={15} />
            <input placeholder="Search by URL" value={q} onChange={(e) => setQ(e.target.value)} spellCheck={false} />
          </div>
          <select className="au-sc-select" value={score} onChange={(e) => setScore(e.target.value)}>
            <option value="all">All scores</option>
            <option value="high">Healthy (75+)</option>
            <option value="mid">Needs work (45-74)</option>
            <option value="low">At risk (&lt;45)</option>
          </select>
          <select className="au-sc-select" value={when} onChange={(e) => setWhen(e.target.value)}>
            <option value="all">All time</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
          </select>
          <select className="au-sc-select" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="highest">Highest score</option>
            <option value="lowest">Lowest score</option>
          </select>
        </div>

        <Cell solid style={{ padding: 0, overflow: "hidden" }}>
          <div className="au-sc-tablewrap" style={{ border: 0, borderRadius: 0 }}>
            <table className="au-sc-table">
              <thead>
                <tr>
                  {onCompareSelected && <th style={{ width: 34 }} aria-label="Select" />}
                  <th>Website</th><th>Overall Score</th><th>Status</th>
                  <th>Scan Date</th><th>Duration</th><th className="au-sc-th-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={onCompareSelected ? 7 : 6} className="au-sc-empty">No scans match these filters.</td></tr>
                )}
                {rows.map((s) => {
                  const busy = busyId === s.id;
                  const isSel = selected.includes(s.id);
                  const st = s.status || scoreStatus(s.overall_score);
                  return (
                    <tr key={s.id} className={isSel ? "au-sc-sel" : ""}>
                      {onCompareSelected && (
                        <td>
                          <input type="checkbox" className="au-sc-check" checked={isSel}
                                 onChange={() => toggleSel(s.id)} aria-label={`Select ${s.domain || s.url} for comparison`} />
                        </td>
                      )}
                      <td>
                        <span className="au-sc-url">{s.domain || s.url}</span>
                        {s.billable === false && (
                          <Tag variant="info" style={{ marginLeft: 6 }} title="Triggered by a monitor — doesn't use your scan quota">Monitor</Tag>
                        )}
                      </td>
                      <td><span role="img" aria-label={`Score ${s.overall_score ?? "not available"}`}><Ring value={s.overall_score} size={34} /></span></td>
                      <td><Tag variant={STATUS_VARIANT[st] || "info"}>{String(st).toUpperCase()}</Tag></td>
                      <td className="au-sc-meta">{fmtDate(s.scan_time)}</td>
                      <td className="au-sc-meta">{fmtDuration(s.duration_ms)}</td>
                      <td className="au-sc-td-right">
                        <div className="au-sc-actions">
                          <button className="au-sc-iconbtn" onClick={() => onView(s.id)}
                                  title="View scan details" aria-label={`View scan details for ${s.domain || s.url}`}>
                            <Eye size={13} />
                          </button>
                          {canRun && (
                            <button className="au-sc-iconbtn" disabled={busy} onClick={() => onRerun(s.id)}
                                    title="Re-run scan" aria-label={`Re-run scan for ${s.domain || s.url}`}>
                              <RefreshCw size={13} className={busy ? "spin-slow" : ""} />
                            </button>
                          )}
                          {canDelete && (
                            <button className="au-sc-iconbtn au-sc-danger" disabled={busy} onClick={() => onDelete(s.id)}
                                    title="Delete" aria-label={`Delete scan for ${s.domain || s.url}`}>
                              <Trash2 size={13} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Cell>

        {/* floating compare bar — appears only when exactly two scans are ticked */}
        {onCompareSelected && selected.length > 0 && (
          <div className="au-sc-cmp">
            <span className="au-sc-cmp-n">{selected.length} selected</span>
            {selected.length === 2
              ? <button className="au-sc-cmp-go" onClick={() => onCompareSelected(selected[0], selected[1])}>
                  <GitCompare size={14} /> Compare selected
                </button>
              : <span className="au-sc-cmp-hint">pick one more to compare</span>}
            <button className="au-sc-cmp-x" onClick={() => setSelected([])} title="Clear selection"><X size={14} /></button>
          </div>
        )}
      </Shell>
    </div>
  );
}

/* Route-level LOADING (Aurora) — replaces the shared <StatsSkeleton/><TableSkeleton/> for
   /app/scans only. */
export function ScansLoading() {
  return (
    <div className="aurora-screen">
      <Shell>
        <div className="au-sc-toolbar">
          <Skeleton w="40%" h={38} /><Skeleton w={120} h={38} /><Skeleton w={120} h={38} />
        </div>
        <Cell solid>
          <div className="au-sc-skrows">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} h={44} />)}</div>
        </Cell>
      </Shell>
    </div>
  );
}

/* Route-level EMPTY (Aurora) — copy verbatim from the shared EmptyState. */
export function ScansEmpty({ onRun }) {
  return (
    <div className="aurora-screen">
      <Shell>
        <Cell solid>
          <div className="au-sc-center">
            <div className="au-sc-ill"><Radar size={26} /></div>
            <div className="au-sc-ct">No scans yet</div>
            <div className="au-sc-cs">
              Run your first scan to see:
              <ul className="au-empty-bullets">
                <li>visibility score</li><li>biggest problems</li><li>evidence</li><li>recommended fixes</li>
              </ul>
            </div>
            <button className="au-btn au-accent" onClick={onRun}><Radar size={16} /> Run first scan</button>
          </div>
        </Cell>
      </Shell>
    </div>
  );
}

/* Route-level ERROR (Aurora) — copy verbatim from the shared ErrorState. */
export function ScansError({ message, onRetry }) {
  return (
    <div className="aurora-screen">
      <Shell>
        <Cell solid>
          <div className="au-sc-center" role="alert">
            <div className="au-sc-ill au-sc-bad"><AlertTriangle size={26} /></div>
            <div className="au-sc-cs" style={{ marginBottom: onRetry ? 20 : 0 }}>{message || "Something went wrong."}</div>
            {onRetry && <button className="au-btn au-accent" onClick={onRetry}><RefreshCw size={14} /> Retry</button>}
          </div>
        </Cell>
      </Shell>
    </div>
  );
}
