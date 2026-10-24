/* AI Answer Tracking — MIGRATED to Spectrum. Data flow, hooks, effects, handlers, polling,
   and the exported helpers/components (groupByProvider, gapCountLabel, Leaderboard,
   ProviderGroup) are byte-for-byte unchanged; only JSX + class names + chart theming changed.
   `.spectrum-screen`-scoped. (Fixed a latent pre-existing bug: Trash2 was used but never
   imported — now imported so the prompt delete button works.) */
import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Plus, Play, Info, RefreshCw, Check, X, Pencil, Trash2,
  ArrowUpRight, ArrowDownRight, Minus, AlertTriangle, ChevronRight, ChevronDown,
} from "lucide-react";
import {
  Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  listMonitors, getMonitorAnswerTracking, addMonitorPrompt, runMonitorAnswerTracking,
  getMonitorAnswerTrackingTrend, updatePrompt, deletePrompt,
  getPromptRun, getPromptRunSummary, getPromptRunResults, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { Shell, Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import { AIVisibilityPanel } from "./AIVisibility.jsx";
import AnswerSimulator from "./AnswerSimulator.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import "./AnswerTracking.spectrum.css";

const RUN_STATUS_LABEL = {
  pending: "Pending", running: "Running", completed: "Completed",
  failed: "Failed", partial: "Partial",
};
const RUN_STATUS_COLOR = {
  completed: "var(--sp-blue-d)", partial: "var(--sp-lav-d)", failed: "var(--sp-violet-d)",
  running: "var(--sp-primary)", pending: "var(--sp-muted)",
};

export default function AnswerTracking({ selectedMonitorId = null, selectedRunId = null, currentScanDomain = null }) {
  const { hasPermission } = useAuth();
  const navigate = useNavigate();
  const canRun = hasPermission("scan:run");

  const [monitors, setMonitors] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await listMonitors();
      setMonitors(res.monitors || res || []);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load your sites.");
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  // A single site is auto-selected (no selector) — redirect so the URL carries the
  // choice. BUT: when we know what site the user is actually working with right now
  // (currentScanDomain, from their most recent scan), auto-select ONLY a monitor
  // whose domain matches it — never the org's sole PRE-EXISTING monitor for an
  // unrelated site. Without this guard, an org with exactly one (old) monitor would
  // silently land a user who just scanned a brand-new, not-yet-monitored site on
  // that old monitor's questions/evidence/tracked state — a real cross-site data
  // leak, not a convenience. When currentScanDomain is unknown (e.g. no scans at
  // all yet), the original single-monitor convenience still applies.
  const matchingCurrentDomain = (monitors && currentScanDomain)
    ? monitors.filter((m) => m.domain === currentScanDomain) : [];
  const sole = currentScanDomain
    ? (matchingCurrentDomain.length === 1 ? matchingCurrentDomain[0] : null)
    : (monitors && monitors.length === 1 ? monitors[0] : null);
  useEffect(() => {
    if (sole && !selectedMonitorId) {
      navigate(`/app/answer-tracking/${encodeURIComponent(sole.id)}`, { replace: true });
    }
  }, [sole, selectedMonitorId, navigate]);

  const active = monitors && selectedMonitorId ? monitors.find((m) => m.id === selectedMonitorId) || null : null;
  // No monitor at all matches the site the user is currently working with — real
  // monitors may still exist (for OTHER sites), so offer the selector rather than
  // showing nothing, but never silently default into one of them.
  const noDomainMatch = !!(currentScanDomain && monitors && monitors.length > 0
    && matchingCurrentDomain.length === 0);
  const showSelector = monitors && (monitors.length > 1 || noDomainMatch);
  // An explicit monitor id in the URL always wins (a bookmark/deep link/deliberate
  // selector choice is authoritative — never overridden), but if it genuinely
  // doesn't match the site the user is currently working with, say so. Small,
  // non-blocking — the user stays exactly where they navigated.
  const domainMismatch = !!(active && currentScanDomain && active.domain !== currentScanDomain);

  return (
    <div className="spectrum-screen">
      <Shell>
        <Explainer />

        {error && !monitors ? (
          <Cell solid style={{ marginTop: 14 }}><div className="sp-card-center" role="alert">
            <div className="sp-ill sp-ill-bad"><AlertTriangle size={26} /></div>
            <div className="sp-card-s" style={{ marginBottom: 20 }}>{error}</div>
            <Button variant="gradient" onClick={load}>Retry</Button>
          </div></Cell>
        ) : !monitors ? (
          <Cell solid style={{ marginTop: 14 }}><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} h={44} />)}</div></Cell>
        ) : monitors.length === 0 ? (
          <Cell solid style={{ marginTop: 14 }}>
            <div className="sp-dim" style={{ padding: "10px 2px", fontSize: 13 }}>
              No sites yet. Add a site under <b>Monitoring</b> first — Answer Tracking prompts
              belong to a site.
            </div>
          </Cell>
        ) : (
          <>
            {active && (
              // Compact context indicator — makes it immediately obvious which site's
              // data is on screen, so a cross-site mismatch (like the one this fixes)
              // is never silently invisible again.
              <div className="sp-at-context">
                Answer Tracking <span className="sp-at-context-domain">{active.domain || active.normalized_url || active.url}</span>
              </div>
            )}

            {domainMismatch && (
              // The user explicitly navigated/selected this monitor — that choice is
              // never overridden — but it doesn't match their current scan's site, so
              // say so plainly rather than leaving it silently ambiguous.
              <div className="sp-at-mismatch">
                You are viewing Answer Tracking for <b>{active.domain}</b>, while this scan is for <b>{currentScanDomain}</b>.
              </div>
            )}

            {noDomainMatch && !active && (
              <Cell solid style={{ marginTop: 14 }}>
                <div className="sp-dim" style={{ padding: "10px 2px", fontSize: 13 }}>
                  No Answer Tracking site is set up yet for <b>{currentScanDomain}</b>. Add it under{" "}
                  <b>Monitoring</b>, or pick one of your existing tracked sites below.
                </div>
              </Cell>
            )}

            {showSelector && (
              <div className="sp-at-siteselect">
                <label className="sp-at-siteselect-h" htmlFor="at-site">Site</label>
                <select id="at-site" className="sp-select" value={selectedMonitorId || ""}
                        onChange={(e) => e.target.value &&
                          navigate(`/app/answer-tracking/${encodeURIComponent(e.target.value)}`)}>
                  <option value="" disabled>Select a site…</option>
                  {monitors.map((m) => (
                    <option key={m.id} value={m.id}>{m.name || m.normalized_url || m.url}</option>
                  ))}
                </select>
              </div>
            )}

            {active
              ? <SiteAnswerTracking key={active.id} monitorId={active.id}
                                    selectedRunId={selectedRunId} canRun={canRun} />
              : showSelector && !noDomainMatch && (
                  <Cell solid style={{ marginTop: 14 }}>
                    <div className="sp-dim" style={{ fontSize: 13, padding: "6px 2px" }}>
                      Select a site above to manage its prompts and see results.
                    </div>
                  </Cell>
                )}
          </>
        )}
      </Shell>
    </div>
  );
}

function Explainer() {
  return (
    <div className="sp-at-explainer">
      <Info size={15} />
      <span>
        The <b>AEO Answer Simulator</b> below estimates how well an AI answer can be supported
        using your website&apos;s own scanned content — at no cost, with no external AI calls by
        default. It is not a live measurement of ChatGPT, Claude, Gemini, or Perplexity responses.
        For that, see the <b>Provider Tracking (Premium)</b> section, which samples real AI
        assistants through their provider APIs.
      </span>
    </div>
  );
}

function SiteAnswerTracking({ monitorId, selectedRunId, canRun }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [runMsg, setRunMsg] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await getMonitorAnswerTracking(monitorId));
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load this site’s prompts.");
    }
  }, [monitorId]);

  useEffect(() => { load(); }, [load]);

  const atMax = data && data.prompts.length >= data.max_prompts;

  const add = async (value) => {
    const t = (value ?? text).trim();
    if (!t) return;
    setBusy(true); setError(null);
    try {
      await addMonitorPrompt(monitorId, t);
      setText("");
      await load();
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not add the prompt.");
    } finally { setBusy(false); }
  };

  const toggle = async (p) => {
    try { await updatePrompt(p.id, { is_active: !p.is_active }); await load(); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not update the prompt."); }
  };

  const removePrompt = async (p) => {
    try { await deletePrompt(p.id); await load(); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not remove the prompt."); }
  };

  const run = async () => {
    setBusy(true); setRunMsg(null); setError(null);
    try {
      const res = await runMonitorAnswerTracking(monitorId);
      setRunMsg({ ok: true, text: `Run ${RUN_STATUS_LABEL[res.status] || res.status}.` });
      await load();
    } catch (e) {
      setRunMsg({ ok: false, text: e instanceof ScanError ? e.message : "Could not start the run." });
    } finally { setBusy(false); }
  };

  if (error && !data) return <Cell solid style={{ marginTop: 14 }}><div className="sp-at-err">{error}</div><Button variant="gradient" onClick={load}>Retry</Button></Cell>;
  if (!data) return <Cell solid style={{ marginTop: 14 }}><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} h={40} />)}</div></Cell>;

  const estimate = data.estimate;
  const estCalls = estimate?.call_count ?? 0;
  const estCost = estimate?.estimated_cost_usd;
  const noProviders = estimate && (!estimate.providers || estimate.providers.length === 0);
  const existing = new Set((data.prompts || []).map((p) => p.text.trim().toLowerCase()));
  const suggestions = (data.suggestions || []).filter((s) => !existing.has(s.trim().toLowerCase()));

  return (
    <>
      <AnswerSimulator monitorId={monitorId} canRun={canRun} />

      <details className="sp-at-premium">
        <summary className="sp-at-premium-summary">Provider Tracking (Premium)</summary>
        <div className="sp-at-premium-body">
      <Cell solid className="sp-at-detail">
        <div className="sp-at-detail-h">
          <span>{data.site_name || data.brand_name || data.site_url}</span>
          <span className="sp-at-detail-sub">{data.prompts.length}/{data.max_prompts} prompts</span>
        </div>

        {error && <div className="sp-at-err">{error}</div>}

        <ul className="sp-at-prompts">
          {data.prompts.length === 0 && (
            <li className="sp-dim" style={{ fontSize: 13 }}>No prompts yet — add one below.</li>
          )}
          {data.prompts.map((p) => (
            <PromptRow key={p.id} p={p} canRun={canRun}
                       onToggle={() => toggle(p)} onRemove={() => removePrompt(p)}
                       onSaved={load} onError={setError} />
          ))}
        </ul>

        {canRun && (
          <>
            <div className="sp-at-add">
              <input className="sp-input" placeholder={atMax ? "Prompt limit reached" : "Add a prompt…"}
                     value={text} maxLength={2000} disabled={atMax}
                     onChange={(e) => setText(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && add()} />
              <Button variant="gradient" disabled={busy || atMax || !text.trim()} onClick={() => add()}>
                <Plus size={15} /> Add
              </Button>
            </div>
            {!atMax && suggestions.length > 0 && (
              <div className="sp-at-suggest">
                <span className="sp-at-suggest-h">Try one:</span>
                {suggestions.map((s) => (
                  <button key={s} className="sp-at-suggest-chip" disabled={busy} onClick={() => add(s)}>
                    <Plus size={12} /> {s}
                  </button>
                ))}
              </div>
            )}
            <div className="sp-at-guide">
              Track <b>category queries</b> a buyer would ask — where you’d want to appear:
              <span className="sp-at-guide-good">“best {"<category>"} tools for {"<audience>"}”</span>
              <span className="sp-at-guide-good">“{"<category>"} software compared”</span>
              Avoid general-knowledge questions unrelated to your category
              <span className="sp-at-guide-bad">“what is agentic AI?”</span>
              — those show 0% with noisy, irrelevant competitors.
            </div>
          </>
        )}
        {atMax && (
          <div className="sp-at-note">
            This site has the maximum of {data.max_prompts} prompts. Remove one to add another.
          </div>
        )}

        {canRun && (
          <div className="sp-at-runbar">
            <Button variant="gradient" loading={busy} disabled={busy || noProviders || estCalls === 0} onClick={run}>
              {!busy && <Play size={15} />} Run now
            </Button>
            <div className="sp-at-est">
              {noProviders ? (
                <span className="sp-at-est-warn">No providers configured — a run would make 0 calls.</span>
              ) : (
                <>Estimated this run: <b>{estCalls}</b> provider call{estCalls === 1 ? "" : "s"}
                  {estimate?.providers?.length ? <> across {estimate.providers.join(", ")}</> : null}
                  {estCost != null && (
                    <div className="sp-at-est-cost">
                      answers <b>${(estimate.answer_cost_usd ?? 0).toFixed(2)}</b>
                      {" + "}analysis <b>${(estimate.extraction_cost_usd ?? 0).toFixed(2)}</b>
                      {" = "}<b>${estCost.toFixed(2)}</b> total
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
        {runMsg && <div className={runMsg.ok ? "sp-at-run-ok" : "sp-at-run-err"}>{runMsg.text}</div>}

        {data.runs?.length > 0 && (
          <div className="sp-at-runs">
            <div className="sp-at-runs-h">Recent runs</div>
            {data.runs.map((r) => (
              <Link key={r.id} className="sp-at-run-row"
                    to={`/app/answer-tracking/${encodeURIComponent(monitorId)}/runs/${encodeURIComponent(r.id)}`}>
                <span className="sp-at-run-status" style={{ color: RUN_STATUS_COLOR[r.status] }}>
                  {RUN_STATUS_LABEL[r.status] || r.status}
                </span>
                <span className="sp-at-run-meta">
                  {r.total_calls} call{r.total_calls === 1 ? "" : "s"}
                  {r.failed_calls ? ` · ${r.failed_calls} failed` : ""}
                  {r.estimated_cost_usd != null ? ` · $${r.estimated_cost_usd.toFixed(2)}` : ""}
                </span>
                <span className="sp-at-run-date">{fmtDate(r.completed_at || r.created_at)}</span>
              </Link>
            ))}
          </div>
        )}
      </Cell>
      <ResultsPanel monitorId={monitorId} runs={data.runs} selectedRunId={selectedRunId} />
        </div>
      </details>
    </>
  );
}

function PromptRow({ p, canRun, onToggle, onRemove, onSaved, onError }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(p.text);

  const save = async () => {
    const t = val.trim();
    if (!t) return;
    try { await updatePrompt(p.id, { text: t }); setEditing(false); onSaved?.(); }
    catch (e) { onError?.(e instanceof ScanError ? e.message : "Could not save the prompt."); }
  };

  return (
    <li className={`sp-at-prompt ${p.is_active ? "" : "off"}`}>
      {editing ? (
        <>
          <input className="sp-input" value={val} maxLength={2000} autoFocus
                 onChange={(e) => setVal(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && save()} />
          <button className="sp-iconbtn" onClick={save} title="Save" aria-label="Save"><Check size={14} /></button>
          <button className="sp-iconbtn" onClick={() => { setEditing(false); setVal(p.text); }} title="Cancel" aria-label="Cancel">
            <X size={14} />
          </button>
        </>
      ) : (
        <>
          <span className="sp-at-prompt-text">{p.text}</span>
          {canRun && (
            <div className="sp-at-prompt-actions">
              <button className="sp-iconbtn" onClick={onToggle}
                      title={p.is_active ? "Deactivate" : "Activate"}>
                {p.is_active ? "Active" : "Off"}
              </button>
              <button className="sp-iconbtn" onClick={() => setEditing(true)} title="Edit"><Pencil size={13} /></button>
              <button className="sp-iconbtn sp-danger" onClick={onRemove} title="Remove"><Trash2 size={13} /></button>
            </div>
          )}
        </>
      )}
    </li>
  );
}


/* ============================ Part B — results ============================ */
const SENT_COLOR = { positive: "var(--sp-blue-d)", neutral: "var(--sp-muted)", negative: "var(--sp-violet-d)" };
// A citation's real provenance — never let an LLM-extracted URL read as a provider-
// reported fact (see backend/app/api/routes_answer_tracking.py's citation_source,
// mirroring extraction.py's own `result.citations is None` branch exactly).
const CITATION_SOURCE_LABEL = { provider: "Provider citation", llm_extracted: "Detected from answer" };
const AXIS = { fill: "var(--sp-muted)", fontSize: 10, fontFamily: "'DM Mono', monospace" };

function pct(v) { return v == null ? "—" : `${v}%`; }

/* Count (not %) of prompts that never mention the brand. Reads correctly at zero. */
export function gapCountLabel(rows) {
  const total = (rows || []).length;
  const gaps = (rows || []).filter((r) => r.is_gap).length;
  if (total === 0) return "no prompts yet";
  if (gaps === 0) return `you're mentioned in all ${total} prompt${total === 1 ? "" : "s"}`;
  return `${gaps} of ${total} prompt${total === 1 ? "" : "s"} never mention you`;
}

// FIX4 — a bare "0 citations" is ambiguous; explain WHICH zero this is.
function citationWhy(diag) {
  switch (diag && diag.status) {
    case "no_provider_reports_citations":
      return "No configured provider can report citations — add a search-grounded provider (e.g. Perplexity) to measure this.";
    case "search_disabled":
      return "Search was disabled on these calls — enable search to measure citations.";
    case "searched_not_cited":
      return "Providers searched but did not cite your brand.";
    default:
      return null;
  }
}

function ResultsPanel({ monitorId, runs, selectedRunId }) {
  const { openUpgrade } = useUpgrade();
  const target = selectedRunId
    ? (runs || []).find((r) => r.id === selectedRunId) || null
    : (runs && runs.length ? runs[0] : null);
  const targetId = target ? target.id : null;
  const [run, setRun] = useState(target);
  const [summary, setSummary] = useState(null);
  const [trend, setTrend] = useState(null);
  const [results, setResults] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [error, setError] = useState(null);

  // Poll the target run until its extraction phase completes, then load the analysis.
  useEffect(() => {
    if (!targetId) { setRun(null); setSummary(null); return undefined; }
    let alive = true;
    let timer = null;
    const tick = async () => {
      try {
        const r = await getPromptRun(targetId);
        if (!alive) return;
        setRun(r);
        if (r.extraction_status === "complete") {
          const [s, t, res] = await Promise.all([
            getPromptRunSummary(targetId),
            getMonitorAnswerTrackingTrend(monitorId, { n: 10 }).catch(() => null),
            getPromptRunResults(targetId).catch(() => null),
          ]);
          if (!alive) return;
          setSummary(s); setTrend(t); setResults(res);
        } else if (r.extraction_status === "pending" || r.extraction_status === "running") {
          timer = setTimeout(tick, 5000);   // still analysing — poll
        }
      } catch (e) {
        if (alive) setError(e instanceof ScanError ? e.message : "Could not load results.");
      }
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [targetId, monitorId]);

  if (!target) {
    return (
      <Cell solid className="sp-at-results">
        <div className="sp-panel-h">Results</div>
        <div className="sp-dim" style={{ fontSize: 13, padding: "6px 2px" }}>
          No runs yet. Run this set to see whether AI assistants mention and cite your brand.
        </div>
      </Cell>
    );
  }

  const analysing = run && (run.extraction_status === "pending" || run.extraction_status === "running");
  const failedExt = run && run.extraction_status === "failed";

  // delta vs previous run (from the trend series, oldest -> newest)
  let delta = null;
  if (trend && trend.runs && trend.runs.length >= 2) {
    const cur = trend.runs[trend.runs.length - 1].mention_rate;
    const prev = trend.runs[trend.runs.length - 2].mention_rate;
    if (cur != null && prev != null) delta = Math.round((cur - prev) * 10) / 10;
  }

  return (
    <>
      {/* Phase 3: negative-first AI Visibility framing, above the detailed run breakdown. */}
      {summary && !analysing && <AIVisibilityPanel runId={targetId} />}
      <Cell solid className="sp-at-results">
      <div className="sp-panel-h">Results <span className="sp-sub">latest run</span></div>

      {error && <div className="sp-at-err">{error}</div>}
      {analysing && (
        <div className="sp-at-analysing"><RefreshCw size={14} className="spin" /> Analysing responses…</div>
      )}
      {failedExt && <div className="sp-at-err">Analysis failed for this run. Try re-analysing it.</div>}

      {summary && !analysing && (
        <>
          {summary.excluded_extraction_failures > 0 && (
            <div className="sp-at-note"><AlertTriangle size={13} /> {summary.excluded_extraction_failures} sample(s)
              excluded — extraction failed on them (not counted as “not mentioned”).</div>
          )}

          {/* headline */}
          <div className="sp-at-headline">
            <div>
              <div className="sp-at-metric-v">{pct(summary.mention_rate)}
                {delta != null && <DeltaBadge d={delta} />}
              </div>
              <div className="sp-at-metric-l">Mention rate <span className="sp-dim">· {summary.analyzed_count} samples</span></div>
            </div>
            <div>
              <div className="sp-at-metric-v">{summary.citation_count}</div>
              <div className="sp-at-metric-l">Brand citations</div>
              {summary.citation_count === 0 && (
                <div className="sp-at-cite-why">{citationWhy(summary.citation_diagnosis)}</div>
              )}
            </div>
            {summary.average_position != null && (
              <div>
                <div className="sp-at-metric-v">#{summary.average_position}</div>
                <div className="sp-at-metric-l">Avg. position</div>
              </div>
            )}
          </div>

          {/* Provider breakdown, competitor share of voice and the run-level leaderboard
              now live ONLY in the AI Visibility panel above (see AIVisibility.jsx) — this
              page stays the operational workspace for individual prompt runs/results and
              no longer repeats that aggregate/competitive intelligence. */}

          {/* sentiment */}
          {Object.keys(summary.sentiment || {}).length > 0 && (
            <div className="sp-at-block">
              <div className="sp-at-block-h">Sentiment across mentions</div>
              <div className="sp-at-sent">
                {["positive", "neutral", "negative"].filter((k) => summary.sentiment[k]).map((k) => (
                  <span key={k} className="sp-at-sent-chip" style={{ color: SENT_COLOR[k] }}>
                    {k}: {summary.sentiment[k]}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* per-prompt table — the operational, prompt-level view: does your brand
              appear for THIS query, and the raw provider evidence behind that verdict.
              The aggregate "who's winning your category" leaderboard is in AI Visibility. */}
          <div className="sp-at-block">
            <div className="sp-at-block-h">By prompt <span className="sp-dim">· {gapCountLabel(summary.per_prompt)}</span></div>
            <div className="sp-at-ptable">
              {summary.per_prompt.map((row) => (
                <PromptResultRow key={row.prompt_id} row={row}
                                 open={expanded === row.prompt_id}
                                 onToggle={() => setExpanded(expanded === row.prompt_id ? null : row.prompt_id)}
                                 results={results} />
              ))}
            </div>
          </div>

          {/* cited URLs (full list is Pro — see backend gate_run_summary) */}
          {(summary.cited_urls.length > 0 || summary.locked_cited_url_count > 0) && (
            <div className="sp-at-block">
              <div className="sp-at-block-h">Cited brand URLs</div>
              {summary.cited_urls.map((u) => (
                <div key={u.url} className="sp-at-url-row">
                  <span className="sp-at-url">{u.url}</span>
                  <span className="sp-at-url-n">{u.count}×</span>
                </div>
              ))}
              {summary.locked_cited_url_count > 0 && (
                <button type="button" className="sp-at-lb-clear" onClick={() => openUpgrade("ai_visibility")}>
                  {summary.locked_cited_url_count} cited URL{summary.locked_cited_url_count === 1 ? "" : "s"} — unlock with Pro
                </button>
              )}
            </div>
          )}

          {/* trend across recent runs */}
          {trend && trend.runs.length >= 2 && <TrendChart trend={trend} />}
        </>
      )}
      </Cell>
    </>
  );
}

export function Leaderboard({ summary, filterEntity, onSelect }) {
  const board = summary.leaderboard || [];
  const excluded = summary.leaderboard_excluded || 0;
  const minApp = summary.leaderboard_min_appearances;
  if (board.length === 0) {
    return (
      <div className="sp-at-block">
        <div className="sp-at-block-h">Who’s winning your category</div>
        <div className="sp-dim" style={{ fontSize: 12 }}>
          No entity was recommended in at least {minApp} samples yet
          {excluded > 0
            ? ` — ${excluded} ${excluded === 1 ? "entity" : "entities"} appeared too rarely to rank.`
            : "."}
        </div>
      </div>
    );
  }
  const maxRate = Math.max(...board.map((e) => e.appearance_rate || 0), 1);
  return (
    <div className="sp-at-block">
      <div className="sp-at-block-h">Who’s winning your category
        <span className="sp-dim"> · click a row to filter prompts below</span></div>
      <div className="sp-at-lb">
        <div className="sp-at-lb-head">
          <span className="sp-at-lb-rank">#</span>
          <span className="sp-at-lb-name">Entity</span>
          <span className="sp-at-lb-delta" title="rank change vs previous run">Δ</span>
          <span className="sp-at-lb-bar" />
          <span className="sp-at-lb-rate">Rate</span>
          <span className="sp-at-lb-cov" title="distinct prompts it appears in">Prompts</span>
          <span className="sp-at-lb-h2h" title="prompts where it appears but you do not">Vs you</span>
        </div>
        {board.map((e) => {
          const active = filterEntity && filterEntity.name === e.name;
          return (
            <button key={e.name} type="button"
                    className={`sp-at-lb-row${e.is_you ? " you" : ""}${active ? " active" : ""}`}
                    onClick={() => onSelect(e)}>
              <span className="sp-at-lb-rank">#{e.rank}</span>
              <span className="sp-at-lb-name">
                {e.name}{e.is_you && <span className="sp-at-lb-tag">you</span>}
              </span>
              <RankDelta d={e.rank_delta} />
              <span className="sp-at-lb-bar">
                <span className="sp-at-lb-fill"
                      style={{ width: `${Math.max(2, (e.appearance_rate || 0) / maxRate * 100)}%` }} />
              </span>
              <span className="sp-at-lb-rate">{pct(e.appearance_rate)}</span>
              <span className="sp-at-lb-cov">{e.prompt_coverage}</span>
              <span className={`sp-at-lb-h2h${e.head_to_head > 0 && !e.is_you ? " hot" : ""}`}>
                {e.head_to_head > 0 ? e.head_to_head : "—"}
              </span>
            </button>
          );
        })}
      </div>
      {excluded > 0 && (
        <div className="sp-dim" style={{ fontSize: 11, marginTop: 6 }}>
          {excluded} more {excluded === 1 ? "entity" : "entities"} below the {minApp}-sample
          threshold not shown.
        </div>
      )}
    </div>
  );
}

/* Rank movement vs the previous run: up = toward #1 (good). Null when new or no prior run. */
function RankDelta({ d }) {
  if (d == null) return <span className="sp-at-lb-delta flat">·</span>;
  if (d === 0) return <span className="sp-at-lb-delta flat"><Minus size={10} /></span>;
  const up = d > 0;
  return (
    <span className={`sp-at-lb-delta ${up ? "up" : "down"}`}>
      {up ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}{Math.abs(d)}
    </span>
  );
}

function DeltaBadge({ d }) {
  if (d === 0) return <span className="sp-at-delta flat"><Minus size={12} /> 0</span>;
  const up = d > 0;
  return (
    <span className={`sp-at-delta ${up ? "up" : "down"}`}>
      {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{Math.abs(d)}
    </span>
  );
}

function PromptResultRow({ row, open, onToggle, results }) {
  const group = results && results.prompts ? results.prompts.find((p) => p.prompt_id === row.prompt_id) : null;
  return (
    <div className={`sp-at-prow ${row.is_gap ? "gap" : ""}`}>
      <button className="sp-at-prow-head" onClick={onToggle}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="sp-at-prow-text">{row.text}</span>
        {row.irrelevant_hint && <span className="sp-at-tag warn" title="No mentions and no category competitors">off-category?</span>}
        <span className="sp-at-prow-rate" style={{ color: row.is_gap ? "var(--sp-violet-d)" : "var(--sp-ink)" }}>
          {pct(row.mention_rate)}
        </span>
        <span className="sp-at-prow-n">{row.mentions}/{row.samples}</span>
      </button>
      {open && (
        <div className="sp-at-prow-body">
          {/* "Why not you — and what to do" now lives ONLY in the AI Visibility panel's
              Content gaps section above, so it isn't shown twice on this page. */}
          {row.irrelevant_hint && <InformationalHint />}
          {group && groupByProvider(group.results).map((g) => (
            <ProviderGroup key={g.provider} g={g}
                           ctx={{ hasGap: !!row.gap, informational: !!row.irrelevant_hint }} />
          ))}
        </div>
      )}
    </div>
  );
}

/* CHANGE 4b — informational-prompt hint (never auto-disables/edits the prompt). */
function InformationalHint() {
  return (
    <div className="sp-at-hint">
      <b>Looks informational.</b> No provider recommended any brand for this question, so it can’t
      measure your visibility — it reads as an informational query, not a buying one. Try a
      category buying query instead, e.g. <i>“best {"<category>"} tools for {"<audience>"}”</i>.
      This is just a hint — we won’t change your prompt.
    </div>
  );
}

/* Collapse a prompt's individual samples into ONE block per provider. */
export function groupByProvider(samples) {
  const order = [];
  const byProvider = new Map();
  for (const r of samples || []) {
    if (!byProvider.has(r.provider)) { byProvider.set(r.provider, []); order.push(r.provider); }
    byProvider.get(r.provider).push(r);
  }
  const groups = order.map((provider) => {
    const rs = byProvider.get(provider);
    const total = rs.length;
    const named = rs.filter((r) => r.brand_mentioned === true);
    const allExtractionFailed = rs.every((r) => r.extraction_failed === true);

    let ref = rs[0];
    if (named.length) {
      ref = named.reduce((best, r) => {
        const bp = best.position == null ? Infinity : best.position;
        const rp = r.position == null ? Infinity : r.position;
        return rp < bp ? r : best;
      }, named[0]);
    }
    const seenE = new Set();
    const recommended = [];
    const pushEntities = (list) => {
      for (const e of (list || [])) {
        const key = (e.name || "").trim().toLowerCase();
        if (key && !seenE.has(key)) { seenE.add(key); recommended.push(e); }
      }
    };
    pushEntities(ref.recommended_entities);
    for (const r of rs) if (r !== ref) pushEntities(r.recommended_entities);

    const namedWithCtx = named.find((r) => r.mention_context);
    const seenU = new Set();
    const citations = [];
    for (const r of rs) for (const u of (r.brand_urls_cited || [])) {
      if (u && !seenU.has(u)) { seenU.add(u); citations.push({ url: u, source: r.citation_source }); }
    }
    const namedWithSent = named.find((r) => r.sentiment);
    const searchOff = rs.some((r) => r.search_enabled === false);

    const recKey = (r) => JSON.stringify(
      (r.recommended_entities || []).map((e) => (e.name || "").trim().toLowerCase()).sort());
    const verdictKey = (r) => `${r.brand_mentioned}|${recKey(r)}`;
    const identical = total > 1 && rs.every((r) => verdictKey(r) === verdictKey(rs[0]));

    return {
      provider, samples: rs, total, namedCount: named.length,
      anyNamed: named.length > 0, allExtractionFailed, identical,
      sentence: namedWithCtx ? namedWithCtx.mention_context : null,
      recommended, citations,
      sentiment: namedWithSent ? namedWithSent.sentiment : null,
      position: named.length ? ref.position : null,
      searchOff,
    };
  });
  return groups.sort((a, b) => (b.anyNamed === a.anyNamed ? 0 : b.anyNamed ? 1 : -1));
}

const _LETTERS = "ABCDEFGH";

/* One provider's result for a prompt, aggregated across its samples. */
export function ProviderGroup({ g, ctx }) {
  const [showRaw, setShowRaw] = useState(false);
  const shown = g.identical ? g.samples.slice(0, 1) : g.samples;   // identical => show one
  return (
    <div className="sp-at-verdict">
      <div className="sp-at-verdict-h">
        <b>{g.provider}</b>
        {g.allExtractionFailed
          ? <span className="sp-at-tag warn">extraction failed</span>
          : g.namedCount > 0
            ? <span className="sp-at-tag ok">Named in {g.namedCount} of {g.total}</span>
            : <span className="sp-at-tag no">Not named in any of {g.total}</span>}
        {g.total > 1 && (
          g.identical
            ? <span className="sp-at-tag" title="Both samples agreed">{g.total} identical responses</span>
            : <span className="sp-at-tag warn" title="Samples disagreed — an unstable position">responses differed</span>
        )}
        {g.sentiment && <span className="sp-at-tag" style={{ color: SENT_COLOR[g.sentiment] }}>{g.sentiment}</span>}
        {g.position != null && <span className="sp-at-tag">#{g.position}</span>}
        {g.searchOff && <span className="sp-at-tag warn" title="These samples ran without web search — citations may be unavailable">no web search</span>}
      </div>
      {g.namedCount > 0 && g.sentence && (
        <div className="sp-at-verdict-body"><div className="sp-at-quote">“{g.sentence}”</div></div>
      )}
      {g.citations.length > 0 && (
        <div className="sp-at-verdict-body">
          {g.citations.map((c) => (
            <div key={c.url} className="sp-at-cite-row">
              <a className="sp-at-cite" href={c.url} target="_blank" rel="noreferrer">{c.url}</a>
              <span className="sp-at-cite-src">{CITATION_SOURCE_LABEL[c.source] || CITATION_SOURCE_LABEL.llm_extracted}</span>
            </div>
          ))}
        </div>
      )}
      {g.recommended.length > 0 ? (
        <div className="sp-at-verdict-body">
          <div className="sp-at-instead">
            <span className="sp-dim">{g.namedCount > 0 ? "Also recommended:" : "Recommended instead:"}</span>{" "}
            {g.recommended.map((e, idx) => (
              <span key={idx} className="sp-at-chip">{idx + 1}. {e.name}</span>
            ))}
          </div>
        </div>
      ) : (g.namedCount === 0 && !g.allExtractionFailed && (
        <div className="sp-at-verdict-body"><EmptyRecommendation ctx={ctx} /></div>
      ))}
      <button className="sp-at-rawtoggle" onClick={() => setShowRaw((s) => !s)}>
        {showRaw ? "Hide" : "View"} full response{!g.identical && g.total > 1 ? "s" : ""}
      </button>
      {showRaw && (
        <div className="sp-at-verdict-samples">
          {shown.map((r, i) => (
            <SampleVerdict key={i} r={r} ctx={ctx}
                           label={g.total > 1 && !g.identical ? `Response ${_LETTERS[i]}` : null} />
          ))}
        </div>
      )}
    </div>
  );
}

/* CHANGE 3 — replaces the useless empty line, in priority order. */
function EmptyRecommendation({ ctx }) {
  if (ctx?.hasGap) {
    return <div className="sp-dim" style={{ fontSize: 12 }}>
      No brand recommended — see “Content gaps” in AI Visibility above for why and what to do.</div>;
  }
  if (ctx?.informational) {
    return <div className="sp-dim" style={{ fontSize: 12 }}>
      No provider recommended a brand — this reads as an informational question, not a buying
      query, so it can’t show brand visibility.</div>;
  }
  return <div className="sp-dim" style={{ fontSize: 12 }}>No specific brands were recommended in this response.</div>;
}

/* One prompt×provider sample as a structured VERDICT. `label` is a human "Response A/B". */
function SampleVerdict({ r, label, ctx }) {
  const [showRaw, setShowRaw] = useState(false);
  const mentioned = r.brand_mentioned === true;
  return (
    <div className="sp-at-verdict">
      <div className="sp-at-verdict-h">
        <b>{r.provider}</b>
        {label && <span className="sp-dim"> · {label}</span>}
        {r.extraction_failed
          ? <span className="sp-at-tag warn">extraction failed</span>
          : mentioned
            ? <span className="sp-at-tag ok">MENTIONED</span>
            : <span className="sp-at-tag no">NOT MENTIONED</span>}
        {!r.extraction_failed && mentioned && r.sentiment
          && <span className="sp-at-tag" style={{ color: SENT_COLOR[r.sentiment] }}>{r.sentiment}</span>}
        {!r.extraction_failed && mentioned && r.position != null && <span className="sp-at-tag">#{r.position}</span>}
      </div>
      {!r.extraction_failed && mentioned && (
        <div className="sp-at-verdict-body">
          {r.mention_context && <div className="sp-at-quote">“{r.mention_context}”</div>}
          {(r.brand_urls_cited || []).map((u) => (
            <div key={u} className="sp-at-cite-row">
              <a className="sp-at-cite" href={u} target="_blank" rel="noreferrer">{u}</a>
              <span className="sp-at-cite-src">{CITATION_SOURCE_LABEL[r.citation_source] || CITATION_SOURCE_LABEL.llm_extracted}</span>
            </div>
          ))}
        </div>
      )}
      {!r.extraction_failed && !mentioned && (
        <div className="sp-at-verdict-body">
          {(r.recommended_entities || []).length > 0 ? (
            <div className="sp-at-instead">
              <span className="sp-dim">Recommended instead:</span>{" "}
              {r.recommended_entities.map((e, idx) => (
                <span key={idx} className="sp-at-chip">{idx + 1}. {e.name}</span>
              ))}
            </div>
          ) : (
            <EmptyRecommendation ctx={ctx} />
          )}
        </div>
      )}
      <button className="sp-at-rawtoggle" onClick={() => setShowRaw((s) => !s)}>
        {showRaw ? "Hide" : "View"} full response
      </button>
      {showRaw && <RawText text={r.raw_response} error={r.error} highlight={r.mention_context} />}
    </div>
  );
}


function RawText({ text, error, highlight }) {
  if (error) return <div className="sp-at-raw-t sp-dim">No answer — {error}</div>;
  if (!text) return <div className="sp-at-raw-t sp-dim">(empty)</div>;
  if (highlight && text.includes(highlight)) {
    const [before, after] = text.split(highlight);
    return <div className="sp-at-raw-t">{before}<mark className="sp-at-mark">{highlight}</mark>{after}</div>;
  }
  return <div className="sp-at-raw-t">{text}</div>;
}

function TrendChart({ trend }) {
  const data = trend.runs.map((r, i) => ({
    i: i + 1, rate: r.mention_rate,
    changed: r.model_changed || r.search_changed,
    searchChanged: r.search_changed,
    date: fmtDate(r.created_at),
  }));
  const anySearchChange = data.some((d) => d.searchChanged);
  const renderDot = ({ cx, cy, payload }) => (
    payload.changed
      ? <rect x={cx - 4} y={cy - 4} width={8} height={8} fill="var(--sp-lav-d)" stroke="var(--sp-solid)" strokeWidth={1.5} />
      : <circle cx={cx} cy={cy} r={3} fill="var(--sp-primary)" />
  );
  return (
    <div className="sp-at-block">
      <div className="sp-at-block-h">Trend <span className="sp-dim">· ▪ marks a model{anySearchChange ? " or web-search" : ""} change</span></div>
      <div className="sp-at-chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 10, bottom: 0, left: -22 }}>
            <CartesianGrid stroke="var(--sp-line)" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ background: "var(--sp-solid)", border: "1px solid var(--sp-line)", borderRadius: 12, fontSize: 12, boxShadow: "var(--sp-sh-s)" }} />
            <Line type="monotone" dataKey="rate" stroke="var(--sp-primary)" strokeWidth={2}
                  dot={renderDot} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
