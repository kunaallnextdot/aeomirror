/* AI Visibility Report — MIGRATED to Aurora. Renders for the authed /app/report AND the
   public /r/:token share (readOnly). Data flow, hooks, effects, downloads, access/purchase
   logic are byte-for-byte unchanged; only JSX + class names changed. `.aurora-screen`-scoped.
   Out of scope (stay dark, flagged): <SharePanel> and <InsightsBody>/<ContentInsights> are
   separate components migrated in their own phases. */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FileDown, FileJson, FileSpreadsheet, Wrench, AlertTriangle,
  CheckCircle2, Zap, ChevronDown, Clock, Gauge, Lock, Sparkles, ListChecks,
  Check, TrendingUp, Circle, History,
} from "lucide-react";
import { getReport, getReportAccess, getContentInsights, getVerifications, downloadReport, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { InsightsBody } from "./ContentInsights.jsx";
import { SharePanel } from "./SharePanel.jsx";
import { ReportAIVisibility } from "./AIVisibility.jsx";
import { ReportQuestionBank } from "./QuestionBank.jsx";
import VerificationBadge from "./VerificationBadge.jsx";
import DiagnosisCard, { SupportingEvidenceNote } from "./DiagnosisCard.jsx";
import {
  TECH_SEO_COVERED_BY_RECOMMENDATION, TECH_ISSUE_PROBLEM, TECH_ISSUE_FIX,
  ENTITY_SIGNAL_LABEL, CRAWL_ISSUE_TITLE, CRAWL_ISSUE_FIX, CRAWL_ISSUE_WHY,
} from "./actionSources.js";
import { Shell, Cell, Button, Ring } from "./aurora.jsx";
import "./ReportView.aurora.css";

export const AU_PRIORITY = {
  Critical: "var(--au-peach-d)", High: "var(--au-peach-d)", Medium: "var(--au-lemon-d)", Low: "var(--au-muted)",
};
const auScoreColor = (v) => (v >= 75 ? "var(--au-mint-d)" : v >= 45 ? "var(--au-lemon-d)" : "var(--au-peach-d)");

function fmtEv(v) {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (v == null) return "-";
  if (Array.isArray(v)) return v.length ? `${v.length}: ${v.slice(0, 3).join(", ")}${v.length > 3 ? "…" : ""}` : "none";
  if (typeof v === "object") return Object.entries(v).map(([k, val]) => `${k}=${val}`).join(", ");
  return String(v);
}

// Scrolls to (and, if collapsed, expands) the full recommendation card for a signal —
// lets the free-tier "What's hurting your score?" summary jump straight to the same
// real Problem/Evidence/Fix detail already rendered below, instead of duplicating it.
function jumpToRecommendation(signalId) {
  const card = document.getElementById(`rec-${signalId}`);
  if (!card) return;
  const head = document.getElementById(`rec-h-${signalId}`);
  if (head && head.getAttribute("aria-expanded") === "false") head.click();
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

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
  const [verifications, setVerifications] = useState([]);   // persisted Fix Verification records

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

  // Deep links from Action Center (and anywhere else) land on a specific real section
  // anchor, e.g. "#rep-schema" — the SAME id already rendered by that section below,
  // never a synthetic one. Only fires once the report has actually rendered (so the
  // target element exists) and only when the current URL really carries a hash.
  useEffect(() => {
    if (!report) return;
    const hash = window.location.hash?.slice(1);
    if (!hash) return;
    const el = document.getElementById(hash);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [report]);

  // Surface any AI content insights already generated for this scan (read-only view).
  // Skipped for a public share — the public payload never carries content insights.
  useEffect(() => {
    if (readOnly || !scanId) return;
    setCiInsights([]);
    getContentInsights(scanId).then((r) => setCiInsights(r.insights || [])).catch(() => {});
  }, [scanId, readOnly]);

  // Persisted Fix Verification records for this scan (see Scan Details' own fuller
  // panel — this is only a compact badge). Skipped for a public share; a locked
  // caller simply never gets a record for a signal they can't see anyway.
  useEffect(() => {
    if (readOnly || !scanId) return;
    setVerifications([]);
    getVerifications(scanId).then((r) => setVerifications(r.verifications || [])).catch(() => {});
  }, [scanId, readOnly]);
  const verificationBySignal = useMemo(() => {
    const map = {};
    for (const v of verifications) if (!map[v.signal_id]) map[v.signal_id] = v;
    return map;
  }, [verifications]);

  // Called after a successful $9 purchase (dev flow completes inline). Unlock, refresh
  // authoritative access, and auto-run the download the user originally asked for.
  const onPurchased = useCallback(async (fmt) => {
    setAccess({ unlocked: true });
    setJustUnlocked(true);
    loadAccess();
    if (fmt) { try { await downloadReport(scanId, fmt); } catch { /* user can retry */ } }
  }, [scanId, loadAccess]);

  // Accepts either a format string (from a locked download) or an options object
  // ({ message }) so the recommendation paywall can pass its own "X more fixes" copy.
  const openPurchase = (arg) => {
    const opts = typeof arg === "string" ? { fmt: arg } : (arg || {});
    openUpgrade("report", { scanId, message: opts.message,
                            onSuccess: () => onPurchased(opts.fmt) });
  };

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
  // A locked (free) caller already receives ONLY the free preview recommendations from
  // the server (see backend `gate_recommendations`) — `locked_recommendation_count`
  // tells the real total, there is nothing further to slice or blur client-side.
  const recs = report.recommendations || [];
  const lockedCount = report.locked_recommendation_count || 0;
  const lockExports = !unlocked;
  const hasQuickWins = (sc.quick_wins || []).length > 0;
  // Phase 1: negative-first read-side insights (present on every report; free data).
  const ins = report.insights || null;
  const hasProblems = (ins?.top_problems || []).length > 0;
  const hasBreakdown = (ins?.score_breakdown || []).length > 0;
  // Phase 4: Schema / Internal Link / Entity intelligence + scan-only Question Mining.
  // Already server-gated (see backend `gate_phase4`) — a locked caller's `phase4.*`
  // already IS the free preview (small real lists + `locked_*_count`), never full data
  // to slice client-side.
  const p4 = report.phase4 || null;
  // Phase 2: simulator + 30-day action plan. For a locked (free) caller the server has
  // ALREADY trimmed these to a preview (see backend `gate_insights`) — there is no full
  // data to slice client-side, so the preview shape (`preview_tasks`/`locked_*_count`)
  // is read directly rather than derived from `items.length` / week buckets.
  const simItems = ins?.score_impact?.items || [];
  const hasSimulator = simItems.length > 0;
  const ap = ins?.action_plan || null;
  const planTaskCount = ap
    ? (unlocked
        ? ["week_1", "week_2", "week_3", "week_4", "backlog"].reduce((n, k) => n + (ap[k] || []).length, 0)
        : (ap.total_tasks ?? (ap.preview_tasks || []).length))
    : 0;
  const hasPlan = planTaskCount > 0;
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
    ...(hasProblems ? [{ id: "rep-whats-hurting", label: "What's hurting" }] : []),
    ...(hasBreakdown ? [{ id: "rep-breakdown", label: "Breakdown" }] : []),
    { id: "rep-categories", label: "Categories" },
    ...(hasQuickWins ? [{ id: "rep-quickwins", label: "Quick wins" }] : []),
    ...(hasSimulator ? [{ id: "rep-simulator", label: "Simulator" }] : []),
    ...(hasPlan ? [{ id: "rep-plan", label: "Action plan" }] : []),
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
            {ins?.issue_count > 0 && (
              <p className="au-rep-found">
                <AlertTriangle size={14} /> We found <b>{ins.issue_count}</b> problem{ins.issue_count === 1 ? "" : "s"} affecting your AI visibility
                {ins.total_points_lost > 0 && <> — costing about <b>{ins.total_points_lost}</b> points.</>}
              </p>
            )}
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

          {/* B. What's hurting your score? — negative-first, evidence-backed (free). */}
          {hasProblems && (
            <Cell solid id="rep-whats-hurting" style={{ scrollMarginTop: 120 }}>
              <div className="au-panel-h">
                <AlertTriangle size={14} style={{ color: "var(--au-peach-d)" }} /> What's hurting your score?
                <span className="au-sub">the biggest reasons your AI visibility is losing points</span>
              </div>
              <div className="au-rep-probs">
                {ins.top_problems.map((p) => {
                  const evEntries = Object.entries(p.evidence || {}).filter(([k]) => k !== "detected_types");
                  return (
                    <div key={p.signal_id} className="au-rep-prob">
                      <div className="au-rep-prob-top">
                        <span className="au-rep-prob-name">{p.signal}</span>
                        <span className="au-rep-prob-score" style={{ color: auScoreColor(p.score) }}>{p.score}<span className="au-rep-prob-max">/100</span></span>
                        <span className="au-rep-prob-loss">−{p.points_lost} pts</span>
                      </div>
                      <div className="au-rep-prob-issue">{p.issue}</div>
                      {evEntries.length > 0 && (
                        <div className="au-rep-prob-ev">
                          {evEntries.slice(0, 3).map(([k, v]) => (
                            <span key={k} className="au-rep-prob-ev-item"><b>{k}:</b> {fmtEv(v)}</span>
                          ))}
                        </div>
                      )}
                      {p.recommendation_preview && <div className="au-rep-prob-prev"><Wrench size={11} /> {p.recommendation_preview}</div>}
                      <button className="au-rep-prob-jump" onClick={() => jumpToRecommendation(p.signal_id)}>
                        See full fix →
                      </button>
                    </div>
                  );
                })}
              </div>
            </Cell>
          )}

          {/* C. Score breakdown — every signal, points lost (free). */}
          {hasBreakdown && (
            <Cell solid id="rep-breakdown" style={{ scrollMarginTop: 120 }}>
              <div className="au-panel-h">Score breakdown <span className="au-sub">points lost per signal (weight × gap to 100)</span></div>
              <div className="au-rep-bd">
                {ins.score_breakdown.map((s) => (
                  <div key={s.signal_id} className="au-rep-bd-row">
                    <div className="au-rep-bd-name">{s.label}</div>
                    <div className="au-rep-bd-track"><div className="au-rep-bd-fill" style={{ width: `${s.score}%`, background: auScoreColor(s.score) }} /></div>
                    <div className="au-rep-bd-score" style={{ color: auScoreColor(s.score) }}>{s.score}</div>
                    <div className={`au-rep-bd-loss${s.points_lost > 0 ? " neg" : ""}`}>{s.points_lost > 0 ? `−${s.points_lost}` : "0"}</div>
                  </div>
                ))}
              </div>
            </Cell>
          )}

          {/* AI Visibility (Phase 3) — the site's AI answer coverage, from Answer Tracking.
              Authenticated report only (public shares have no monitor/AT context). */}
          {!readOnly && scanId && <div id="rep-ai-visibility" style={{ scrollMarginTop: 120 }}><ReportAIVisibility scanId={scanId} /></div>}

          {/* Phase 4 — deeper AEO intelligence, derived from the same scan evidence
              above. Free/paid is server-gated (report.phase4); nothing here is blurred
              real content — a locked caller's data already IS the free preview.
              `available: false` means the underlying scan hasn't finished yet (a
              pending/running/failed bulk scan) — an honest "not ready" message, never
              a fabricated "everything is missing" diagnosis for pages never analyzed. */}
          {p4?.available === false && (
            <Cell solid id="rep-phase4-pending" style={{ scrollMarginTop: 120 }}>
              <div className="au-panel-h">Deeper AEO Intelligence</div>
              <div className="au-dim" style={{ fontSize: 13 }}>
                Scan still in progress — insufficient evidence to generate Schema, Internal
                Link, Entity, and Question intelligence until the scan completes.
              </div>
            </Cell>
          )}
          {p4?.available !== false && p4 && (
            <>
              <SchemaIntelligence data={p4.schema} unlocked={unlocked} recommendations={report.recommendations}
                                  onUnlock={() => openPurchase({ message: "Unlock the complete schema diagnosis for every missing type." })} />
              <LinkIntelligence data={p4.links} unlocked={unlocked}
                                onUnlock={() => openPurchase({ message: "Unlock the complete internal-link diagnosis." })} />
              <EntityIntelligence data={p4.entity} unlocked={unlocked} recommendations={report.recommendations}
                                  onUnlock={() => openPurchase({ message: "Unlock complete entity evidence." })} />
              <QuestionMining data={p4.questions} unlocked={unlocked}
                              onUnlock={() => openPurchase({ message: "Unlock the complete question mining results." })} />
            </>
          )}

          {/* Technical SEO & Indexability — for every crawled URL, technically-grounded
              crawlability/indexability signals (never a claim about actual Google
              indexing — see backend `reports/technical_seo.py`). Free/paid is
              server-gated (report.technical_seo); a locked caller's data already IS
              the free preview. */}
          <TechnicalSeoIntelligence data={report.technical_seo} unlocked={unlocked} recommendations={report.recommendations}
                                    onUnlock={() => openPurchase({ message: "Unlock the complete Technical SEO & Indexability report for every crawled URL." })} />

          {/* Real Crawl Graph & Internal Linking — for a multi-page (bulk) scan, the
              real internal-link graph: reachability/depth from the crawl seed and true
              orphan pages (zero inbound internal links from other crawled pages). Never
              available for a single-page scan (see backend `reports/crawl_graph.py`).
              Free/paid is server-gated (report.crawl_graph). */}
          <CrawlGraphIntelligence data={report.crawl_graph} unlocked={unlocked}
                                  onUnlock={() => openPurchase({ message: "Unlock the complete crawl graph — every orphan page, edge, and disconnected page." })} />

          {/* Content Cannibalization & Duplicate Content Intelligence — for a
              multi-page (bulk) scan, near-duplicate/overlap/potential-cannibalization
              clusters, duplicate page elements, and thin-content candidates. Never a
              claim of confirmed search-result cannibalization (see backend
              `reports/content_intelligence.py`). Free/paid is server-gated
              (report.content_intelligence). */}
          <ContentIntelligence data={report.content_intelligence} unlocked={unlocked}
                               onUnlock={() => openPurchase({ message: "Unlock the complete content intelligence report — every cluster, duplicate element, and thin page." })} />

          {/* Question Bank — consolidates the scan questions above WITH Answer Tracking's
              actually-tracked prompts + related opportunities into one grounded view.
              Authenticated report only (public shares have no monitor/AT context). */}
          {!readOnly && scanId && <ReportQuestionBank scanId={scanId} />}

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

          {/* Score impact simulator (Phase 2) */}
          {hasSimulator && (
            <ScoreSimulator data={ins.score_impact} unlocked={unlocked}
                            onUnlock={() => openPurchase({ message: "Unlock the full score-impact simulator, complete diagnosis and implementation steps." })} />
          )}

          {/* 30-day AEO action plan (Phase 2) */}
          {hasPlan && (
            <ActionPlan data={ap} unlocked={unlocked}
                        onUnlock={(locked) => openPurchase({ message: `Your audit found ${locked} more action${locked === 1 ? "" : "s"} — unlock your complete 30-day AEO plan with implementation steps, priority and estimated score impact.` })} />
          )}

          {/* recommendations */}
          <div id="rep-recommendations" className="au-panel-h" style={{ margin: "8px 0 0", scrollMarginTop: 120 }}>
            Recommendations <span className="au-sub">what's wrong, why it matters, and how to fix it</span>
          </div>
          {recs.length === 0 && lockedCount === 0 ? (
            <Cell solid><div className="au-dim" style={{ textAlign: "center", padding: 20 }}>No problems found — this site is in excellent shape for AI visibility. 🎉</div></Cell>
          ) : (
            <>
              {/* Paid: estimated score recovery from fixing the issues found. Free: the
                  server has already trimmed `recs` to the free preview (see backend
                  `gate_recommendations`) — every card shown is real, full content; the
                  locked remainder is represented ONLY by a count, never rendered. */}
              {ins?.max_recovery?.recoverable_points > 0 && <ScoreImpact proj={ins.max_recovery} />}
              <div className="au-rep-list">{recs.map((r, i) => (
                <RecCard key={r.id + i} r={r} idx={i + 1} ins={aiById[r.id]}
                         verification={verificationBySignal[r.id]}
                         verifyHref={!readOnly && scanId ? `/app/scans/${encodeURIComponent(scanId)}` : null} />
              ))}</div>
              {!unlocked && lockedCount > 0 && (
                <div className="au-rep-lock-overlay" style={{ marginTop: 12 }}>
                  <div className="au-rep-lock-t">{lockedCount} more fix{lockedCount === 1 ? "" : "es"} found</div>
                  <div className="au-rep-lock-s">Unlock the complete diagnosis, implementation steps and affected URLs.</div>
                  <ul className="au-rep-lock-feats">
                    <li><Check size={12} /> All affected URLs</li>
                    <li><Check size={12} /> Complete implementation steps</li>
                    <li><Check size={12} /> Schema &amp; content fixes</li>
                    <li><Check size={12} /> Detailed evidence</li>
                    <li><Check size={12} /> Projected score impact</li>
                  </ul>
                  <Button variant="accent" onClick={() => openPurchase({ message: `${lockedCount} more fix${lockedCount === 1 ? "" : "es"} found — unlock the complete diagnosis, implementation steps and affected URLs.` })}>
                    <Lock size={14} /> Unlock complete diagnosis
                  </Button>
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

/* ============================== Phase 4: deep AEO intelligence ==============================
   All four sections render exactly what the server sent (see backend `gate_phase4`) — a
   locked caller's data already IS the free preview (a couple of real items + a locked
   count); there is nothing further to hide or blur here. */
function LockNote({ count, noun, onUnlock }) {
  if (!count) return null;
  return (
    <div className="au-sim-lock">
      <Lock size={13} />
      <span>{count} more {noun}{count === 1 ? "" : "s"}</span>
      <Button variant="accent" onClick={onUnlock}>Unlock</Button>
    </div>
  );
}

/* Phase C: when the same root cause is ALREADY represented by a full recommendation
   card (report.recommendations[] has an entry whose id equals this section's own real
   `related_recommendation_id` — never fuzzy-matched), missing schema types render as
   supporting evidence for that recommendation (with a jump-link to it, reusing
   `jumpToRecommendation`) instead of a second, duplicate diagnosis card for the same
   underlying "schema" signal. Otherwise each real gap gets its own DiagnosisCard:
   title is the short "what's wrong" headline; Evidence is the real affected URL(s);
   Why it matters / How to fix are the section's own real `why_it_matters`/
   `recommended_action` text — never invented. No `problem` paragraph is passed (it
   would just restate the title) and no Verify link (Verify lives on the covering
   recommendation's own card when one exists — see the supporting-evidence branch). */
function SchemaIntelligence({ data, unlocked, onUnlock, recommendations }) {
  if (!data) return null;
  const missing = data.missing_types || [];
  const present = data.present_types || [];
  if (missing.length === 0 && present.length === 0 && data.state === "absent" && !data.bulk) {
    return null;
  }
  const coveringRec = (recommendations || []).find((r) => r.id === data.related_recommendation_id);
  return (
    <Cell solid id="rep-schema" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">Schema Intelligence <span className="au-sub">what structured data exists, and what's missing</span></div>
      {present.length > 0 && (
        <div className="au-dim" style={{ fontSize: 12, marginBottom: 8 }}>
          Detected: {present.join(", ")}
        </div>
      )}
      {data.state === "malformed" && (
        <div className="au-at-err" style={{ marginBottom: 8 }}>
          {data.malformed_blocks} JSON-LD block(s) present but malformed.
        </div>
      )}
      {missing.length === 0 ? (
        <div className="au-dim" style={{ fontSize: 13 }}>No missing schema types found for this page. 🎉</div>
      ) : coveringRec ? (
        <SupportingEvidenceNote
          recommendationTitle={coveringRec.issue_title}
          items={missing.map((m) => `Missing ${m.type} schema` + ((m.affected_urls || []).length ? ` — ${m.affected_urls.join(", ")}` : ""))}
          onJump={() => jumpToRecommendation(coveringRec.id)}
        />
      ) : (
        <div className="au-rep-list">
          {missing.map((m) => (
            <DiagnosisCard key={m.type}
              title={`Missing ${m.type} schema`}
              evidence={(m.affected_urls || []).length > 0 ? m.affected_urls : undefined}
              whyItMatters={m.why_it_matters}
              fix={m.recommended_action}
            />
          ))}
        </div>
      )}
      {!unlocked && <LockNote count={data.locked_missing_count} noun="missing schema type" onUnlock={onUnlock} />}
      {data.bulk && (
        <div className="au-dim" style={{ fontSize: 12, marginTop: 10 }}>
          {data.bulk.weak_page_count} of {data.bulk.pages_checked} scanned page(s) have weak/missing structured data.
          {!unlocked && data.locked_weak_page_count > 0 && ` ${data.locked_weak_page_count} more page(s) — Pro.`}
        </div>
      )}
    </Cell>
  );
}

function LinkIntelligence({ data, unlocked, onUnlock }) {
  if (!data) return null;
  const issues = data.issues || [];
  return (
    <Cell solid id="rep-links-intel" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">Internal Link Intelligence <span className="au-sub">outgoing link coverage from this page</span></div>
      {issues.length === 0 ? (
        <div className="au-dim" style={{ fontSize: 13 }}>No internal-linking issues found for this page. 🎉</div>
      ) : (
        <div className="au-rep-list">
          {issues.map((iss) => (
            <div key={iss.type} className="au-rep-card">
              <div className="au-rep-card-b" style={{ padding: "12px 14px" }}>
                <div className="au-rep-card-t" style={{ marginBottom: 4 }}>{iss.label}</div>
                <p className="au-rep-desc">{iss.detail}</p>
                <div className="au-dim" style={{ fontSize: 12, marginBottom: 6 }}>
                  Affected URL{(iss.affected_urls || []).length === 1 ? "" : "s"}: {(iss.affected_urls || []).join(", ") || "—"}
                </div>
                <div className="au-rep-ai-why"><b>Recommendation:</b> {iss.recommended_action}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      {!unlocked && <LockNote count={data.locked_issue_count} noun="link issue" onUnlock={onUnlock} />}
      <div className="au-dim" style={{ fontSize: 11, marginTop: 10 }}>{data.inbound_link_graph_note}</div>
      {data.bulk && (
        <div className="au-dim" style={{ fontSize: 12, marginTop: 4 }}>
          {data.bulk.weak_page_count} of {data.bulk.pages_checked} scanned page(s) have weak internal linking.
          {!unlocked && data.locked_weak_page_count > 0 && ` ${data.locked_weak_page_count} more page(s) — Pro.`}
        </div>
      )}
    </Cell>
  );
}

/* Phase C: the overview (primary entity, completeness, types, sameAs) stays exactly
   as before — real, useful context that is NOT a "problem" even when e.g. sameAs is
   empty (that's shown via `same_as_note`, never framed as an issue). A diagnosis only
   renders when `missing_signals` is genuinely non-empty; the same
   related_recommendation_id dedup as Schema applies (entity and schema share the same
   underlying "schema" signal on the backend), so a missing-signal finding either
   becomes supporting evidence for an existing "schema" recommendation, or — when no
   such recommendation exists — one aggregate DiagnosisCard (missing_signals is an
   array of check keys, not per-item objects, so it's one card, not N). */
function EntityIntelligence({ data, unlocked, onUnlock, recommendations }) {
  if (!data) return null;
  const missing = data.missing_signals || [];
  const sameAs = data.same_as || [];
  const coveringRec = (recommendations || []).find((r) => r.id === data.related_recommendation_id);
  const missingLabels = missing.map((k) => ENTITY_SIGNAL_LABEL[k] || k);
  return (
    <Cell solid id="rep-entity" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">Entity Intelligence <span className="au-sub">what entity your site's own structured data represents</span></div>
      <div className="au-sim-scores" style={{ marginBottom: 10 }}>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Primary entity</div>
          <div className="au-sim-sc-v" style={{ fontSize: 15 }}>{data.primary_entity_name || "Not detected"}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Signal completeness</div>
          <div className="au-sim-sc-v">{data.completeness_pct}%</div></div>
      </div>
      {(data.primary_entity_types || []).length > 0 && (
        <div className="au-dim" style={{ fontSize: 12, marginBottom: 6 }}>Entity types: {data.primary_entity_types.join(", ")}</div>
      )}
      <div className="au-dim" style={{ fontSize: 12, marginBottom: 6 }}>
        sameAs: {sameAs.length > 0 ? sameAs.join(", ") : (data.same_as_note || "No sameAs relationship detected.")}
      </div>
      {missing.length === 0 ? (
        <div className="au-dim" style={{ fontSize: 13 }}>No missing entity signals detected. 🎉</div>
      ) : coveringRec ? (
        <SupportingEvidenceNote
          recommendationTitle={coveringRec.issue_title}
          items={missingLabels}
          onJump={() => jumpToRecommendation(coveringRec.id)}
        />
      ) : (
        <DiagnosisCard
          title={`${missing.length} entity signal${missing.length === 1 ? "" : "s"} missing`}
          evidence={missingLabels}
          fix="Add the missing entity fields to your Organization/WebSite JSON-LD."
        />
      )}
      {!unlocked && <LockNote count={data.locked_missing_signal_count} noun="missing entity signal" onUnlock={onUnlock} />}
      {!unlocked && data.locked_same_as_count > 0 && (
        <div className="au-dim" style={{ fontSize: 11, marginTop: 4 }}>{data.locked_same_as_count} more sameAs link(s) — Pro.</div>
      )}
      <div className="au-dim" style={{ fontSize: 11, marginTop: 10 }}>{data.knowledge_graph_note}</div>
    </Cell>
  );
}

const _CATEGORY_LABEL = {
  pricing: "Pricing", comparison: "Comparison", local: "Local", how_to: "How-to",
  problem_solution: "Problem/solution", service: "Service", informational: "Informational",
};

function QuestionMining({ data, unlocked, onUnlock }) {
  if (!data) return null;
  const questions = data.questions || [];
  if (questions.length === 0) return null;
  return (
    <Cell solid id="rep-questions" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">Question Opportunities <span className="au-sub">real questions already in your content and schema</span></div>
      <div className="au-rep-list">
        {questions.map((q, i) => (
          <div key={i} className="au-rep-card">
            <div className="au-rep-card-b" style={{ padding: "12px 14px" }}>
              <div className="au-rep-card-t" style={{ marginBottom: 4 }}>"{q.text}"</div>
              <div className="au-dim" style={{ fontSize: 12 }}>
                {_CATEGORY_LABEL[q.category] || q.category} · source: {q.source === "schema_faq" ? "FAQ schema" : "page heading"}
                {q.affected_url ? ` · ${q.affected_url}` : ""}
              </div>
              {q.answer && <p className="au-rep-desc" style={{ marginTop: 6 }}>{q.answer}</p>}
            </div>
          </div>
        ))}
      </div>
      {!unlocked && <LockNote count={data.locked_question_count} noun="question" onUnlock={onUnlock} />}
    </Cell>
  );
}

/* ============================== Technical SEO & Indexability ==============================
   Renders exactly what the server sent (see backend `gate_technical_seo`) — a locked
   caller's data already IS the free preview. Terminology stays strictly technical
   ("indexable" / "crawlable" / "blocked") — this never claims Google has actually
   indexed a URL (no Search Console integration exists). */
const _TSEO_SUMMARY_LABEL = {
  indexable: "Indexable", not_indexable: "Noindex", blocked: "Blocked",
  redirected: "Redirected", errors: "Errors", unknown: "Unknown",
};
const _TSEO_STATUS_COLOR = {
  indexable: "var(--au-mint-d)", not_indexable: "var(--au-peach-d)",
  blocked: "var(--au-peach-d)", redirected: "var(--au-lemon-d)",
  error: "var(--au-peach-d)", unknown: "var(--au-muted)",
};
const _TSEO_STATUS_LABEL = {
  indexable: "Indexable", not_indexable: "Not indexable", blocked: "Blocked",
  redirected: "Redirected", error: "Error", unknown: "Unknown",
};
// Reverse of actionSources.js's TECH_SEO_COVERED_BY_RECOMMENDATION (signal_id -> issue
// codes) into (issue code -> signal_id), computed once at module load — the SAME
// editorial mapping Action Center already uses, never a second copy or fuzzy match.
const _TSEO_CODE_TO_SIGNAL = Object.fromEntries(
  Object.entries(TECH_SEO_COVERED_BY_RECOMMENDATION)
    .flatMap(([signalId, codes]) => codes.map((c) => [c, signalId])),
);

function TechnicalSeoIntelligence({ data, unlocked, onUnlock, recommendations }) {
  if (!data) return null;
  if (data.available === false) {
    return (
      <Cell solid id="rep-technical-seo" style={{ scrollMarginTop: 120 }}>
        <div className="au-panel-h">Technical SEO &amp; Indexability</div>
        <div className="au-dim" style={{ fontSize: 13 }}>
          Scan still in progress — insufficient evidence to generate indexability
          intelligence until the scan completes.
        </div>
      </Cell>
    );
  }
  const summary = data.summary || {};
  const issues = data.issues || [];
  const urls = data.urls || [];
  const sitemapGaps = data.sitemap_urls_not_crawled || [];
  if ((summary.total_urls || 0) === 0) return null;
  // Real per-URL detail (HTTP status, canonical) already sits on `data.urls`, keyed by
  // URL — cross-referenced (never fabricated) so an issue's Evidence step can show
  // "url — HTTP 404" instead of a bare URL that would otherwise duplicate the exact
  // text of the URL-by-URL table below.
  const urlByUrl = Object.fromEntries(urls.map((u) => [u.url, u]));
  const issueEvidenceLine = (url) => {
    const u = urlByUrl[url];
    if (!u) return url;
    let line = `${url} — HTTP ${u.status_code ?? "unknown"}`;
    if (u.canonical_type === "other") line += ` · canonical → ${u.canonical}`;
    return line;
  };

  return (
    <Cell solid id="rep-technical-seo" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">
        Technical SEO &amp; Indexability
        <span className="au-sub">
          for every crawled URL: can search engines discover, crawl, and index it?
        </span>
      </div>
      <div className="au-sim-scores" style={{ marginBottom: 12 }}>
        <div className="au-sim-sc">
          <div className="au-sim-sc-l">URLs analyzed</div>
          <div className="au-sim-sc-v">{summary.total_urls}</div>
        </div>
        {["indexable", "not_indexable", "blocked", "redirected", "errors", "unknown"]
          .filter((k) => summary[k] > 0)
          .map((k) => (
            <div key={k} className="au-sim-sc">
              <div className="au-sim-sc-l">{_TSEO_SUMMARY_LABEL[k]}</div>
              <div className="au-sim-sc-v">{summary[k]}</div>
            </div>
          ))}
      </div>

      {/* Phase C: an actual indexability problem (real severity, real affected URLs)
          is diagnosed via DiagnosisCard — Problem/Evidence/Fix, sourced only from the
          existing TECH_ISSUE_PROBLEM/FIX copy Action Center already uses (never a
          second copy). When the same root cause is already a full recommendation card
          (the same TECH_SEO_COVERED_BY_RECOMMENDATION map Action Center's dedup uses),
          it renders as supporting evidence for that recommendation instead of a
          duplicate action, with Verify offered only there — never invented here. */}
      {issues.length > 0 && (
        <div className="au-rep-list" style={{ marginBottom: 12 }}>
          {issues.map((iss) => {
            const coveringRec = (recommendations || []).find((r) => r.id === _TSEO_CODE_TO_SIGNAL[iss.code]);
            if (coveringRec) {
              return (
                <SupportingEvidenceNote key={iss.code}
                  recommendationTitle={coveringRec.issue_title}
                  items={[iss.label, ...(iss.affected_urls || []).map(issueEvidenceLine)]}
                  onJump={() => jumpToRecommendation(coveringRec.id)}
                />
              );
            }
            return (
              <DiagnosisCard key={iss.code}
                title={iss.label} severity={iss.severity}
                problem={TECH_ISSUE_PROBLEM[iss.code]}
                evidence={(iss.affected_urls || []).length > 0 ? iss.affected_urls.map(issueEvidenceLine) : undefined}
                fix={TECH_ISSUE_FIX[iss.code]}
              />
            );
          })}
        </div>
      )}
      {!unlocked && <LockNote count={data.locked_issue_count} noun="technical issue" onUnlock={onUnlock} />}

      {/* Scan coverage (never an indexability error): sitemap URLs the crawl itself
          didn't reach this run. This is information about what this SCAN covered, not
          a claim those URLs are broken/unindexable — kept visually and textually
          separate from the issues list above so it can never read as urgency. */}
      {sitemapGaps.length > 0 && (
        <div className="au-tseo-coverage" style={{ marginBottom: 12 }}>
          <div className="au-tseo-coverage-h">Scan coverage — not an indexability error</div>
          <p className="au-dim" style={{ fontSize: 12, margin: "4px 0 6px" }}>
            {sitemapGaps.length} URL{sitemapGaps.length === 1 ? "" : "s"} listed in your
            sitemap {sitemapGaps.length === 1 ? "was" : "were"} not reached during this
            scan's crawl. This reflects this scan's own coverage, not a claim that these
            URLs are unindexable.
          </p>
          <div className="au-dim" style={{ fontSize: 11.5 }}>{sitemapGaps.slice(0, 5).join(", ")}</div>
          {!unlocked && data.locked_sitemap_gap_count > 0 && (
            <div className="au-dim" style={{ fontSize: 11, marginTop: 4 }}>{data.locked_sitemap_gap_count} more — Pro.</div>
          )}
        </div>
      )}

      {urls.length > 0 && (
        <div className="au-rep-list" style={{ marginTop: issues.length > 0 ? 14 : 0 }}>
          {urls.map((u) => (
            <div key={u.url} className="au-rep-card">
              <div className="au-rep-card-b" style={{ padding: "10px 14px" }}>
                <div className="au-tseo-row">
                  <span className="au-tseo-badge" style={{ background: _TSEO_STATUS_COLOR[u.indexability_status] || "var(--au-muted)" }}>
                    {_TSEO_STATUS_LABEL[u.indexability_status] || u.indexability_status}
                  </span>
                  <span className="au-tseo-url">{u.url}</span>
                  <span className="au-dim" style={{ fontSize: 11.5 }}>HTTP {u.status_code ?? "unknown"}</span>
                </div>
                {(u.issues || []).length > 0 && (
                  <div className="au-dim" style={{ fontSize: 11.5, marginTop: 4 }}>{u.issues.join(", ")}</div>
                )}
                {u.canonical_type === "other" && (
                  <div className="au-dim" style={{ fontSize: 11.5, marginTop: 2 }}>Canonical → {u.canonical}</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {!unlocked && <LockNote count={data.locked_url_count} noun="crawled URL" onUnlock={onUnlock} />}
    </Cell>
  );
}

/* ============================== Real Crawl Graph & Internal Linking ==============================
   Renders exactly what the server sent (see backend `gate_crawl_graph`) — a locked
   caller's data already IS the free preview. No "Google crawled/indexed" wording — this
   is purely the site's OWN internal-link structure, grounded in the actual crawled
   HTML. Diagnostic tables first; no graph-visualization dependency required to
   understand the data (per the task's explicit "UI must remain usable without a visual
   graph" requirement). */
function CrawlGraphIntelligence({ data, unlocked, onUnlock }) {
  if (!data) return null;
  if (data.available === false) {
    if (data.reason === "multi_page_crawl_required") return null;   // single-page scan: nothing to show
    return (
      <Cell solid id="rep-crawl-graph" style={{ scrollMarginTop: 120 }}>
        <div className="au-panel-h">Crawl Graph &amp; Internal Linking</div>
        <div className="au-dim" style={{ fontSize: 13 }}>
          Scan still in progress — insufficient evidence to build the internal-link
          graph until the scan completes.
        </div>
      </Cell>
    );
  }
  const summary = data.summary || {};
  const issues = data.issues || [];
  const orphans = data.orphans || [];
  const pages = data.pages || [];
  const depthHistogram = data.depth_histogram || [];
  const topReferenced = data.top_referenced || [];
  if ((summary.pages || 0) === 0) return null;
  // Real per-page detail (inbound/outbound link counts, depth) already sits on
  // `data.pages` (and, on a gated/free payload where `pages` is itself trimmed,
  // `data.orphans` — the same per-page shape) keyed by URL — cross-referenced (never
  // fabricated) so an issue's Evidence step is specific rather than a bare URL that
  // would otherwise duplicate the exact text of the orphan-pages table below.
  const pageByUrl = Object.fromEntries([...orphans, ...pages].map((p) => [p.url, p]));
  const issueEvidenceLine = (url) => {
    const p = pageByUrl[url];
    if (!p) return url;
    let line = `${url} — ${p.inbound_pages ?? 0} inbound, ${p.outbound_pages ?? 0} outbound`;
    if (p.depth != null) line += `, depth ${p.depth}`;
    return line;
  };

  return (
    <Cell solid id="rep-crawl-graph" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">
        Crawl Graph &amp; Internal Linking
        <span className="au-sub">how your crawled pages actually connect to each other</span>
      </div>
      <div className="au-sim-scores" style={{ marginBottom: 12 }}>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Pages crawled</div><div className="au-sim-sc-v">{summary.pages}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Internal links</div><div className="au-sim-sc-v">{summary.internal_edges}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Reachable</div><div className="au-sim-sc-v">{summary.reachable_pages}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Disconnected</div><div className="au-sim-sc-v">{summary.disconnected_pages}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Orphan pages</div><div className="au-sim-sc-v">{summary.orphan_pages}</div></div>
        {summary.max_depth != null && (
          <div className="au-sim-sc"><div className="au-sim-sc-l">Max depth</div><div className="au-sim-sc-v">{summary.max_depth}</div></div>
        )}
      </div>

      {/* Phase C: each real graph issue via DiagnosisCard, using the SAME
          CRAWL_ISSUE_WHY/FIX copy Action Center's normalizer uses (never a second
          copy). The Why-it-matters text is what makes the ORPHAN_PAGE vs
          DISCONNECTED_PAGE distinction explicit and specific per finding — an orphan
          has zero inbound internal links at all; a disconnected page may still have
          inbound links but isn't reachable by following links from the crawl seed
          (see backend `reports/crawl_graph.py`) — rather than a single generic note
          that would blur the two together. No Verify link: crawl_graph issues carry
          no `related_recommendation_id` on the backend, so one is never invented. */}
      {issues.length > 0 && (
        <div className="au-rep-list" style={{ marginBottom: 12 }}>
          {issues.map((iss) => (
            <DiagnosisCard key={iss.code}
              title={iss.label} severity={iss.severity}
              evidence={(iss.affected_urls || []).length > 0 ? iss.affected_urls.map(issueEvidenceLine) : undefined}
              whyItMatters={CRAWL_ISSUE_WHY[iss.code]}
              fix={CRAWL_ISSUE_FIX[iss.code]}
            />
          ))}
        </div>
      )}
      {!unlocked && <LockNote count={data.locked_issue_count} noun="graph issue" onUnlock={onUnlock} />}

      {orphans.length > 0 && (
        <>
          <div className="au-panel-h" style={{ fontSize: 13, marginTop: 14 }}>Orphan pages <span className="au-sub">zero inbound internal links</span></div>
          <div className="au-rep-list" style={{ marginBottom: 12 }}>
            {orphans.map((o) => (
              <div key={o.url} className="au-rep-card">
                <div className="au-rep-card-b" style={{ padding: "10px 14px" }}>
                  <span className="au-tseo-url">{o.url}</span>
                  <div className="au-dim" style={{ fontSize: 11.5, marginTop: 2 }}>
                    0 referring pages · {o.outbound_pages} outbound link{o.outbound_pages === 1 ? "" : "s"}
                    {o.depth != null ? ` · depth ${o.depth}` : o.reachable === false ? " · not reachable from seed" : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {!unlocked && <LockNote count={data.locked_orphan_count} noun="orphan page" onUnlock={onUnlock} />}
        </>
      )}

      {depthHistogram.length > 0 && (
        <>
          <div className="au-panel-h" style={{ fontSize: 13, marginTop: 14 }}>Site structure <span className="au-sub">depth from the crawl seed</span></div>
          <div className="au-rep-bd" style={{ marginBottom: 12 }}>
            {depthHistogram.map((d) => (
              <div key={d.label} className="au-rep-bd-row">
                <div className="au-rep-bd-name">Depth {d.label}</div>
                <div className="au-rep-bd-track">
                  <div className="au-rep-bd-fill" style={{ width: `${Math.min(100, (d.count / summary.pages) * 100)}%`, background: "var(--au-primary)" }} />
                </div>
                <div className="au-rep-bd-score">{d.count} page{d.count === 1 ? "" : "s"}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {topReferenced.length > 0 && (
        <>
          <div className="au-panel-h" style={{ fontSize: 13, marginTop: 14 }}>Top referenced pages</div>
          <div className="au-rep-list">
            {topReferenced.map((t) => (
              <div key={t.url} className="au-rep-card">
                <div className="au-rep-card-b" style={{ padding: "8px 14px", flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                  <span className="au-tseo-url">{t.url}</span>
                  <span className="au-dim" style={{ fontSize: 11.5, whiteSpace: "nowrap" }}>{t.inbound_pages} referring page{t.inbound_pages === 1 ? "" : "s"}</span>
                </div>
              </div>
            ))}
          </div>
          {!unlocked && data.locked_top_referenced_count > 0 && (
            <div className="au-dim" style={{ fontSize: 11, marginTop: 6 }}>{data.locked_top_referenced_count} more — Pro.</div>
          )}
        </>
      )}
    </Cell>
  );
}

/* ============================== Content Cannibalization & Duplicate Content Intelligence ==============================
   Renders exactly what the server sent (see backend `gate_content_intelligence`) — a
   locked caller's data already IS the free preview. Terminology stays strictly
   "potential" — never a claim of confirmed search-result cannibalization.

   Phase C: a cluster's `evidence[]` (the real overlap/similarity measurements) and
   real page URLs become the DiagnosisCard's Evidence step; `recommendation` (already a
   full human sentence, richer than the generic CONSOLIDATE/DIFFERENTIATE/
   REVIEW_CANONICAL action-code copy Action Center's compact preview uses) is the Fix
   step — no separate "why" step, since the evidence array already IS the explanation
   for why these pages were grouped, and repeating it under a second heading would
   just be the same text twice. A KEEP_SEPARATE cluster (the scanner reviewed it and
   found no actionable overlap) is filtered out entirely in `ContentIntelligence`
   below — never rendered as if it were a problem. */
function ContentCluster({ cluster }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <DiagnosisCard
      title={cluster.label} severity={cluster.severity}
      evidence={[`${cluster.confidence} confidence`, ...(cluster.evidence || [])]}
      fix={cluster.recommendation}
      affectedPages={
        <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 2 }}>
          {cluster.pages.map((p) => <span key={p.url} className="au-tseo-url">{p.url}</span>)}
        </div>
      }
    >
      {cluster.pages.length > 1 && (
        <button type="button" className="au-ci-toggle" onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Hide" : "Show"} page details
        </button>
      )}
      {expanded && (
        <ul className="au-ci-evidence">
          {cluster.pages.map((p) => (
            <li key={p.url}>
              {p.url} — {p.title || "no title"} · {p.word_count} words
              {p.canonical ? ` · canonical: ${p.canonical}` : ""}
            </li>
          ))}
        </ul>
      )}
    </DiagnosisCard>
  );
}

function ContentIntelligence({ data, unlocked, onUnlock }) {
  if (!data) return null;
  if (data.available === false) {
    if (data.reason === "multi_page_crawl_required") return null;   // single-page scan: nothing to show
    return (
      <Cell solid id="rep-content-intelligence" style={{ scrollMarginTop: 120 }}>
        <div className="au-panel-h">Content Intelligence</div>
        <div className="au-dim" style={{ fontSize: 13 }}>
          Scan still in progress — insufficient evidence to analyze content overlap
          until the scan completes.
        </div>
      </Cell>
    );
  }
  const summary = data.summary || {};
  const clusters = data.clusters || [];
  const elements = data.duplicate_elements || [];
  const thin = data.thin_pages || [];
  if ((summary.pages_analyzed || 0) === 0) return null;

  return (
    <Cell solid id="rep-content-intelligence" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">
        Content Intelligence
        <span className="au-sub">potential cannibalization, near-duplicate and overlapping content</span>
      </div>
      <div className="au-sim-scores" style={{ marginBottom: 12 }}>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Pages analyzed</div><div className="au-sim-sc-v">{summary.pages_analyzed}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Potential cannibalization</div><div className="au-sim-sc-v">{summary.potential_cannibalization_clusters}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Near-duplicate</div><div className="au-sim-sc-v">{summary.duplicate_clusters}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Content overlap</div><div className="au-sim-sc-v">{summary.overlap_clusters}</div></div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Duplicate elements</div><div className="au-sim-sc-v">{summary.duplicate_elements}</div></div>
        {summary.thin_pages > 0 && (
          <div className="au-sim-sc"><div className="au-sim-sc-l">Thin-content</div><div className="au-sim-sc-v">{summary.thin_pages}</div></div>
        )}
      </div>

      {/* KEEP_SEPARATE means the scanner reviewed this cluster and found no
          actionable overlap — never rendered as if it were a problem (same rule
          Action Center's own normalizer already applies to this exact field). */}
      {clusters.filter((c) => c.recommended_action !== "KEEP_SEPARATE").length > 0 && (
        <div className="au-rep-list" style={{ marginBottom: 12 }}>
          {clusters.filter((c) => c.recommended_action !== "KEEP_SEPARATE")
            .map((c) => <ContentCluster key={c.id} cluster={c} />)}
        </div>
      )}
      {!unlocked && <LockNote count={data.locked_cluster_count} noun="content cluster" onUnlock={onUnlock} />}

      {elements.length > 0 && (
        <>
          <div className="au-panel-h" style={{ fontSize: 13, marginTop: 14 }}>Duplicate elements</div>
          {/* No `fix` step here — the backend provides no per-element recommendation
              field (unlike clusters' own `recommendation`), so none is invented. */}
          <div className="au-rep-list" style={{ marginBottom: 12 }}>
            {elements.map((e, i) => (
              <DiagnosisCard key={i}
                title={
                  (e.type === "duplicate_title" ? "Duplicate title" : e.type === "duplicate_h1" ? "Duplicate H1" : "Duplicate meta description")
                  + ` duplicated across ${e.urls.length} pages`
                }
                evidence={[`"${e.value}"`, ...e.urls]}
              />
            ))}
          </div>
          {!unlocked && data.locked_duplicate_element_count > 0 && (
            <div className="au-dim" style={{ fontSize: 11, marginTop: 4, marginBottom: 12 }}>
              {data.locked_duplicate_element_count} more duplicate-element group(s) — Pro.
            </div>
          )}
        </>
      )}

      {thin.length > 0 && (
        <>
          <div className="au-panel-h" style={{ fontSize: 13, marginTop: 14 }}>Thin-content candidates <span className="au-sub">relative to this site's own median</span></div>
          <div className="au-rep-list">
            {thin.map((p) => (
              <div key={p.url} className="au-rep-card">
                <div className="au-rep-card-b" style={{ padding: "8px 14px", flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                  <span className="au-tseo-url">{p.url}</span>
                  <span className="au-dim" style={{ fontSize: 11.5, whiteSpace: "nowrap" }}>{p.word_count} words (site median {p.site_median_word_count})</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {!unlocked && data.locked_thin_page_count > 0 && (
        <div className="au-dim" style={{ fontSize: 11, marginTop: 6 }}>{data.locked_thin_page_count} more thin page(s) — Pro.</div>
      )}
    </Cell>
  );
}

/* Deterministic, estimate-only score-recovery preview. Reads insights.projected_recovery
   / max_recovery (computed server-side from weight × gap-to-100). Clearly labelled a
   projection — it never reflects a re-score. */
function ScoreImpact({ proj }) {
  if (!proj) return null;
  return (
    <div className="au-rep-ip">
      <div className="au-rep-ip-row">
        <div className="au-rep-ip-col"><div className="au-rep-ip-l">Current</div><div className="au-rep-ip-v">{proj.current_score}</div></div>
        <TrendingUp size={18} className="au-rep-ip-arrow" aria-hidden="true" />
        <div className="au-rep-ip-col"><div className="au-rep-ip-l">Potential recovery</div><div className="au-rep-ip-v pos">+{proj.recoverable_points}</div></div>
        <div className="au-rep-ip-col"><div className="au-rep-ip-l">Estimated after fixes</div><div className="au-rep-ip-v goal">{proj.projected_score}</div></div>
      </div>
      <div className="au-rep-ip-note">{proj.label}</div>
    </div>
  );
}

const round1 = (n) => Math.round(n * 10) / 10;

/* Score Impact Simulator (Phase 2). Select fixes → estimated recovery, computed live
   from each opportunity's recoverable points (weight × gap-to-100, deduped per signal).
   Deterministic arithmetic only — no scan, no re-score, clamped to 100. Free users see a
   1–2 item preview; the rest unlock via the existing $9/Pro modal. */
function ScoreSimulator({ data, unlocked, onUnlock }) {
  // Locked callers already receive only the free preview items from the server
  // (`gate_insights`) plus `locked_item_count` — there is nothing further to slice or
  // hide client-side; the count comes from the server, not from `items.length`.
  const shown = data.items || [];
  const lockedItems = unlocked ? 0 : (data.locked_item_count ?? 0);
  const [sel, setSel] = useState(() => new Set());
  const toggle = (id) => setSel((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  // Selection is keyed by signal_id, so choosing several items from one signal can't
  // recover its points twice (the item list is already one-per-signal).
  const recovery = round1(shown.filter((i) => sel.has(i.signal_id))
    .reduce((a, i) => a + i.recoverable_points, 0));
  const projected = round1(Math.min(100, data.current_score + recovery));

  return (
    <Cell solid id="rep-simulator" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">
        <TrendingUp size={14} style={{ color: "var(--au-primary)" }} /> Score impact simulator
        <span className="au-sub">select the fixes you'll make to see the estimated recovery</span>
      </div>
      <div className="au-sim-scores">
        <div className="au-sim-sc"><div className="au-sim-sc-l">Current score</div><div className="au-sim-sc-v">{round1(data.current_score)}</div></div>
        <div className="au-sim-plus" aria-hidden="true">+</div>
        <div className="au-sim-sc"><div className="au-sim-sc-l">Selected fixes</div><div className="au-sim-sc-v pos">+{recovery}</div></div>
        <TrendingUp size={16} className="au-sim-arrow" aria-hidden="true" />
        <div className="au-sim-sc"><div className="au-sim-sc-l">Estimated score</div><div className="au-sim-sc-v goal">{projected}</div></div>
      </div>
      <div className="au-sim-note">{data.label}</div>
      <div className="au-sim-items">
        {shown.map((it) => {
          const on = sel.has(it.signal_id);
          return (
            <button key={it.signal_id} type="button" className={`au-sim-item${on ? " on" : ""}`}
                    onClick={() => toggle(it.signal_id)} aria-pressed={on}>
              <span className="au-sim-check">{on ? <CheckCircle2 size={17} /> : <Circle size={17} />}</span>
              <span className="au-sim-item-main">
                <span className="au-sim-item-t">{it.title}</span>
                <span className="au-sim-item-meta">
                  {it.signal} · score {it.current_score}{it.priority ? ` · ${it.priority}` : ""}{it.difficulty ? ` · ${it.difficulty}` : ""}{it.estimated_fix_time ? ` · ${it.estimated_fix_time}` : ""}
                </span>
              </span>
              <span className="au-sim-item-rec">+{round1(it.recoverable_points)}</span>
            </button>
          );
        })}
      </div>
      {!unlocked && lockedItems > 0 && (
        <div className="au-sim-lock">
          <Lock size={13} />
          <span>{lockedItems} more opportunit{lockedItems === 1 ? "y" : "ies"} in the full simulator</span>
          <Button variant="accent" onClick={onUnlock}>Unlock full simulator</Button>
        </div>
      )}
    </Cell>
  );
}

const PLAN_WEEKS = [
  ["week_1", "Week 1 — Quick Wins"],
  ["week_2", "Week 2 — High Impact Fixes"],
  ["week_3", "Week 3 — Authority & Structure"],
  ["week_4", "Week 4 — AI Visibility & Monitoring"],
];

/* 30-Day AEO Action Plan (Phase 2). Rendered from the deterministic, server-built plan
   (no invented tasks). Free users see the first 3 actions + a locked teaser; paid users
   get the full week-by-week plan and backlog. */
function ActionPlan({ data, unlocked, onUnlock }) {
  // Locked callers receive ONLY `preview_tasks` + `locked_task_count` from the server
  // (`gate_insights`) — the real week_1..backlog task data for the locked remainder is
  // never sent over the wire, so there is nothing left to blur here beyond a plain CTA.
  if (!unlocked) {
    const preview = data.preview_tasks || [];
    const locked = data.locked_task_count ?? 0;
    return (
      <Cell solid id="rep-plan" style={{ scrollMarginTop: 120 }}>
        <div className="au-panel-h">
          <ListChecks size={14} style={{ color: "var(--au-primary)" }} /> Your 30-day AEO action plan
          <span className="au-sub">a prioritised, week-by-week plan built from your findings</span>
        </div>
        <div className="au-plan-tasks">{preview.map((t, i) => <PlanTask key={(t.id || "") + i} t={t} />)}</div>
        {locked > 0 && (
          <div className="au-rep-lock-overlay" style={{ marginTop: 12 }}>
            <div className="au-rep-lock-t">Your audit found {locked} more action{locked === 1 ? "" : "s"}</div>
            <div className="au-rep-lock-s">Unlock your complete 30-day AEO plan with implementation steps, priority, affected pages and estimated score impact.</div>
            <Button variant="accent" onClick={() => onUnlock(locked)}><Lock size={14} /> Unlock complete plan</Button>
          </div>
        )}
      </Cell>
    );
  }

  return (
    <Cell solid id="rep-plan" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">
        <ListChecks size={14} style={{ color: "var(--au-primary)" }} /> Your 30-day AEO action plan
        <span className="au-sub">prioritised week-by-week from your findings</span>
      </div>
      {PLAN_WEEKS.map(([k, label]) => (data[k] || []).length > 0 && (
        <div key={k} className="au-plan-week">
          <div className="au-plan-week-h">{label}</div>
          <div className="au-plan-tasks">{data[k].map((t, i) => <PlanTask key={(t.id || "") + i} t={t} />)}</div>
        </div>
      ))}
      {(data.backlog || []).length > 0 && (
        <div className="au-plan-week">
          <div className="au-plan-week-h au-plan-backlog">Later / Backlog</div>
          <div className="au-plan-tasks">{data.backlog.map((t, i) => <PlanTask key={"bk" + i} t={t} />)}</div>
        </div>
      )}
    </Cell>
  );
}

function PlanTask({ t }) {
  return (
    <div className="au-plan-task">
      {t.priority && <span className="au-rep-pri" style={{ background: AU_PRIORITY[t.priority] }}>{t.priority}</span>}
      <div className="au-plan-task-main">
        <div className="au-plan-task-t">{t.recommendation}</div>
        {t.why_it_matters && <div className="au-plan-task-why">{t.why_it_matters}</div>}
        <div className="au-plan-task-meta">
          {t.signal && <span>{t.signal}</span>}
          {t.difficulty && <span><Wrench size={11} /> {t.difficulty}</span>}
          {t.estimated_fix_time && <span><Clock size={11} /> {t.estimated_fix_time}</span>}
        </div>
      </div>
    </div>
  );
}

/* The recommendation BODY — problem/why-it-matters/fix/implementation/expected-outcome,
   all sourced from the ONE existing `build_recommendations()`/fix_template payload
   (never a second recommendation engine). Extracted so Scan Details can render the
   exact same diagnosis as the Report's Recommendations section, not a re-implementation
   of it — see ScanDetails.jsx. `showProblem` is false only in Scan Details, whose own
   "What's wrong" bullets already show this same text (fx.problem is literally the
   signal's first issue string) — skipping it there avoids showing the identical
   sentence twice in the same expanded panel, never a change to the underlying data. */
export function RecommendationCard({ r, ins, showProblem = true }) {
  const fx = r.fix_template || {};
  // `showProblem` also gates Evidence: Scan Details (showProblem=false there) already
  // renders this signal's evidence in its own dedicated block above this card — see
  // the module comment on ScanDetails.jsx's signal accordion — so this never repeats
  // it a second time in the same expanded panel. Real evidence only, never invented:
  // `r.evidence.issues` is the scanner's own issue strings, `r.evidence.findings` its
  // own raw evidence dict (same source ScanDetails' evidence block reads from).
  const evIssues = r.evidence?.issues || [];
  const evFindings = Object.entries(r.evidence?.findings || {}).filter(([k]) => k !== "detected_types");
  // Fix-first order: Problem -> Fix -> Implementation -> Outcome -> Why it matters/
  // Explanation -> Evidence -> Business/AI impact metadata (see PART 4/8 of the
  // Phase F ticket — same reordering already applied to DiagnosisCard). No field was
  // removed, renamed, or merged — only repositioned, so every existing consumer of
  // this data (Full Report, Action Center, Scan Details) keeps the exact same real
  // content, just fix-first.
  return (
    <div className="au-rep-card-b">
      {showProblem && <p className="au-rep-desc">{r.description}</p>}
      {showProblem && fx.problem && <><div className="au-rep-fx-h">Problem</div><p className="au-rep-desc">{fx.problem}</p></>}

      {(fx.recommended_fix || []).length > 0 && (
        <><div className="au-rep-fx-h au-rep-fx-h-prominent">Recommended fix</div>
          <ul className="au-rep-fx-ul au-rep-fix-prominent">{fx.recommended_fix.map((s, i) => <li key={i}><Wrench size={11} /> <span>{s}</span></li>)}</ul></>
      )}
      {fx.implementation_example && (
        <><div className="au-rep-fx-h">Implementation example</div>
          <pre className="au-code">{fx.implementation_example}</pre></>
      )}
      {fx.expected_outcome && <><div className="au-rep-fx-h">Expected outcome</div><p className="au-rep-outcome"><CheckCircle2 size={12} /> {fx.expected_outcome}</p></>}

      {ins?.why_it_matters && (
        <div className="au-rep-ai-why">
          <div className="au-rep-ai-why-h"><Sparkles size={12} /> Why it matters</div>
          <p>{ins.why_it_matters}</p>
          {ins.priority_rationale && <p className="au-rep-ai-why-r">{ins.priority_rationale}</p>}
        </div>
      )}
      {fx.explanation && <><div className="au-rep-fx-h">Explanation</div><p className="au-rep-desc">{fx.explanation}</p></>}

      {showProblem && (evIssues.length > 0 || evFindings.length > 0) && (
        <>
          <div className="au-rep-fx-h">Evidence</div>
          {evIssues.length > 0 && (
            <ul className="au-rep-fx-ul">
              {evIssues.map((s, i) => <li key={i}><AlertTriangle size={11} /> <span>{s}</span></li>)}
            </ul>
          )}
          {evFindings.length > 0 && (
            <ul className="au-rep-fx-ul">
              {evFindings.map(([k, v]) => <li key={k}><AlertTriangle size={11} /> <span>{k}: {fmtEv(v)}</span></li>)}
            </ul>
          )}
        </>
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
    </div>
  );
}

function RecCard({ r, idx, defaultOpen, ins, verifyHref, verification }) {
  const [open, setOpen] = useState(defaultOpen ?? idx === 1);   // collapsed by default; first expanded
  return (
    <div className="au-rep-card" id={`rec-${r.id}`} style={{ scrollMarginTop: 120 }}>
      <button id={`rec-h-${r.id}`} className="au-rep-card-h" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="au-rep-pri" style={{ background: AU_PRIORITY[r.priority] }}>{r.priority}</span>
        <span className="au-rep-card-t">{idx}. {r.issue_title}</span>
        <VerificationBadge verification={verification} />
        <span className="au-rep-card-meta">{r.category} · score <b style={{ color: auScoreColor(r.score) }}>{r.score}</b>{r.estimated_fix_time ? ` · ${r.estimated_fix_time}` : ""}</span>
        <ChevronDown size={16} className="au-rep-chev" style={{ transform: open ? "rotate(180deg)" : "none" }} />
      </button>
      {open && (
        <>
          <RecommendationCard r={r} ins={ins} />
          {/* After implementing the fix and re-scanning, Scan Details' own signal
              card has the full before/after comparison (verification.js) — deep-link
              there rather than duplicating that state/logic on this page. Never claims
              "verified" itself; it only offers to go check. */}
          {verifyHref && (
            <div className="au-rep-verify-link">
              <Link to={verifyHref}><History size={12} /> Verify after re-scan</Link>
            </div>
          )}
        </>
      )}
    </div>
  );
}
