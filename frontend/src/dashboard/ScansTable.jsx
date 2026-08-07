/* Recent Scans table + filters + row actions (Phase 4). Filtering/sorting is
   client-side over the already-fetched requester scans (instant, no refetch). */
import React, { useMemo, useState } from "react";
import { Search, Eye, RefreshCw, Trash2, GitCompare, X } from "lucide-react";
import { ScoreRing, StatusBadge, fmtDate, fmtDuration } from "./ui.jsx";

const DAY = 86400000;

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

  return (
    <div>
      <div className="d-toolbar">
        <div className="d-search">
          <Search size={15} />
          <input placeholder="Search by URL" value={q} onChange={(e) => setQ(e.target.value)} spellCheck={false} />
        </div>
        <select className="d-select" value={score} onChange={(e) => setScore(e.target.value)}>
          <option value="all">All scores</option>
          <option value="high">Healthy (75+)</option>
          <option value="mid">Needs work (45-74)</option>
          <option value="low">At risk (&lt;45)</option>
        </select>
        <select className="d-select" value={when} onChange={(e) => setWhen(e.target.value)}>
          <option value="all">All time</option>
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
        </select>
        <select className="d-select" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="highest">Highest score</option>
          <option value="lowest">Lowest score</option>
        </select>
      </div>

      <div className="d-table-wrap">
        <table className="d-table">
          <thead>
            <tr>
              {onCompareSelected && <th style={{ width: 34 }} aria-label="Select" />}
              <th>Website</th><th>Overall Score</th><th>Status</th>
              <th>Scan Date</th><th>Duration</th><th style={{ textAlign: "right" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={onCompareSelected ? 7 : 6} className="d-dim" style={{ textAlign: "center", padding: 28 }}>No scans match these filters.</td></tr>
            )}
            {rows.map((s) => {
              const busy = busyId === s.id;
              const isSel = selected.includes(s.id);
              return (
                <tr key={s.id} className={isSel ? "is-selected" : ""}>
                  {onCompareSelected && (
                    <td>
                      <input type="checkbox" className="d-check" checked={isSel}
                             onChange={() => toggleSel(s.id)} aria-label={`Select ${s.domain || s.url} for comparison`} />
                    </td>
                  )}
                  <td><span className="d-url">{s.domain || s.url}</span></td>
                  <td><div style={{ display: "flex", alignItems: "center", gap: 10 }}><ScoreRing value={s.overall_score} size={34} /></div></td>
                  <td><StatusBadge status={s.status} /></td>
                  <td className="d-mono d-dim">{fmtDate(s.scan_time)}</td>
                  <td className="d-mono d-dim">{fmtDuration(s.duration_ms)}</td>
                  <td>
                    <div className="d-actions">
                      <button className="d-iconbtn" onClick={() => onView(s.id)} title="View report"><Eye size={13} /></button>
                      {canRun && (
                        <button className="d-iconbtn" disabled={busy} onClick={() => onRerun(s.id)} title="Re-run scan">
                          <RefreshCw size={13} className={busy ? "spin-slow" : ""} />
                        </button>
                      )}
                      {canDelete && (
                        <button className="d-iconbtn danger" disabled={busy} onClick={() => onDelete(s.id)} title="Delete"><Trash2 size={13} /></button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* floating compare bar — appears only when exactly two scans are ticked */}
      {onCompareSelected && selected.length > 0 && (
        <div className="d-cmp-float">
          <span className="d-cmp-float-n">{selected.length} selected</span>
          {selected.length === 2
            ? <button className="d-cmp-float-go" onClick={() => onCompareSelected(selected[0], selected[1])}>
                <GitCompare size={14} /> Compare selected
              </button>
            : <span className="d-dim" style={{ fontSize: 12 }}>pick one more to compare</span>}
          <button className="d-cmp-float-x" onClick={() => setSelected([])} title="Clear selection"><X size={14} /></button>
        </div>
      )}
    </div>
  );
}
