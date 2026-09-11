/* AI Visibility Report — MIGRATED to Aurora. Renders for the authed /app/report AND the
   public /r/:token share (readOnly). Data flow, hooks, effects, downloads, access/purchase
   logic are byte-for-byte unchanged; only JSX + class names changed. `.aurora-screen`-scoped.
   Out of scope (stay dark, flagged): <SharePanel> and <InsightsBody>/<ContentInsights> are
   separate components migrated in their own phases. */
import React, { useCallback, useEffect, useState } from "react";
import {
  FileDown, FileJson, FileSpreadsheet, Wrench, AlertTriangle,
  CheckCircle2, Zap, ChevronDown, Clock, Gauge, Lock, Sparkles, ListChecks,
} from "lucide-react";
import { getReport, getReportAccess, getContentInsights, downloadReport, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { InsightsBody } from "./ContentInsights.jsx";
import { SharePanel } from "./SharePanel.jsx";
import { Shell, Cell, Button, Ring } from "./aurora.jsx";
import "./ReportView.aurora.css";

const AU_PRIORITY = {
  Critical: "var(--au-peach-d)", High: "var(--au-peach-d)", Medium: "var(--au-lemon-d)", Low: "var(--au-muted)",
};
const auScoreColor = (v) => (v >= 75 ? "var(--au-mint-d)" : v >= 45 ? "var(--au-lemon-d)" : "var(--au-peach-d)");

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

  if (!readOnly && !scanId) return (
    <div className="aurora-screen"><Shell><Cell solid><div className="au-card-center au-dim">No scans yet — run a scan to generate your report.</div></Cell></Shell></div>
  );
  if (error) return (
    <div className="aurora-screen"><Shell><Cell solid><div className="au-card-center" role="alert">
      <div className="au-ill au-ill-bad"><AlertTriangle size={26} /></div>
      <div className="au-card-s" style={{ marginBottom: 20 }}>{error}</div>
      <Button variant="accent" onClick={load}>Retry</Button>
    </div></Cell></Shell></div>
  );
  if (!report) return (
    <div className="aurora-screen"><Shell><div className="au-stack">
      <Cell solid><div style={{ display: "grid", gap: 10 }}><div className="au-skel" style={{ height: 22, width: "45%" }} /><div className="au-skel" style={{ height: 12, width: "70%" }} /></div></Cell>
      <Cell solid><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 5 }).map((_, i) => <div key={i} className="au-skel" style={{ height: 44 }} />)}</div></Cell>
    </div></Shell></div>
  );

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
    <div className="aurora-screen">
      <Shell>
        <div className="au-stack">
          {/* header: score + downloads */}
          <Cell solid className="au-rep-head">
            <div className="au-rep-head-l">
              <span role="img" aria-label={`Overall score ${sc.overall_score}, grade ${sc.grade}`}><Ring value={sc.overall_score} size={72} stroke={7} /></span>
              <div>
                <div className="au-rep-domain">{report.domain || report.url}</div>
                <div className="au-rep-grade">
                  Grade <b style={{ color: auScoreColor(sc.overall_score) }}>{sc.grade}</b>
                  <span className="au-dim" style={{ marginLeft: 8 }}>· {recs.length} recommendation{recs.length === 1 ? "" : "s"}</span>
                </div>
              </div>
            </div>
            {!readOnly && (
              <div className="au-rep-actions">
                <Button variant="ghost" disabled={busy === "pdf"} onClick={() => onDownload("pdf")}
                        title={lockExports ? "Unlock exports" : "Download PDF"}>
                  {lockExports ? <Lock size={13} /> : <FileDown size={15} />} {busy === "pdf" ? "Preparing…" : "PDF"}
                </Button>
                <Button variant="ghost" disabled={busy === "json"} onClick={() => onDownload("json")}
                        title={lockExports ? "Unlock exports" : "Download JSON"}>
                  {lockExports ? <Lock size={13} /> : <FileJson size={15} />} JSON
                </Button>
                <Button variant="ghost" disabled={busy === "csv"} onClick={() => onDownload("csv")}
                        title={lockExports ? "Unlock exports" : "Download CSV"}>
                  {lockExports ? <Lock size={13} /> : <FileSpreadsheet size={15} />} CSV
                </Button>
                <SharePanel scanId={scanId} />
              </div>
            )}
          </Cell>
          {dlError && <div className="au-rep-dlerr"><AlertTriangle size={13} /> {dlError}</div>}
          {justUnlocked && !dlError && (
            <div className="au-rep-ok"><CheckCircle2 size={13} /> Exports unlocked ✓</div>
          )}

          {/* sticky in-page nav — jumps to each section */}
          <nav className="au-rep-nav">
            {NAV.map((n) => (
              <button key={n.id} className="au-rep-nav-btn" onClick={() => scrollTo(n.id)}>{n.label}</button>
            ))}
          </nav>

          {/* executive summary + counts */}
          <Cell solid id="rep-summary" style={{ scrollMarginTop: 120 }}>
            <div className="au-panel-h">
              Executive summary
              {ai?.executive_summary && <span className="au-rep-ai-tag"><Sparkles size={11} /> AI-written</span>}
            </div>
            <p className="au-rep-summary">{summaryText}</p>
            {ai?.executive_summary && (
              <div className="au-rep-ai-note"><Sparkles size={11} /> Narrative generated by Claude from this scan's findings.</div>
            )}
            <div className="au-rep-counts">
              {["Critical", "High", "Medium", "Low"].map((k) => (
                <div key={k} className="au-rep-count">
                  <div className="au-rep-count-n" style={{ color: AU_PRIORITY[k] }}>{sc.issue_counts?.[k] ?? 0}</div>
                  <div className="au-rep-count-l">{k}</div>
                </div>
              ))}
            </div>
          </Cell>

          {/* AI priority action plan (paid reports only) */}
          {(ai?.action_plan || []).length > 0 && (
            <Cell solid>
              <div className="au-panel-h">
                <ListChecks size={14} style={{ color: "var(--au-primary)" }} /> Priority action plan
                <span className="au-rep-ai-tag"><Sparkles size={11} /> AI-written</span>
              </div>
              <ol className="au-rep-plan">{ai.action_plan.map((step, i) => <li key={i}>{step}</li>)}</ol>
            </Cell>
          )}

          {/* Free tier: AI insights for the top issues + an upgrade teaser. */}
          {!readOnly && isFreeAi && (ai?.issue_insights || []).length > 0 && (
            <Cell solid>
              <div className="au-panel-h">
                <Sparkles size={14} style={{ color: "var(--au-primary)" }} /> Why your top issues matter
                <span className="au-rep-ai-tag"><Sparkles size={11} /> AI-written</span>
              </div>
              {ai.issue_insights.map((ins) => (
                <div key={ins.id} className="au-rep-ai-insight">
                  <div className="au-rep-ai-insight-t">{recsById[ins.id]?.issue_title || ins.id}</div>
                  {ins.why_it_matters && <p>{ins.why_it_matters}</p>}
                  {ins.priority_rationale && <p className="au-rep-ai-why-r">{ins.priority_rationale}</p>}
                </div>
              ))}
              <div className="au-rep-ai-teaser">
                <div className="au-rep-ai-teaser-t">
                  <Lock size={13} /> Full analysis for all {recs.length} issue{recs.length === 1 ? "" : "s"} + a prioritised action plan
                </div>
                <Button variant="accent" onClick={() => openPurchase()}>Unlock full report</Button>
              </div>
            </Cell>
          )}

          {/* category breakdown */}
          <div id="rep-categories" className="au-rep-grid2" style={{ scrollMarginTop: 120 }}>
            <Cell solid>
              <div className="au-panel-h">Category breakdown</div>
              {(sc.category_scores || []).map((c) => (
                <div key={c.category} className="au-bar">
                  <div className="au-bar-l">{c.category}</div>
                  <div className="au-bar-track"><div className="au-bar-fill" style={{ width: `${c.score}%`, background: auScoreColor(c.score) }} /></div>
                  <div className="au-bar-v" style={{ color: auScoreColor(c.score) }}>{c.score}</div>
                </div>
              ))}
            </Cell>
            <Cell solid>
              <div className="au-panel-h">Strengths &amp; weaknesses</div>
              <div className="au-rep-sw-h"><CheckCircle2 size={13} style={{ color: "var(--au-mint-d)" }} /> Strengths</div>
              {(sc.strengths || []).length ? sc.strengths.map((s) => (
                <div key={s.label} className="au-rep-sw-row"><span>{s.label}</span><span className="au-mono" style={{ color: "var(--au-mint-d)" }}>{s.score}</span></div>
              )) : <div className="au-dim" style={{ fontSize: 12.5, padding: "2px 0 8px" }}>None scored 75+ yet.</div>}
              <div className="au-rep-sw-h" style={{ marginTop: 10 }}><AlertTriangle size={13} style={{ color: "var(--au-peach-d)" }} /> Weaknesses</div>
              {(sc.weaknesses || []).length ? sc.weaknesses.map((w) => (
                <div key={w.label} className="au-rep-sw-row"><span>{w.label}</span><span className="au-mono" style={{ color: "var(--au-peach-d)" }}>{w.score}</span></div>
              )) : <div className="au-dim" style={{ fontSize: 12.5, padding: "2px 0" }}>No critical weaknesses. 🎉</div>}
            </Cell>
          </div>

          {/* quick wins */}
          {hasQuickWins && (
            <Cell solid id="rep-quickwins" style={{ scrollMarginTop: 120 }}>
              <div className="au-panel-h"><Zap size={14} style={{ color: "var(--au-lemon-d)" }} /> Quick wins <span className="au-sub">high impact, under an hour each</span></div>
              <div className="au-rep-qw">
                {sc.quick_wins.map((q) => (
                  <div key={q.id} className="au-rep-qw-item">
                    <span className="au-rep-pri" style={{ background: AU_PRIORITY[q.priority] }}>{q.priority}</span>
                    <span className="au-rep-qw-t">{q.issue_title}</span>
                    <span className="au-dim au-mono" style={{ fontSize: 11 }}><Clock size={11} /> {q.estimated_fix_time}</span>
                  </div>
                ))}
              </div>
            </Cell>
          )}

          {/* recommendations */}
          <div id="rep-recommendations" className="au-panel-h" style={{ margin: "8px 0 0", scrollMarginTop: 120 }}>
            Recommendations <span className="au-sub">what's wrong, why it matters, and how to fix it</span>
          </div>
          {recs.length === 0 ? (
            <Cell solid><div className="au-dim" style={{ textAlign: "center", padding: 20 }}>No issues found — this site is in excellent shape for AI visibility. 🎉</div></Cell>
          ) : unlocked ? (
            <div className="au-rep-list">{recs.map((r, i) => <RecCard key={r.id + i} r={r} idx={i + 1} ins={aiById[r.id]} />)}</div>
          ) : (
            <>
              {/* Free: the first recommendation is shown in full as a teaser… */}
              <div className="au-rep-list"><RecCard r={recs[0]} idx={1} /></div>
              {/* …the rest are blurred behind an unlock overlay. */}
              {recs.length > 1 && (
                <div className="au-rep-lockwrap">
                  <div className="au-rep-list au-rep-blur" aria-hidden="true">
                    {recs.slice(1).map((r, i) => <RecCard key={r.id + (i + 1)} r={r} idx={i + 2} defaultOpen={false} />)}
                  </div>
                  <div className="au-rep-lock-overlay">
                    <div className="au-rep-lock-t">Unlock all {recs.length} recommendations</div>
                    <div className="au-rep-lock-s">See every fix — with business impact, code examples and exports.</div>
                    <Button variant="accent" onClick={() => openPurchase()}><Lock size={14} /> Unlock all recommendations</Button>
                  </div>
                </div>
              )}
            </>
          )}

          {/* AI Content Insights already generated for this scan (Feature B, read-only). */}
          {ciInsights.length > 0 && (
            <Cell solid>
              <div className="au-panel-h">
                <Sparkles size={14} style={{ color: "var(--au-primary)" }} /> Content insights
                <span className="au-sub">AI tone, clarity &amp; structure by page</span>
              </div>
              {ciInsights.map((ci) => (
                <div key={ci.page_url} className="au-rep-ci-page">
                  <div className="au-rep-ci-url">{ci.page_url}</div>
                  <InsightsBody data={ci.data} />
                </div>
              ))}
            </Cell>
          )}
        </div>
      </Shell>
    </div>
  );
}

function RecCard({ r, idx, defaultOpen, ins }) {
  const [open, setOpen] = useState(defaultOpen ?? idx === 1);   // collapsed by default; first expanded
  const fx = r.fix_template || {};
  return (
    <div className="au-rep-card">
      <button className="au-rep-card-h" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="au-rep-pri" style={{ background: AU_PRIORITY[r.priority] }}>{r.priority}</span>
        <span className="au-rep-card-t">{idx}. {r.issue_title}</span>
        <span className="au-rep-card-meta">{r.category} · score <b style={{ color: auScoreColor(r.score) }}>{r.score}</b>{r.estimated_fix_time ? ` · ${r.estimated_fix_time}` : ""}</span>
        <ChevronDown size={16} className="au-rep-chev" style={{ transform: open ? "rotate(180deg)" : "none" }} />
      </button>
      {open && (
        <div className="au-rep-card-b">
          <p className="au-rep-desc">{r.description}</p>
          {ins?.why_it_matters && (
            <div className="au-rep-ai-why">
              <div className="au-rep-ai-why-h"><Sparkles size={12} /> Why it matters</div>
              <p>{ins.why_it_matters}</p>
              {ins.priority_rationale && <p className="au-rep-ai-why-r">{ins.priority_rationale}</p>}
            </div>
          )}
          <div className="au-rep-impact">
            <div><div className="au-rep-impact-l"><Gauge size={12} /> Business impact</div><div>{r.business_impact}</div></div>
            <div><div className="au-rep-impact-l"><Gauge size={12} /> AI visibility impact</div><div>{r.ai_visibility_impact}</div></div>
          </div>
          <div className="au-rep-meta-row">
            <span><Clock size={12} /> {r.estimated_fix_time}</span>
            <span><Wrench size={12} /> {r.difficulty}</span>
            <span>Severity: <b>{r.severity}</b></span>
          </div>
          {fx.problem && <><div className="au-rep-fx-h">Problem</div><p className="au-rep-desc">{fx.problem}</p></>}
          {fx.explanation && <><div className="au-rep-fx-h">Explanation</div><p className="au-rep-desc">{fx.explanation}</p></>}
          {(fx.recommended_fix || []).length > 0 && (
            <><div className="au-rep-fx-h">Recommended fix</div>
              <ul className="au-rep-fx-ul">{fx.recommended_fix.map((s, i) => <li key={i}><Wrench size={11} /> <span>{s}</span></li>)}</ul></>
          )}
          {fx.implementation_example && (
            <><div className="au-rep-fx-h">Implementation example</div>
              <pre className="au-code">{fx.implementation_example}</pre></>
          )}
          {fx.expected_outcome && <><div className="au-rep-fx-h">Expected outcome</div><p className="au-rep-outcome"><CheckCircle2 size={12} /> {fx.expected_outcome}</p></>}
        </div>
      )}
    </div>
  );
}
