/* Compare two scans — MIGRATED to Aurora. Data flow, hooks, deep-link auto-run guard,
   metering/gate logic are byte-for-byte unchanged; only JSX + class names changed.
   `.aurora-screen`-scoped. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, TrendingUp, TrendingDown, Minus, Lock } from "lucide-react";
import { fmtDate } from "./ui.jsx";
import { ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { queryParam } from "../auth/router.jsx";
import { Shell, Cell, Ring, Button } from "./aurora.jsx";
import { diffSignals } from "./verification.js";

export default function Compare({ scans = [], onCompare }) {
  const { openUpgrade, handleGated, reloadSubscription } = useUpgrade();
  const [aId, setAId] = useState("");
  const [bId, setBId] = useState("");
  const [a, setA] = useState(null);
  const [b, setB] = useState(null);
  const [loading, setLoading] = useState(false);
  const [gate, setGate] = useState(null);   // set to the 402 upgrade message

  const autoRan = useRef("");   // "a,b" of the last deep-link pair auto-run (once each)

  const runCompare = useCallback(async (idA, idB) => {
    if (!idA || !idB) return;
    setLoading(true); setGate(null);
    try {
      const { a: da, b: db } = await onCompare(idA, idB);
      setA(da); setB(db); reloadSubscription();
    } catch (e) {
      setA(null); setB(null);
      if (e instanceof ScanError && e.code === 402) {
        setGate(e.message);
        handleGated(e, "compares");
      }
    } finally { setLoading(false); }
  }, [onCompare, reloadSubscription, handleGated]);

  useEffect(() => {
    const preset = (queryParam("compare") || "").split(",").filter(Boolean);
    const valid = preset.filter((id) => scans.some((s) => String(s.id) === id));
    if (valid.length === 2) {
      setAId(valid[0]); setBId(valid[1]);
      const key = valid.join(",");
      if (autoRan.current !== key) { autoRan.current = key; runCompare(valid[0], valid[1]); }
      return;
    }
    if (scans.length >= 2) { setAId(scans[1].id); setBId(scans[0].id); }
    else if (scans.length === 1) { setBId(scans[0].id); }
  }, [scans, runCompare]);

  const opts = (v, set) => (
    <select className="au-select" value={v}
            onChange={(e) => { set(e.target.value); setA(null); setB(null); setGate(null); }}
            style={{ width: "100%" }}>
      <option value="">Select a scan…</option>
      {scans.map((s) => <option key={s.id} value={s.id}>{s.domain} · {s.overall_score ?? "-"} · {fmtDate(s.scan_time)}</option>)}
    </select>
  );

  const issueSet = (rep) => new Set((rep?.sections || []).flatMap((s) => s.issues || []));

  // Signal-level score/status deltas: the SAME shared utility Fix Verification uses
  // (see verification.js) — one comparison engine, not two.
  let improved = [], regressed = [], newIssues = [], resolved = [];
  if (a && b) {
    for (const d of diffSignals(a, b)) {
      if (!d.comparable) continue;
      if (d.score_after > d.score_before) improved.push({ label: d.label, before: d.score_before, after: d.score_after });
      else if (d.score_after < d.score_before) regressed.push({ label: d.label, before: d.score_before, after: d.score_after });
    }
    const ia = issueSet(a), ib = issueSet(b);
    newIssues = [...ib].filter((x) => !ia.has(x));
    resolved = [...ia].filter((x) => !ib.has(x));
  }

  return (
    <div className="aurora-screen">
      <Shell>
        <div className="au-cmp-pick">
          <Cell solid><div className="au-lbl">Previous scan</div><div style={{ marginTop: 10 }}>{opts(aId, setAId)}</div></Cell>
          <Cell solid><div className="au-lbl">Current scan</div><div style={{ marginTop: 10 }}>{opts(bId, setBId)}</div></Cell>
        </div>

        <div style={{ display: "flex", justifyContent: "center", margin: "14px 0" }}>
          <Button variant="accent" disabled={!aId || !bId || loading} onClick={() => runCompare(aId, bId)}>
            {loading ? "Comparing…" : "Compare"}
          </Button>
        </div>

        {gate ? (
          <Cell solid><div className="au-card-center">
            <div className="au-ill au-ill-warn"><Lock size={22} /></div>
            <div className="au-card-s">{gate}</div>
            <Button variant="accent" onClick={() => openUpgrade("compares", { message: gate })}>Upgrade to Pro</Button>
          </div></Cell>
        ) : (!a || !b) ? (
          <Cell solid><div className="au-dim" style={{ textAlign: "center", padding: 24 }}>
            {loading ? "Loading…" : (aId && bId) ? "Click Compare to run the comparison." : "Pick two scans to compare."}
          </div></Cell>
        ) : (
          <>
            <Cell solid style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 26, marginBottom: 16 }}>
              <Delta label={a.domain} value={a.overall_score} />
              <ArrowRight size={22} style={{ color: "var(--au-muted)" }} />
              <Delta label={b.domain} value={b.overall_score} />
              <ScoreDelta before={a.overall_score} after={b.overall_score} />
            </Cell>

            <div className="au-grid2">
              <DiffList title="Improved signals" items={improved.map((x) => `${x.label}: ${x.before} → ${x.after}`)} tone="up" />
              <DiffList title="Regressed signals" items={regressed.map((x) => `${x.label}: ${x.before} → ${x.after}`)} tone="down" />
              <DiffList title="Resolved issues" items={resolved} tone="up" />
              <DiffList title="New issues" items={newIssues} tone="down" />
            </div>
          </>
        )}
      </Shell>
    </div>
  );
}

function Delta({ label, value }) {
  return (
    <div style={{ textAlign: "center" }}>
      <span role="img" aria-label={`Score ${value ?? "not available"}`}><Ring value={value} size={64} stroke={6} /></span>
      <div className="au-dim" style={{ fontSize: 12, marginTop: 6, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</div>
    </div>
  );
}

function ScoreDelta({ before, after }) {
  const diff = (after ?? 0) - (before ?? 0);
  const Icon = diff > 0 ? TrendingUp : diff < 0 ? TrendingDown : Minus;
  const cls = diff > 0 ? "au-up" : diff < 0 ? "au-down" : "au-flat";
  return <div className={cls} style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 700, fontFamily: "var(--au-font-numeric)", fontSize: 18 }}><Icon size={18} /> {diff > 0 ? "+" : ""}{diff}</div>;
}

function DiffList({ title, items, tone }) {
  return (
    <Cell solid>
      <div className="au-panel-h">{title} <span className="au-sub">{items.length}</span></div>
      {items.length === 0 ? <div className="au-dim" style={{ fontSize: 12.5 }}>None</div> : (
        <div>
          {items.map((it, i) => (
            <div key={i} className="au-cmp-diff">
              <span className={tone === "up" ? "au-up" : "au-down"}>{tone === "up" ? "▲" : "▼"}</span>
              <span style={{ color: "var(--au-ink-2)" }}>{it}</span>
            </div>
          ))}
        </div>
      )}
    </Cell>
  );
}
