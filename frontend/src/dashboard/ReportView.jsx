/* AI Visibility Report (Phase 6): the actionable report for a scan — scorecard,
   category breakdown, strengths/weaknesses, quick wins, top priorities, and rich
   per-issue recommendations, plus PDF / JSON / CSV downloads and Share (future). */
import React, { useCallback, useEffect, useState } from "react";
import {
  FileDown, FileJson, FileSpreadsheet, Wrench, AlertTriangle,
  CheckCircle2, Zap, ChevronDown, Clock, Gauge, Lock, Sparkles, ListChecks,
} from "lucide-react";
import { getReport, getReportAccess, getContentInsights, downloadReport, ScanError } from "../api.js";
import { ScoreRing, scoreColor, TableSkeleton, ErrorState } from "./ui.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { InsightsBody } from "./ContentInsights.jsx";
import { SharePanel } from "./SharePanel.jsx";

const PRIORITY_COLOR = {
  Critical: "var(--bad)", High: "#E0722A", Medium: "var(--warn)", Low: "var(--txt-mid)",
};

// `readOnly` renders the report for a PUBLIC share (/r/:token): no exports, no Share,
// no upgrade CTAs, no authenticated fetches — the report is passed in via `report`.
export default function ReportView({ scanId, readOnly = false, report: reportProp = null }) {
  const { isLimited, openUpgrade } = useUpgrade();
  const [report, setReport] = useState(reportProp);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);        // which download is in flight
  const [dlError, setDlError] = useState(null);
  const [access, setAccess] = useState(null);    // {unlocked} from server; null while loading
  const [justUnlocked, setJustUnlocked] = useState(false);
  const [ciInsights, setCiInsights] = useState([]);   // AI content insights already generated

  // In read-only (public) mode the payload is already the full whitelisted report, so
  // everything is "unlocked" for rendering — but every action path is removed below, so
  // nothing authenticated is reachable. Otherwise the server's scan-scoped access check
  // is authoritative (Pro OR a $9 purchase of THIS scan); while it loads, fall back to
  // the plan so the UI doesn't flash the wrong state.
  const unlocked = readOnly ? true : (access ? access.unlocked : !isLimited);

  const load = useCallback(async () => {
    if (readOnly || !scanId) return;
    setError(null); setReport(null); setAccess(null); setJustUnlocked(false);
    try { setReport(await getReport(scanId)); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not load the report."); }
  }, [scanId, readOnly]);

  const loadAccess = useCallback(async () => {
    if (readOnly || !scanId) return;
    try { setAccess(await getReportAccess(scanId)); }
    catch { /* keep the plan-based fallback */ }
  }, [scanId, readOnly]);

  useEffect(() => {
    if (readOnly) { setReport(reportProp); return; }   // public: no authenticated fetch
    load(); loadAccess();
  }, [readOnly, reportProp, load, loadAccess]);

  // Surface any AI content insights already generated for this scan (read-only view).
  // Skipped for a public share — the public payload never carries content insights.
  useEffect(() => {
    if (readOnly || !scanId) return;
    setCiInsights([]);
    getContentInsights(scanId).then((r) => setCiInsights(r.insights || [])).catch(() => {});
  }, [scanId, readOnly]);

  // Called after a successful $9 purchase (dev flow completes inline). Unlock, refresh
  // authoritative access, and auto-run the download the user originally asked for.
  const onPurchased = useCallback(async (fmt) => {
    setAccess({ unlocked: true });
    setJustUnlocked(true);
    loadAccess();
    if (fmt) { try { await downloadReport(scanId, fmt); } catch { /* user can retry */ } }
  }, [scanId, loadAccess]);

  const openPurchase = (fmt) =>
    openUpgrade("report", { scanId, onSuccess: () => onPurchased(fmt) });

  const onDownload = async (fmt) => {
    if (!unlocked) { openPurchase(fmt); return; }     // locked → purchase, then auto-download
    setBusy(fmt); setDlError(null);
    try { await downloadReport(scanId, fmt); }
    catch (e) {
      // Belt & braces: a 402 from the export endpoint opens the same purchase modal.
      if (e instanceof ScanError && e.code === 402) openPurchase(fmt);
      else setDlError(e instanceof ScanError ? e.message : "Download failed.");
    } finally { setBusy(null); }
  };

  if (!readOnly && !scanId) return <div className="d-panel d-dim" style={{ textAlign: "center", padding: 32 }}>No scans yet — run a scan to generate your report.</div>;
  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!report) return <TableSkeleton rows={5} />;

  const sc = report.scorecard || {};
  const recs = report.recommendations || [];
  const lockExports = !unlocked;
  const hasQuickWins = (sc.quick_wins || []).length > 0;
  // Feature A: AI narrative (present only on paid reports). Replaces the generic
  // template text where available; absent → everything renders exactly as before.
  const ai = report.ai || null;
  const aiById = ai ? Object.fromEntries((ai.issue_insights || []).map((i) => [i.id, i])) : {};
  const recsById = Object.fromEntries(recs.map((r) => [r.id, r]));
  const summaryText = ai?.executive_summary || sc.summary;
  // Free-tier narrative: summary + top-3 insights, no action plan. Paid narratives
  // carry their insights inside the (unlocked) recommendation cards instead.
  const isFreeAi = ai?._meta?.tier === "free";

  const scrollTo = (id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const NAV = [
    { id: "rep-summary", label: "Summary" },
    { id: "rep-categories", label: "Categories" },
    ...(hasQuickWins ? [{ id: "rep-quickwins", label: "Quick wins" }] : []),
    { id: "rep-recommendations", label: "Recommendations" },
  ];

  return (
    <div className="rep">
      {/* header: score + downloads */}
      <div className="d-panel rep-head">
        <div className="rep-head-l">
          <ScoreRing value={sc.overall_score} size={72} stroke={7} />
          <div>
            <div className="rep-domain">{report.domain || report.url}</div>
            <div className="rep-grade">
              Grade <b style={{ color: scoreColor(sc.overall_score) }}>{sc.grade}</b>
              <span className="d-dim" style={{ marginLeft: 8 }}>· {recs.length} recommendation{recs.length === 1 ? "" : "s"}</span>
            </div>
          </div>
        </div>
        {!readOnly && (
        <div className="rep-actions">
          <button className={`rep-dl${lockExports ? " locked" : ""}`} disabled={busy === "pdf"} onClick={() => onDownload("pdf")}
                  title={lockExports ? "Unlock exports" : "Download PDF"}>
            {lockExports ? <Lock size={13} className="lock-i" /> : <FileDown size={15} />} {busy === "pdf" ? "Preparing…" : "PDF"}
          </button>
          <button className={`rep-dl${lockExports ? " locked" : ""}`} disabled={busy === "json"} onClick={() => onDownload("json")}
                  title={lockExports ? "Unlock exports" : "Download JSON"}>
            {lockExports ? <Lock size={13} className="lock-i" /> : <FileJson size={15} />} JSON
          </button>
          <button className={`rep-dl${lockExports ? " locked" : ""}`} disabled={busy === "csv"} onClick={() => onDownload("csv")}
                  title={lockExports ? "Unlock exports" : "Download CSV"}>
            {lockExports ? <Lock size={13} className="lock-i" /> : <FileSpreadsheet size={15} />} CSV
          </button>
          <SharePanel scanId={scanId} />
        </div>
        )}
      </div>
      {dlError && <div className="rep-dlerr"><AlertTriangle size={13} /> {dlError}</div>}
      {justUnlocked && !dlError && (
        <div className="rep-unlocked"><CheckCircle2 size={13} /> Exports unlocked ✓</div>
      )}

      {/* sticky in-page nav — jumps to each section */}
      <nav className="rep-nav">
        {NAV.map((n) => (
          <button key={n.id} className="rep-nav-btn" onClick={() => scrollTo(n.id)}>{n.label}</button>
        ))}
      </nav>

      {/* executive summary + counts */}
      <div id="rep-summary" className="d-panel" style={{ marginTop: 14, scrollMarginTop: 120 }}>
        <div className="d-panel-h">
          Executive summary
          {ai?.executive_summary && <span className="rep-ai-tag"><Sparkles size={11} /> AI-written</span>}
        </div>
        <p className="rep-summary">{summaryText}</p>
        {ai?.executive_summary && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4,
                        fontSize: 11, color: "var(--txt-dim)" }}>
            <Sparkles size={11} /> Narrative generated by Claude from this scan's findings.
          </div>
        )}
        <div className="rep-counts">
          {["Critical", "High", "Medium", "Low"].map((k) => (
            <div key={k} className="rep-count">
              <div className="rep-count-n" style={{ color: PRIORITY_COLOR[k] }}>{sc.issue_counts?.[k] ?? 0}</div>
              <div className="rep-count-l">{k}</div>
            </div>
          ))}
        </div>
      </div>

      {/* AI priority action plan (paid reports only) */}
      {(ai?.action_plan || []).length > 0 && (
        <div className="d-panel" style={{ marginTop: 14 }}>
          <div className="d-panel-h">
            <ListChecks size={14} style={{ color: "var(--accent)" }} /> Priority action plan
            <span className="rep-ai-tag"><Sparkles size={11} /> AI-written</span>
          </div>
          <ol className="rep-plan">
            {ai.action_plan.map((step, i) => <li key={i}>{step}</li>)}
          </ol>
        </div>
      )}

      {/* Free tier: AI insights for the top issues + an upgrade teaser. Paid narratives
          render their insights inside the unlocked recommendation cards instead. */}
      {!readOnly && isFreeAi && (ai?.issue_insights || []).length > 0 && (
        <div className="d-panel" style={{ marginTop: 14 }}>
          <div className="d-panel-h">
            <Sparkles size={14} style={{ color: "var(--accent)" }} /> Why your top issues matter
            <span className="rep-ai-tag"><Sparkles size={11} /> AI-written</span>
          </div>
          {ai.issue_insights.map((ins) => (
            <div key={ins.id} className="rep-ai-insight">
              <div className="rep-ai-insight-t">{recsById[ins.id]?.issue_title || ins.id}</div>
              {ins.why_it_matters && <p>{ins.why_it_matters}</p>}
              {ins.priority_rationale && <p className="rep-ai-why-r">{ins.priority_rationale}</p>}
            </div>
          ))}
          <div className="rep-ai-teaser">
            <div className="rep-ai-teaser-t">
              <Lock size={13} /> Full analysis for all {recs.length} issue{recs.length === 1 ? "" : "s"} + a prioritised action plan
            </div>
            <button className="btn btn-primary" onClick={() => openPurchase()}>Unlock full report</button>
          </div>
        </div>
      )}

      {/* category breakdown */}
      <div id="rep-categories" className="d-grid d-grid-2" style={{ marginTop: 14, scrollMarginTop: 120 }}>
        <div className="d-panel">
          <div className="d-panel-h">Category breakdown</div>
          <div className="rep-cats">
            {(sc.category_scores || []).map((c) => (
              <div key={c.category} className="rep-cat">
                <div className="rep-cat-l">{c.category}</div>
                <div className="rep-cat-track"><div style={{ width: `${c.score}%`, background: scoreColor(c.score) }} /></div>
                <div className="rep-cat-n d-mono" style={{ color: scoreColor(c.score) }}>{c.score}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="d-panel">
          <div className="d-panel-h">Strengths & weaknesses</div>
          <div className="rep-sw-h"><CheckCircle2 size={13} style={{ color: "var(--good)" }} /> Strengths</div>
          {(sc.strengths || []).length ? sc.strengths.map((s) => (
            <div key={s.label} className="rep-sw-row"><span>{s.label}</span><span className="d-mono" style={{ color: "var(--good)" }}>{s.score}</span></div>
          )) : <div className="d-dim" style={{ fontSize: 12.5, padding: "2px 0 8px" }}>None scored 75+ yet.</div>}
          <div className="rep-sw-h" style={{ marginTop: 10 }}><AlertTriangle size={13} style={{ color: "var(--bad)" }} /> Weaknesses</div>
          {(sc.weaknesses || []).length ? sc.weaknesses.map((w) => (
            <div key={w.label} className="rep-sw-row"><span>{w.label}</span><span className="d-mono" style={{ color: "var(--bad)" }}>{w.score}</span></div>
          )) : <div className="d-dim" style={{ fontSize: 12.5, padding: "2px 0" }}>No critical weaknesses. 🎉</div>}
        </div>
      </div>

      {/* quick wins */}
      {hasQuickWins && (
        <div id="rep-quickwins" className="d-panel" style={{ marginTop: 14, scrollMarginTop: 120 }}>
          <div className="d-panel-h"><Zap size={14} style={{ color: "var(--warn)" }} /> Quick wins <span className="sub">high impact, under an hour each</span></div>
          <div className="rep-qw">
            {sc.quick_wins.map((q) => (
              <div key={q.id} className="rep-qw-item">
                <span className="rep-pri" style={{ background: PRIORITY_COLOR[q.priority] }}>{q.priority}</span>
                <span className="rep-qw-t">{q.issue_title}</span>
                <span className="d-dim d-mono" style={{ fontSize: 11 }}><Clock size={11} /> {q.estimated_fix_time}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* recommendations */}
      <div id="rep-recommendations" className="d-panel-h" style={{ margin: "20px 0 10px", scrollMarginTop: 120 }}>
        Recommendations <span className="sub">what's wrong, why it matters, and how to fix it</span>
      </div>
      {recs.length === 0 ? (
        <div className="d-panel d-dim" style={{ textAlign: "center", padding: 28 }}>No issues found — this site is in excellent shape for AI visibility. 🎉</div>
      ) : unlocked ? (
        <div className="rep-list">{recs.map((r, i) => <RecCard key={r.id + i} r={r} idx={i + 1} ins={aiById[r.id]} />)}</div>
      ) : (
        <>
          {/* Free: the first recommendation is shown in full as a teaser… */}
          <div className="rep-list"><RecCard r={recs[0]} idx={1} /></div>
          {/* …the rest are blurred behind an unlock overlay. */}
          {recs.length > 1 && (
            <div className="rep-lockwrap" style={{ marginTop: 10 }}>
              <div className="rep-list rep-blur" aria-hidden="true">
                {recs.slice(1).map((r, i) => <RecCard key={r.id + (i + 1)} r={r} idx={i + 2} defaultOpen={false} />)}
              </div>
              <div className="rep-lock-overlay">
                <div className="rep-lock-t">Unlock all {recs.length} recommendations</div>
                <div className="rep-lock-s">See every fix — with business impact, code examples and exports.</div>
                <button className="btn btn-primary" onClick={() => openPurchase()}>
                  <Lock size={14} /> Unlock all recommendations
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {/* AI Content Insights already generated for this scan (Feature B, read-only). */}
      {ciInsights.length > 0 && (
        <div className="d-panel" style={{ marginTop: 18 }}>
          <div className="d-panel-h">
            <Sparkles size={14} style={{ color: "var(--accent)" }} /> Content insights
            <span className="sub">AI tone, clarity &amp; structure by page</span>
          </div>
          {ciInsights.map((ci) => (
            <div key={ci.page_url} className="rep-ci-page">
              <div className="rep-ci-url d-mono">{ci.page_url}</div>
              <InsightsBody data={ci.data} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RecCard({ r, idx, defaultOpen, ins }) {
  const [open, setOpen] = useState(defaultOpen ?? idx === 1);   // collapsed by default; first expanded
  const fx = r.fix_template || {};
  return (
    <div className="rep-card">
      <button className="rep-card-h" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="rep-pri" style={{ background: PRIORITY_COLOR[r.priority] }}>{r.priority}</span>
        <span className="rep-card-t">{idx}. {r.issue_title}</span>
        <span className="rep-card-meta d-dim">{r.category} · score <b style={{ color: scoreColor(r.score) }}>{r.score}</b>{r.estimated_fix_time ? ` · ${r.estimated_fix_time}` : ""}</span>
        <ChevronDown size={16} className="rep-chev" style={{ transform: open ? "rotate(180deg)" : "none" }} />
      </button>
      {open && (
        <div className="rep-card-b">
          <p className="rep-desc">{r.description}</p>
          {ins?.why_it_matters && (
            <div className="rep-ai-why">
              <div className="rep-ai-why-h"><Sparkles size={12} /> Why it matters</div>
              <p>{ins.why_it_matters}</p>
              {ins.priority_rationale && <p className="rep-ai-why-r">{ins.priority_rationale}</p>}
            </div>
          )}
          <div className="rep-impact">
            <div><div className="rep-impact-l"><Gauge size={12} /> Business impact</div><div>{r.business_impact}</div></div>
            <div><div className="rep-impact-l"><Gauge size={12} /> AI visibility impact</div><div>{r.ai_visibility_impact}</div></div>
          </div>
          <div className="rep-meta-row">
            <span><Clock size={12} /> {r.estimated_fix_time}</span>
            <span><Wrench size={12} /> {r.difficulty}</span>
            <span>Severity: <b>{r.severity}</b></span>
          </div>
          {fx.problem && <><div className="rep-fx-h">Problem</div><p>{fx.problem}</p></>}
          {fx.explanation && <><div className="rep-fx-h">Explanation</div><p>{fx.explanation}</p></>}
          {(fx.recommended_fix || []).length > 0 && (
            <><div className="rep-fx-h">Recommended fix</div>
              <ul className="rep-fx-ul">{fx.recommended_fix.map((s, i) => <li key={i}><Wrench size={11} /> <span>{s}</span></li>)}</ul></>
          )}
          {fx.implementation_example && (
            <><div className="rep-fx-h">Implementation example</div>
              <pre className="rep-code">{fx.implementation_example}</pre></>
          )}
          {fx.expected_outcome && <><div className="rep-fx-h">Expected outcome</div><p className="rep-outcome"><CheckCircle2 size={12} /> {fx.expected_outcome}</p></>}
        </div>
      )}
    </div>
  );
}
