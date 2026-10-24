/* AI Content Insights (Feature B). Pro users analyze a page's writing (tone / clarity
   / structure meters, specific suggestions, a before/after rewrite); Free users see a
   lock that opens the upgrade modal. <InsightsBody> is the read-only renderer reused by
   ReportView to surface insights that already exist for a scan.

   Spectrum migration: this is an embedded CARD (rendered inside ScanDetails/ReportView),
   not a route-level screen, so — same as SharePanel's sp-share-* classes — it carries no
   `.spectrum-screen`/`<Shell>` wrapper of its own; it just uses the shared `sp-panel` card
   plus the screen-specific `sp-ci-*` rules in ContentInsights.spectrum.css for shapes the
   shared classes don't cover. */
import React, { useCallback, useEffect, useState } from "react";
import { Sparkles, Lock, Wand2, AlertTriangle, Check } from "lucide-react";
import { analyzeContent, getContentInsights, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { spectrumRingColor } from "../shared/spectrum.jsx";
import "./ContentInsights.spectrum.css";

function Meter({ label, meter }) {
  const s = meter?.score_0_100 ?? 0;
  const color = spectrumRingColor(s);
  return (
    <div className="sp-ci-meter">
      <div className="sp-ci-meter-top">
        <span className="sp-ci-meter-l">{label}</span>
        <span className="sp-ci-meter-n sp-mono" style={{ color }}>{s}</span>
      </div>
      <div className="sp-ci-meter-track"><div style={{ width: `${s}%`, background: color }} /></div>
      {meter?.assessment && <div className="sp-ci-meter-a">{meter.assessment}</div>}
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
    <div className={`sp-ci-block ${className}`}>
      <div className="sp-ci-h">{label} — what to fix</div>
      <ol className="sp-ci-ol">{fixes.map((f, i) => <li key={i}>{f}</li>)}</ol>
      {implementation && (
        <>
          <div className="sp-ci-h" style={{ marginTop: 8 }}>Implementation</div>
          <pre className="sp-code">{implementation}</pre>
        </>
      )}
    </div>
  );
}

export function InsightsBody({ data }) {
  if (!data) return null;
  const rw = data.rewrite_example || {};
  return (
    <div className="sp-ci-body">
      <div className="sp-ci-meters">
        <Meter label="Tone" meter={data.tone} />
        <Meter label="Clarity" meter={data.clarity} />
        <Meter label="Structure" meter={data.structure} />
      </div>

      <FixBlock label="Tone" meter={data.tone} className="sp-ci-fix-tone" />
      <FixBlock label="Clarity" meter={data.clarity} className="sp-ci-fix-clarity" />
      <FixBlock label="Structure" meter={data.structure} className="sp-ci-fix-structure" />

      {(data.suggestions || []).length > 0 && (
        <div className="sp-ci-block">
          <div className="sp-ci-h">Suggestions</div>
          <ul className="sp-ci-ul">{data.suggestions.map((s, i) => <li key={i}><Check size={12} /> <span>{s}</span></li>)}</ul>
        </div>
      )}
      {(rw.before || rw.after) && (
        <div className="sp-ci-block">
          <div className="sp-ci-h">Rewrite example</div>
          <div className="sp-ci-ba">
            <div className="sp-ci-ba-col"><div className="sp-ci-ba-l">Before</div><p>{rw.before}</p></div>
            <div className="sp-ci-ba-col sp-ci-ba-after"><div className="sp-ci-ba-l">After</div><p>{rw.after}</p></div>
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
    <div className="sp-panel" style={{ marginTop: 16 }}>
      <div className="sp-panel-h">
        <Sparkles size={14} style={{ color: "var(--sp-primary)" }} /> Content Insights
        <span className="sp-sub">AI tone, clarity &amp; structure</span>
      </div>

      {locked ? (
        <div className="sp-ci-lock">
          <div className="sp-ci-lock-t"><Lock size={13} /> AI content analysis is a Pro feature</div>
          <div className="sp-ci-lock-s">Tone, clarity and structure scores with concrete rewrite suggestions for this page.</div>
          <button className="sp-btn sp-gradient" onClick={() => openUpgrade("ai_content")}>
            <Lock size={14} /> Unlock Content Insights
          </button>
        </div>
      ) : data ? (
        <>
          <InsightsBody data={data} />
          <button className="sp-iconbtn" style={{ marginTop: 10 }} disabled={loading} onClick={run}>
            <Wand2 size={13} className={loading ? "spin-slow" : ""} /> {loading ? "Analyzing…" : "Re-analyze"}
          </button>
          {loading && <div className="sp-dim" style={{ fontSize: 12, marginTop: 8 }}>This can take up to a minute.</div>}
          {err && <div className="sp-ci-err"><AlertTriangle size={13} /> {err}</div>}
        </>
      ) : (
        <div className="sp-ci-empty">
          <div className="sp-dim" style={{ fontSize: 13, marginBottom: 12 }}>
            Analyze this page's writing with AI — tone, clarity and structure, plus concrete rewrite suggestions.
          </div>
          <button className="sp-btn sp-gradient" disabled={loading} onClick={run}>
            <Wand2 size={14} className={loading ? "spin-slow" : ""} /> {loading ? "Analyzing…" : "Analyze content"}
          </button>
          {loading && <div className="sp-dim" style={{ fontSize: 12, marginTop: 8 }}>This can take up to a minute.</div>}
          {err && <div className="sp-ci-err"><AlertTriangle size={13} /> {err}</div>}
        </div>
      )}
    </div>
  );
}
