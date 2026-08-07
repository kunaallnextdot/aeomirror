/* Compare two scans (Phase 4): pick a previous + current scan and diff overall
   score, per-signal scores, and issues. Fetches full detail for the two picks. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, TrendingUp, TrendingDown, Minus, Lock } from "lucide-react";
import { ScoreRing, fmtDate } from "./ui.jsx";
import { ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { queryParam } from "../auth/router.jsx";

export default function Compare({ scans = [], onCompare }) {
  const { openUpgrade, handleGated, reloadSubscription } = useUpgrade();
  const [aId, setAId] = useState("");
  const [bId, setBId] = useState("");
  const [a, setA] = useState(null);
  const [b, setB] = useState(null);
  const [loading, setLoading] = useState(false);
  const [gate, setGate] = useState(null);   // set to the 402 upgrade message

  const autoRan = useRef("");   // "a,b" of the last deep-link pair auto-run (once each)

  // Run the (server-metered) comparison. Invoked ONLY by an explicit user action:
  // the Compare button, or a `?compare=a,b` deep link (itself a user action). It is
  // NEVER called from a plain mount effect — that used to silently burn the Free quota.
  const runCompare = useCallback(async (idA, idB) => {
    if (!idA || !idB) return;
    setLoading(true); setGate(null);
    try {
      const { a: da, b: db } = await onCompare(idA, idB);
      setA(da); setB(db); reloadSubscription();
    } catch (e) {
      setA(null); setB(null);
      // Monthly comparison limit → show the inline gate AND pop the modal.
      if (e instanceof ScanError && e.code === 402) {
        setGate(e.message);
        handleGated(e, "compares");
      }
    } finally { setLoading(false); }
  }, [onCompare, reloadSubscription, handleGated]);

  useEffect(() => {
    // A `?compare=a,b` query (set when two scans are ticked in Recent Scans) preselects
    // both sides; only ids that exist in the loaded scan list are honoured. A deep link
    // is an explicit action, so it auto-runs — but ONLY once per pair, never on re-render.
    const preset = (queryParam("compare") || "").split(",").filter(Boolean);
    const valid = preset.filter((id) => scans.some((s) => String(s.id) === id));
    if (valid.length === 2) {
      setAId(valid[0]); setBId(valid[1]);
      const key = valid.join(",");
      if (autoRan.current !== key) { autoRan.current = key; runCompare(valid[0], valid[1]); }
      return;
    }
    // Plain tab open: preselect the dropdowns for good UX, but do NOT fetch.
    if (scans.length >= 2) { setAId(scans[1].id); setBId(scans[0].id); }
    else if (scans.length === 1) { setBId(scans[0].id); }
  }, [scans, runCompare]);

  const opts = (v, set) => (
    <select className="d-select" value={v}
            onChange={(e) => { set(e.target.value); setA(null); setB(null); setGate(null); }}
            style={{ width: "100%" }}>
      <option value="">Select a scan…</option>
      {scans.map((s) => <option key={s.id} value={s.id}>{s.domain} · {s.overall_score ?? "-"} · {fmtDate(s.scan_time)}</option>)}
    </select>
  );

  const secMap = (rep) => Object.fromEntries((rep?.sections || []).map((s) => [s.id, s]));
  const issueSet = (rep) => new Set((rep?.sections || []).flatMap((s) => s.issues || []));

  let improved = [], regressed = [], newIssues = [], resolved = [];
  if (a && b) {
    const ma = secMap(a), mb = secMap(b);
    for (const id of Object.keys(mb)) {
      const before = ma[id]?.score, after = mb[id]?.score;
      if (before == null) continue;
      if (after > before) improved.push({ label: mb[id].label, before, after });
      else if (after < before) regressed.push({ label: mb[id].label, before, after });
    }
    const ia = issueSet(a), ib = issueSet(b);
    newIssues = [...ib].filter((x) => !ia.has(x));
    resolved = [...ia].filter((x) => !ib.has(x));
  }

  return (
    <div>
      <div className="d-cmp-pick">
        <div className="d-card"><div className="d-stat-label">Previous scan</div><div style={{ marginTop: 10 }}>{opts(aId, setAId)}</div></div>
        <div className="d-card"><div className="d-stat-label">Current scan</div><div style={{ marginTop: 10 }}>{opts(bId, setBId)}</div></div>
      </div>

      <div style={{ display: "flex", justifyContent: "center", margin: "14px 0" }}>
        <button className="btn btn-primary btn-sm" disabled={!aId || !bId || loading}
                onClick={() => runCompare(aId, bId)}>
          {loading ? "Comparing…" : "Compare"}
        </button>
      </div>

      {gate ? (
        <div className="d-panel" style={{ textAlign: "center", padding: 28 }}>
          <Lock size={18} style={{ color: "var(--warn)" }} />
          <div style={{ margin: "8px 0 14px", color: "var(--txt-mid)" }}>{gate}</div>
          <button className="btn btn-primary btn-sm" onClick={() => openUpgrade("compares", { message: gate })}>Upgrade to Pro</button>
        </div>
      ) : (!a || !b) ? (
        <div className="d-panel d-dim" style={{ textAlign: "center", padding: 32 }}>
          {loading ? "Loading…" : (aId && bId) ? "Click Compare to run the comparison." : "Pick two scans to compare."}
        </div>
      ) : (
        <>
          <div className="d-panel" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 26, marginBottom: 16 }}>
            <Delta label={a.domain} value={a.overall_score} />
            <ArrowRight size={22} style={{ color: "var(--txt-dim)" }} />
            <Delta label={b.domain} value={b.overall_score} />
            <ScoreDelta before={a.overall_score} after={b.overall_score} />
          </div>

          <div className="d-grid d-grid-2">
            <DiffList title="Improved signals" items={improved.map((x) => `${x.label}: ${x.before} → ${x.after}`)} tone="up" />
            <DiffList title="Regressed signals" items={regressed.map((x) => `${x.label}: ${x.before} → ${x.after}`)} tone="down" />
            <DiffList title="Resolved issues" items={resolved} tone="up" />
            <DiffList title="New issues" items={newIssues} tone="down" />
          </div>
        </>
      )}
    </div>
  );
}

function Delta({ label, value }) {
  return (
    <div style={{ textAlign: "center" }}>
      <ScoreRing value={value} size={64} stroke={6} />
      <div className="d-dim" style={{ fontSize: 12, marginTop: 6, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</div>
    </div>
  );
}

function ScoreDelta({ before, after }) {
  const diff = (after ?? 0) - (before ?? 0);
  const Icon = diff > 0 ? TrendingUp : diff < 0 ? TrendingDown : Minus;
  const cls = diff > 0 ? "d-up" : diff < 0 ? "d-down" : "d-flat";
  return <div className={cls} style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 700, fontFamily: "'IBM Plex Mono'", fontSize: 18 }}><Icon size={18} /> {diff > 0 ? "+" : ""}{diff}</div>;
}

function DiffList({ title, items, tone }) {
  return (
    <div className="d-panel">
      <div className="d-panel-h">{title} <span className="sub">{items.length}</span></div>
      {items.length === 0 ? <div className="d-dim" style={{ fontSize: 12.5 }}>None</div> : (
        <div className="d-list">
          {items.map((it, i) => (
            <div key={i} className="d-diff">
              <span className={tone === "up" ? "d-up" : "d-down"}>{tone === "up" ? "▲" : "▼"}</span>
              <span style={{ color: "var(--txt-mid)" }}>{it}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
