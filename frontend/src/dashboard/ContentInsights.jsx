/* AI Content Insights (Feature B). Pro users analyze a page's writing (tone / clarity
   / structure meters, specific suggestions, a before/after rewrite); Free users see a
   lock that opens the upgrade modal. <InsightsBody> is the read-only renderer reused by
   ReportView to surface insights that already exist for a scan. */
import React, { useCallback, useEffect, useState } from "react";
import { Sparkles, Lock, Wand2, AlertTriangle, Check } from "lucide-react";
import { analyzeContent, getContentInsights, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";

/* Score colour on the light Aurora card (deep tokens, readable) — same thresholds as the
   app's auScoreColor; the shared dark scoreColor() was near-invisible on white. */
const ciScore = (v) => (v >= 75 ? "var(--au-mint-d)" : v >= 45 ? "var(--au-lemon-d)" : "var(--au-peach-d)");

function Meter({ label, meter }) {
  const s = meter?.score_0_100 ?? 0;
  return (
    <div className="ci-meter">
      <div className="ci-meter-top">
        <span className="ci-meter-l">{label}</span>
        <span className="ci-meter-n d-mono" style={{ color: ciScore(s) }}>{s}</span>
      </div>
      <div className="ci-meter-track"><div style={{ width: `${s}%`, background: ciScore(s) }} /></div>
      {meter?.assessment && <div className="ci-meter-a">{meter.assessment}</div>}
    </div>
  );
}

/* Fix-first: a meter's own concrete action list + a short worked-example
   implementation, immediately after the meters — real model output (see
   backend/app/scanner/ai_content.py's _meter_with_fixes, the ONE shared shape used
   for tone, clarity AND structure), never fabricated, honestly omitted when the
   model found nothing to fix for that meter. */
function FixBlock({ label, meter, className }) {
  const fixes = meter?.fixes || [];
  const implementation = meter?.implementation || "";
  if (fixes.length === 0) return null;
  return (
    <div className={`ci-block ${className}`}>
      <div className="ci-h">{label} — what to fix</div>
      <ol className="ci-ol">{fixes.map((f, i) => <li key={i}>{f}</li>)}</ol>
      {implementation && (
        <>
          <div className="ci-h" style={{ marginTop: 8 }}>Implementation</div>
          <pre className="au-code">{implementation}</pre>
        </>
      )}
    </div>
  );
}

export function InsightsBody({ data }) {
  if (!data) return null;
  const rw = data.rewrite_example || {};
  return (
    <div className="ci-body">
      <div className="ci-meters">
        <Meter label="Tone" meter={data.tone} />
        <Meter label="Clarity" meter={data.clarity} />
        <Meter label="Structure" meter={data.structure} />
      </div>

      <FixBlock label="Tone" meter={data.tone} className="ci-tone-fix" />
      <FixBlock label="Clarity" meter={data.clarity} className="ci-clarity-fix" />
      <FixBlock label="Structure" meter={data.structure} className="ci-structure-fix" />

      {(data.suggestions || []).length > 0 && (
        <div className="ci-block">
          <div className="ci-h">Suggestions</div>
          <ul className="ci-ul">{data.suggestions.map((s, i) => <li key={i}><Check size={12} /> <span>{s}</span></li>)}</ul>
        </div>
      )}
      {(rw.before || rw.after) && (
        <div className="ci-block">
          <div className="ci-h">Rewrite example</div>
          <div className="ci-ba">
            <div className="ci-ba-col"><div className="ci-ba-l">Before</div><p>{rw.before}</p></div>
            <div className="ci-ba-col ci-ba-after"><div className="ci-ba-l">After</div><p>{rw.after}</p></div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ContentInsightsCard({ scanId, pageUrl }) {
  const { isLimited, openUpgrade } = useUpgrade();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const locked = isLimited;   // Free + enforcement; Pro / billing-off => usable

  // Prefill from any cached insight so a previously-analyzed page renders instantly.
  useEffect(() => {
    let alive = true;
    if (!scanId || locked) return undefined;
    getContentInsights(scanId).then((r) => {
      if (!alive) return;
      const rows = r.insights || [];
      const match = pageUrl ? rows.find((x) => x.page_url === pageUrl) : rows[0];
      if (match) setData(match.data);
    }).catch(() => { /* best-effort prefill */ });
    return () => { alive = false; };
  }, [scanId, pageUrl, locked]);

  const run = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await analyzeContent(scanId, pageUrl);
      setData(r.insights);
    } catch (e) {
      if (e instanceof ScanError && e.code === 402) { openUpgrade("ai_content"); return; }
      setErr(e instanceof ScanError ? e.message : "Analysis failed.");
    } finally { setLoading(false); }
  }, [scanId, pageUrl, openUpgrade]);

  return (
    <div className="au-panel ci-card" style={{ marginTop: 16 }}>
      <div className="au-panel-h">
        <Sparkles size={14} style={{ color: "var(--au-primary)" }} /> Content Insights
        <span className="au-sub">AI tone, clarity &amp; structure</span>
      </div>

      {locked ? (
        <div className="ci-lock">
          <div className="ci-lock-t"><Lock size={13} /> AI content analysis is a Pro feature</div>
          <div className="ci-lock-s">Tone, clarity and structure scores with concrete rewrite suggestions for this page.</div>
          <button className="au-btn au-accent" onClick={() => openUpgrade("ai_content")}>
            <Lock size={14} /> Unlock Content Insights
          </button>
        </div>
      ) : data ? (
        <>
          <InsightsBody data={data} />
          <button className="au-iconbtn" style={{ marginTop: 10 }} disabled={loading} onClick={run}>
            <Wand2 size={13} className={loading ? "spin-slow" : ""} /> {loading ? "Analyzing…" : "Re-analyze"}
          </button>
          {loading && <div className="au-dim" style={{ fontSize: 12, marginTop: 8 }}>This can take up to a minute.</div>}
          {err && <div className="ci-err"><AlertTriangle size={13} /> {err}</div>}
        </>
      ) : (
        <div className="ci-empty">
          <div className="au-dim" style={{ fontSize: 13, marginBottom: 12 }}>
            Analyze this page's writing with AI — tone, clarity and structure, plus concrete rewrite suggestions.
          </div>
          <button className="au-btn au-accent" disabled={loading} onClick={run}>
            <Wand2 size={14} className={loading ? "spin-slow" : ""} /> {loading ? "Analyzing…" : "Analyze content"}
          </button>
          {loading && <div className="au-dim" style={{ fontSize: 12, marginTop: 8 }}>This can take up to a minute.</div>}
          {err && <div className="ci-err"><AlertTriangle size={13} /> {err}</div>}
        </div>
      )}
    </div>
  );
}
