/* AI Answer Tracking — prompt management (Part A) + results (Part B).
   Create prompt sets, manage prompts (max enforced in the UI), run with a live cost
   estimate, and below the prompt list see the analysis: mention rate + delta, provider
   and competitor share of voice, per-prompt gaps, cited URLs, expandable raw responses
   with the mention highlighted, and a run-over-run trend that marks model changes. */
import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Plus, Play, Info, RefreshCw, Check, X, Pencil,
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
import { ErrorState, TableSkeleton, fmtDate } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";

const RUN_STATUS_LABEL = {
  pending: "Pending", running: "Running", completed: "Completed",
  failed: "Failed", partial: "Partial",
};
const RUN_STATUS_COLOR = {
  completed: "var(--good)", partial: "var(--warn)", failed: "var(--bad)",
  running: "var(--accent)", pending: "var(--txt-dim)",
};

export default function AnswerTracking({ selectedMonitorId = null, selectedRunId = null }) {
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

  // A single site is auto-selected (no selector) — redirect so the URL carries the choice.
  const sole = monitors && monitors.length === 1 ? monitors[0] : null;
  useEffect(() => {
    if (sole && !selectedMonitorId) {
      navigate(`/app/answer-tracking/${encodeURIComponent(sole.id)}`, { replace: true });
    }
  }, [sole, selectedMonitorId, navigate]);

  if (error && !monitors) return <ErrorState message={error} onRetry={load} />;
  if (!monitors) return <TableSkeleton rows={4} />;

  const active = selectedMonitorId ? monitors.find((m) => m.id === selectedMonitorId) || null : null;
  const showSelector = monitors.length > 1;   // one site => auto-selected, selector hidden (CHANGE 1d)

  return (
    <div className="at-wrap">
      <Explainer />

      {monitors.length === 0 ? (
        <div className="d-panel" style={{ marginTop: 14 }}>
          <div className="d-dim" style={{ padding: "10px 2px", fontSize: 13 }}>
            No sites yet. Add a site under <b>Monitoring</b> first — Answer Tracking prompts
            belong to a site.
          </div>
        </div>
      ) : (
        <>
          {showSelector && (
            <div className="d-panel at-siteselect" style={{ marginTop: 14 }}>
              <label className="at-siteselect-h" htmlFor="at-site">Site</label>
              <select id="at-site" className="d-select" value={selectedMonitorId || ""}
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
            : showSelector && (
                <div className="d-panel" style={{ marginTop: 14 }}>
                  <div className="d-dim" style={{ fontSize: 13, padding: "6px 2px" }}>
                    Select a site above to manage its prompts and see results.
                  </div>
                </div>
              )}
        </>
      )}
    </div>
  );
}

function Explainer() {
  return (
    <div className="at-explainer">
      <Info size={15} />
      <span>
        Answer Tracking samples AI assistants through their <b>provider APIs</b>. Results are
        sampled and may differ from what a person sees in the consumer chat apps. It measures
        whether your brand appears in those API responses — not a guarantee of what any one user
        will be shown.
      </span>
    </div>
  );
}

/* One site's prompt list + run controls + results. Prompts belong to the site (monitor);
   the prompt-set concept is gone from the UI. */
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
      // A 429 here is a cost guard (interval / monthly limit); its message names the
      // next eligible time and is safe to show directly.
      setRunMsg({ ok: false, text: e instanceof ScanError ? e.message : "Could not start the run." });
    } finally { setBusy(false); }
  };

  if (error && !data) return <div className="d-panel" style={{ marginTop: 14 }}><ErrorState message={error} onRetry={load} /></div>;
  if (!data) return <div className="d-panel" style={{ marginTop: 14 }}><TableSkeleton rows={3} /></div>;

  const estimate = data.estimate;
  const estCalls = estimate?.call_count ?? 0;
  const estCost = estimate?.estimated_cost_usd;
  const noProviders = estimate && (!estimate.providers || estimate.providers.length === 0);
  const existing = new Set((data.prompts || []).map((p) => p.text.trim().toLowerCase()));
  const suggestions = (data.suggestions || []).filter((s) => !existing.has(s.trim().toLowerCase()));

  return (
    <>
    <div className="d-panel at-detail" style={{ marginTop: 14 }}>
      <div className="d-panel-h at-detail-h">
        <span>{data.site_name || data.brand_name || data.site_url}</span>
        <span className="at-detail-sub">{data.prompts.length}/{data.max_prompts} prompts</span>
      </div>

      {error && <div className="at-err">{error}</div>}

      {/* prompts */}
      <ul className="at-prompts">
        {data.prompts.length === 0 && (
          <li className="d-dim" style={{ fontSize: 13 }}>No prompts yet — add one below.</li>
        )}
        {data.prompts.map((p) => (
          <PromptRow key={p.id} p={p} canRun={canRun}
                     onToggle={() => toggle(p)} onRemove={() => removePrompt(p)}
                     onSaved={load} onError={setError} />
        ))}
      </ul>

      {canRun && (
        <>
          <div className="at-add">
            <input className="d-input" placeholder={atMax ? "Prompt limit reached" : "Add a prompt…"}
                   value={text} maxLength={2000} disabled={atMax}
                   onChange={(e) => setText(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && add()} />
            <button className="d-btn" disabled={busy || atMax || !text.trim()} onClick={() => add()}>
              <Plus size={15} /> Add
            </button>
          </div>
          {/* CHANGE 4a — one-tap starter prompts built from this site's brand */}
          {!atMax && suggestions.length > 0 && (
            <div className="at-suggest">
              <span className="at-suggest-h">Try one:</span>
              {suggestions.map((s) => (
                <button key={s} className="at-suggest-chip" disabled={busy} onClick={() => add(s)}>
                  <Plus size={12} /> {s}
                </button>
              ))}
            </div>
          )}
          <div className="at-guide">
            Track <b>category queries</b> a buyer would ask — where you’d want to appear:
            <span className="at-guide-good">“best {"<category>"} tools for {"<audience>"}”</span>
            <span className="at-guide-good">“{"<category>"} software compared”</span>
            Avoid general-knowledge questions unrelated to your category
            <span className="at-guide-bad">“what is agentic AI?”</span>
            — those show 0% with noisy, irrelevant competitors.
          </div>
        </>
      )}
      {atMax && (
        <div className="at-note">
          This site has the maximum of {data.max_prompts} prompts. Remove one to add another.
        </div>
      )}

      {/* run + cost estimate (shown, not hidden) */}
      {canRun && (
        <div className="at-runbar">
          <button className="d-btn primary" disabled={busy || noProviders || estCalls === 0} onClick={run}>
            {busy ? <RefreshCw size={15} className="spin" /> : <Play size={15} />} Run now
          </button>
          <div className="at-est">
            {noProviders ? (
              <span className="at-est-warn">No providers configured — a run would make 0 calls.</span>
            ) : (
              <>Estimated this run: <b>{estCalls}</b> provider call{estCalls === 1 ? "" : "s"}
                {estimate?.providers?.length ? <> across {estimate.providers.join(", ")}</> : null}
                {estCost != null && (
                  <div className="at-est-cost">
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
      {runMsg && <div className={runMsg.ok ? "at-run-ok" : "at-run-err"}>{runMsg.text}</div>}

      {/* run history — status/progress only, no analysis */}
      {data.runs?.length > 0 && (
        <div className="at-runs">
          <div className="at-runs-h">Recent runs</div>
          {data.runs.map((r) => (
            <Link key={r.id} className="at-run-row"
                  to={`/app/answer-tracking/${encodeURIComponent(monitorId)}/runs/${encodeURIComponent(r.id)}`}>
              <span className="at-run-status" style={{ color: RUN_STATUS_COLOR[r.status] }}>
                {RUN_STATUS_LABEL[r.status] || r.status}
              </span>
              <span className="at-run-meta">
                {r.total_calls} call{r.total_calls === 1 ? "" : "s"}
                {r.failed_calls ? ` · ${r.failed_calls} failed` : ""}
                {r.estimated_cost_usd != null ? ` · $${r.estimated_cost_usd.toFixed(2)}` : ""}
              </span>
              <span className="at-run-date">{fmtDate(r.completed_at || r.created_at)}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
    <ResultsPanel monitorId={monitorId} runs={data.runs} selectedRunId={selectedRunId} />
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
    <li className={`at-prompt ${p.is_active ? "" : "off"}`}>
      {editing ? (
        <>
          <input className="d-input" value={val} maxLength={2000} autoFocus
                 onChange={(e) => setVal(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && save()} />
          <button className="d-iconbtn" onClick={save} title="Save"><Check size={14} /></button>
          <button className="d-iconbtn" onClick={() => { setEditing(false); setVal(p.text); }} title="Cancel">
            <X size={14} />
          </button>
        </>
      ) : (
        <>
          <span className="at-prompt-text">{p.text}</span>
          {canRun && (
            <div className="at-prompt-actions">
              <button className="d-iconbtn" onClick={onToggle}
                      title={p.is_active ? "Deactivate" : "Activate"}>
                {p.is_active ? "Active" : "Off"}
              </button>
              <button className="d-iconbtn" onClick={() => setEditing(true)} title="Edit"><Pencil size={13} /></button>
              <button className="d-iconbtn danger" onClick={onRemove} title="Remove"><Trash2 size={13} /></button>
            </div>
          )}
        </>
      )}
    </li>
  );
}


/* ============================ Part B — results ============================ */
const SENT_COLOR = { positive: "var(--good)", neutral: "var(--txt-mid)", negative: "var(--bad)" };
const AXIS = { fill: "var(--txt-dim)", fontSize: 10 };

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
  // The run to show comes from the URL when a run is selected (…/runs/:runId), else the
  // latest. Deriving the target from `runs` means polling RESUMES on a direct load of a
  // prompt-set URL — the panel picks up the in-flight run and keeps polling it.
  const target = selectedRunId
    ? (runs || []).find((r) => r.id === selectedRunId) || null
    : (runs && runs.length ? runs[0] : null);
  const targetId = target ? target.id : null;
  const [run, setRun] = useState(target);
  const [summary, setSummary] = useState(null);
  const [trend, setTrend] = useState(null);
  const [results, setResults] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [filterEntity, setFilterEntity] = useState(null);   // leaderboard row -> filters prompts
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
      <div className="d-panel at-results" style={{ marginTop: 14 }}>
        <div className="d-panel-h">Results</div>
        <div className="d-dim" style={{ fontSize: 13, padding: "6px 2px" }}>
          No runs yet. Run this set to see whether AI assistants mention and cite your brand.
        </div>
      </div>
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
    <div className="d-panel at-results" style={{ marginTop: 14 }}>
      <div className="d-panel-h">Results <span className="sub">latest run</span></div>

      {error && <div className="at-err">{error}</div>}
      {analysing && (
        <div className="at-analysing"><RefreshCw size={14} className="spin" /> Analysing responses…</div>
      )}
      {failedExt && <div className="at-err">Analysis failed for this run. Try re-analysing it.</div>}

      {summary && !analysing && (
        <>
          {summary.excluded_extraction_failures > 0 && (
            <div className="at-note"><AlertTriangle size={13} /> {summary.excluded_extraction_failures} sample(s)
              excluded — extraction failed on them (not counted as “not mentioned”).</div>
          )}

          {/* headline */}
          <div className="at-headline">
            <div className="at-metric">
              <div className="at-metric-v">{pct(summary.mention_rate)}
                {delta != null && <DeltaBadge d={delta} />}
              </div>
              <div className="at-metric-l">Mention rate <span className="d-dim">· {summary.analyzed_count} samples</span></div>
            </div>
            <div className="at-metric">
              <div className="at-metric-v">{summary.citation_count}</div>
              <div className="at-metric-l">Brand citations</div>
              {summary.citation_count === 0 && (
                <div className="at-cite-why">{citationWhy(summary.citation_diagnosis)}</div>
              )}
            </div>
            {summary.average_position != null && (
              <div className="at-metric">
                <div className="at-metric-v">#{summary.average_position}</div>
                <div className="at-metric-l">Avg. position</div>
              </div>
            )}
          </div>

          {/* provider breakdown */}
          {summary.per_provider.length > 0 && (
            <div className="at-block">
              <div className="at-block-h">By provider</div>
              {summary.per_provider.map((p) => <Bar key={p.provider} label={p.provider} value={p.mention_rate} />)}
            </div>
          )}

          {/* competitor share of voice: brand vs competitors. A user's configured
              competitor set (tracked) is the more trustworthy signal, so it's shown apart
              from other entities the model surfaced. */}
          <div className="at-block">
            <div className="at-block-h">Share of voice</div>
            <Bar label="Your brand" value={summary.mention_rate} highlight />
            {summary.competitors.length === 0 ? (
              <div className="d-dim" style={{ fontSize: 12 }}>No competitors recommended in these answers.</div>
            ) : (
              <>
                {summary.competitors.some((c) => c.tracked) && (
                  <div className="at-comp-group">
                    <div className="at-comp-sub">Tracked competitors</div>
                    {summary.competitors.filter((c) => c.tracked).map((c) => (
                      <Bar key={c.name} label={c.name} value={c.mention_rate} />
                    ))}
                  </div>
                )}
                {summary.competitors.some((c) => !c.tracked) && (
                  <div className="at-comp-group">
                    <div className="at-comp-sub">Other entities detected</div>
                    {summary.competitors.filter((c) => !c.tracked).map((c) => (
                      <Bar key={c.name} label={c.name} value={c.mention_rate} />
                    ))}
                  </div>
                )}
              </>
            )}
          </div>

          {/* sentiment */}
          {Object.keys(summary.sentiment || {}).length > 0 && (
            <div className="at-block">
              <div className="at-block-h">Sentiment across mentions</div>
              <div className="at-sent">
                {["positive", "neutral", "negative"].filter((k) => summary.sentiment[k]).map((k) => (
                  <span key={k} className="at-sent-chip" style={{ color: SENT_COLOR[k] }}>
                    {k}: {summary.sentiment[k]}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* run-level competitive leaderboard — who is beating you, and where you're absent */}
          <Leaderboard summary={summary} filterEntity={filterEntity}
                       onSelect={(e) => setFilterEntity(
                         filterEntity && filterEntity.name === e.name ? null : e)} />

          {/* per-prompt table — gaps first, click to expand raw responses */}
          <div className="at-block">
            <div className="at-block-h">By prompt <span className="d-dim">· {gapCountLabel(summary.per_prompt)}</span></div>
            {filterEntity && (
              <div className="at-lb-filter">
                Showing prompts where <b>{filterEntity.name}</b> appears
                <button className="at-lb-clear" onClick={() => setFilterEntity(null)}>clear</button>
              </div>
            )}
            <div className="at-ptable">
              {(filterEntity
                ? summary.per_prompt.filter((r) => (filterEntity.prompt_ids || []).includes(r.prompt_id))
                : summary.per_prompt
              ).map((row) => (
                <PromptResultRow key={row.prompt_id} row={row}
                                 open={expanded === row.prompt_id}
                                 onToggle={() => setExpanded(expanded === row.prompt_id ? null : row.prompt_id)}
                                 results={results} />
              ))}
            </div>
          </div>

          {/* cited URLs */}
          {summary.cited_urls.length > 0 && (
            <div className="at-block">
              <div className="at-block-h">Cited brand URLs</div>
              {summary.cited_urls.map((u) => (
                <div key={u.url} className="at-url-row">
                  <span className="at-url">{u.url}</span>
                  <span className="at-url-n">{u.count}×</span>
                </div>
              ))}
            </div>
          )}

          {/* trend across recent runs, with a marker where a model changed */}
          {trend && trend.runs.length >= 2 && <TrendChart trend={trend} />}
        </>
      )}
    </div>
  );
}

/* "Who's winning your category" — the run-level competitive leaderboard. Rows are ranked by
   appearance rate; the tracked brand's row is visually distinct and always rendered. Clicking a
   row filters the prompt list below to the prompts where that entity appeared. head_to_head is
   the actionable column: prompts the entity holds where the brand is absent. */
export function Leaderboard({ summary, filterEntity, onSelect }) {
  const board = summary.leaderboard || [];
  const excluded = summary.leaderboard_excluded || 0;
  const minApp = summary.leaderboard_min_appearances;
  if (board.length === 0) {
    return (
      <div className="at-block">
        <div className="at-block-h">Who’s winning your category</div>
        <div className="d-dim" style={{ fontSize: 12 }}>
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
    <div className="at-block">
      <div className="at-block-h">Who’s winning your category
        <span className="d-dim"> · click a row to filter prompts below</span></div>
      <div className="at-lb">
        <div className="at-lb-head">
          <span className="at-lb-rank">#</span>
          <span className="at-lb-name">Entity</span>
          <span className="at-lb-delta" title="rank change vs previous run">Δ</span>
          <span className="at-lb-bar" />
          <span className="at-lb-rate">Rate</span>
          <span className="at-lb-cov" title="distinct prompts it appears in">Prompts</span>
          <span className="at-lb-h2h" title="prompts where it appears but you do not">Vs you</span>
        </div>
        {board.map((e) => {
          const active = filterEntity && filterEntity.name === e.name;
          return (
            <button key={e.name} type="button"
                    className={`at-lb-row${e.is_you ? " you" : ""}${active ? " active" : ""}`}
                    onClick={() => onSelect(e)}>
              <span className="at-lb-rank">#{e.rank}</span>
              <span className="at-lb-name">
                {e.name}{e.is_you && <span className="at-lb-tag">you</span>}
              </span>
              <RankDelta d={e.rank_delta} />
              <span className="at-lb-bar">
                <span className="at-lb-fill"
                      style={{ width: `${Math.max(2, (e.appearance_rate || 0) / maxRate * 100)}%` }} />
              </span>
              <span className="at-lb-rate">{pct(e.appearance_rate)}</span>
              <span className="at-lb-cov">{e.prompt_coverage}</span>
              <span className={`at-lb-h2h${e.head_to_head > 0 && !e.is_you ? " hot" : ""}`}>
                {e.head_to_head > 0 ? e.head_to_head : "—"}
              </span>
            </button>
          );
        })}
      </div>
      {excluded > 0 && (
        <div className="d-dim" style={{ fontSize: 11, marginTop: 6 }}>
          {excluded} more {excluded === 1 ? "entity" : "entities"} below the {minApp}-sample
          threshold not shown.
        </div>
      )}
    </div>
  );
}

/* Rank movement vs the previous run: up = toward #1 (good). Null when new or no prior run. */
function RankDelta({ d }) {
  if (d == null) return <span className="at-lb-delta flat">·</span>;
  if (d === 0) return <span className="at-lb-delta flat"><Minus size={10} /></span>;
  const up = d > 0;
  return (
    <span className={`at-lb-delta ${up ? "up" : "down"}`}>
      {up ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}{Math.abs(d)}
    </span>
  );
}

function DeltaBadge({ d }) {
  if (d === 0) return <span className="at-delta flat"><Minus size={12} /> 0</span>;
  const up = d > 0;
  return (
    <span className={`at-delta ${up ? "up" : "down"}`}>
      {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{Math.abs(d)}
    </span>
  );
}

function Bar({ label, value, highlight }) {
  const w = value == null ? 0 : Math.max(2, value);
  return (
    <div className="at-bar-row">
      <span className="at-bar-lbl" title={label}>{label}</span>
      <div className="at-bar-track">
        <div className="at-bar-fill" style={{ width: `${w}%`, background: highlight ? "var(--accent)" : "var(--txt-mid)" }} />
      </div>
      <span className="at-bar-v">{pct(value)}</span>
    </div>
  );
}

function PromptResultRow({ row, open, onToggle, results }) {
  const group = results && results.prompts ? results.prompts.find((p) => p.prompt_id === row.prompt_id) : null;
  return (
    <div className={`at-prow ${row.is_gap ? "gap" : ""}`}>
      <button className="at-prow-head" onClick={onToggle}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="at-prow-text">{row.text}</span>
        {row.irrelevant_hint && <span className="at-tag warn" title="No mentions and no category competitors">off-category?</span>}
        <span className="at-prow-rate" style={{ color: row.is_gap ? "var(--bad)" : "var(--txt)" }}>
          {pct(row.mention_rate)}
        </span>
        <span className="at-prow-n">{row.mentions}/{row.samples}</span>
      </button>
      {open && (
        <div className="at-prow-body">
          {/* CHANGE 3 priority: gap-to-action (what the user came for) beats the informational
              hint; the informational hint (CHANGE 4b) only shows when there's no gap. */}
          {row.gap
            ? <GapToAction gap={row.gap} />
            : row.irrelevant_hint && <InformationalHint />}
          {group && groupByProvider(group.results).map((g) => (
            <ProviderGroup key={g.provider} g={g}
                           ctx={{ hasGap: !!row.gap, informational: !!row.irrelevant_hint }} />
          ))}
        </div>
      )}
    </div>
  );
}

/* CHANGE 4b — a prompt where NO provider recommended any brand is informational, not a buying
   query, so it can't measure visibility. A HINT only: we never auto-disable or edit the prompt. */
function InformationalHint() {
  return (
    <div className="at-hint">
      <b>Looks informational.</b> No provider recommended any brand for this question, so it can’t
      measure your visibility — it reads as an informational query, not a buying one. Try a
      category buying query instead, e.g. <i>“best {"<category>"} tools for {"<audience>"}”</i>.
      This is just a hint — we won’t change your prompt.
    </div>
  );
}

/* Collapse a prompt's individual samples into ONE block per provider. Same provider run
   N times => one row, not N. When the samples DISAGREE we surface the fraction ("Named in
   2 of 3"), never a lossy yes/no. Providers where the brand was named are ordered first. */
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

    // Reference sample for recommended-entity ORDER: the sample where the brand ranked
    // highest (lowest position number); if never named, the first sample.
    let ref = rs[0];
    if (named.length) {
      ref = named.reduce((best, r) => {
        const bp = best.position == null ? Infinity : best.position;
        const rp = r.position == null ? Infinity : r.position;
        return rp < bp ? r : best;
      }, named[0]);
    }
    // Recommended entities deduped by name (case-insensitive), ref's order first.
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

    // Mention sentence from a sample where the brand WAS named.
    const namedWithCtx = named.find((r) => r.mention_context);
    // Citations (brand URLs) deduped across all samples, first-seen order.
    const seenU = new Set();
    const citations = [];
    for (const r of rs) for (const u of (r.brand_urls_cited || [])) {
      if (u && !seenU.has(u)) { seenU.add(u); citations.push(u); }
    }
    const namedWithSent = named.find((r) => r.sentiment);
    // A provider's samples all share a search mode; note if any sample lacked web search.
    const searchOff = rs.some((r) => r.search_enabled === false);

    // CHANGE 2 — do the samples AGREE? Same mention verdict AND same set of recommended
    // entities => "identical". Disagreement is real signal (unstable position) and stays visible.
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
  // Named-first, otherwise keep discovery order (stable).
  return groups.sort((a, b) => (b.anyNamed === a.anyNamed ? 0 : b.anyNamed ? 1 : -1));
}

const _LETTERS = "ABCDEFGH";

/* One provider's result for a prompt, aggregated across its samples. When the samples AGREE
   the block is labelled "N identical responses" and the expander shows one; when they DIFFER
   it is labelled "responses differed" and the expander lists each as "Response A/B". No sample
   index is ever shown. `ctx` carries prompt-level gap/informational state (CHANGE 3). */
export function ProviderGroup({ g, ctx }) {
  const [showRaw, setShowRaw] = useState(false);
  const shown = g.identical ? g.samples.slice(0, 1) : g.samples;   // identical => show one
  return (
    <div className="at-verdict">
      <div className="at-verdict-h">
        <b>{g.provider}</b>
        {g.allExtractionFailed
          ? <span className="at-tag warn">extraction failed</span>
          : g.namedCount > 0
            ? <span className="at-tag ok">Named in {g.namedCount} of {g.total}</span>
            : <span className="at-tag no">Not named in any of {g.total}</span>}
        {g.total > 1 && (
          g.identical
            ? <span className="at-tag" title="Both samples agreed">{g.total} identical responses</span>
            : <span className="at-tag warn" title="Samples disagreed — an unstable position">responses differed</span>
        )}
        {g.sentiment && <span className="at-tag" style={{ color: SENT_COLOR[g.sentiment] }}>{g.sentiment}</span>}
        {g.position != null && <span className="at-tag">#{g.position}</span>}
        {g.searchOff && <span className="at-tag warn" title="These samples ran without web search — citations may be unavailable">no web search</span>}
      </div>
      {g.namedCount > 0 && g.sentence && (
        <div className="at-verdict-body"><div className="at-quote">“{g.sentence}”</div></div>
      )}
      {g.citations.length > 0 && (
        <div className="at-verdict-body">
          {g.citations.map((u) => (
            <a key={u} className="at-cite" href={u} target="_blank" rel="noreferrer">{u}</a>
          ))}
        </div>
      )}
      {g.recommended.length > 0 ? (
        <div className="at-verdict-body">
          <div className="at-instead">
            <span className="d-dim">{g.namedCount > 0 ? "Also recommended:" : "Recommended instead:"}</span>{" "}
            {g.recommended.map((e, idx) => (
              <span key={idx} className="at-chip">{idx + 1}. {e.name}</span>
            ))}
          </div>
        </div>
      ) : (g.namedCount === 0 && !g.allExtractionFailed && (
        <div className="at-verdict-body"><EmptyRecommendation ctx={ctx} /></div>
      ))}
      <button className="at-rawtoggle" onClick={() => setShowRaw((s) => !s)}>
        {showRaw ? "Hide" : "View"} full response{!g.identical && g.total > 1 ? "s" : ""}
      </button>
      {showRaw && (
        <div className="at-verdict-samples">
          {shown.map((r, i) => (
            <SampleVerdict key={i} r={r} ctx={ctx}
                           label={g.total > 1 && !g.identical ? `Response ${_LETTERS[i]}` : null} />
          ))}
        </div>
      )}
    </div>
  );
}

/* CHANGE 3 — replaces the old useless "No specific brands were recommended for this query."
   line, in priority order: gap-to-action (shown above) > informational prompt > fallback. */
function EmptyRecommendation({ ctx }) {
  if (ctx?.hasGap) {
    return <div className="d-dim" style={{ fontSize: 12 }}>
      No brand recommended — see “Why not you — and what to do” above.</div>;
  }
  if (ctx?.informational) {
    return <div className="d-dim" style={{ fontSize: 12 }}>
      No provider recommended a brand — this reads as an informational question, not a buying
      query, so it can’t show brand visibility.</div>;
  }
  return <div className="d-dim" style={{ fontSize: 12 }}>No specific brands were recommended in this response.</div>;
}

/* One prompt×provider sample as a structured VERDICT — not a wall of prose. `label` is a
   human "Response A/B" (never a sample index). The full raw response is behind an expander. */
function SampleVerdict({ r, label, ctx }) {
  const [showRaw, setShowRaw] = useState(false);
  const mentioned = r.brand_mentioned === true;
  return (
    <div className="at-verdict">
      <div className="at-verdict-h">
        <b>{r.provider}</b>
        {label && <span className="d-dim"> · {label}</span>}
        {r.extraction_failed
          ? <span className="at-tag warn">extraction failed</span>
          : mentioned
            ? <span className="at-tag ok">MENTIONED</span>
            : <span className="at-tag no">NOT MENTIONED</span>}
        {!r.extraction_failed && mentioned && r.sentiment
          && <span className="at-tag" style={{ color: SENT_COLOR[r.sentiment] }}>{r.sentiment}</span>}
        {!r.extraction_failed && mentioned && r.position != null && <span className="at-tag">#{r.position}</span>}
      </div>
      {!r.extraction_failed && mentioned && (
        <div className="at-verdict-body">
          {r.mention_context && <div className="at-quote">“{r.mention_context}”</div>}
          {(r.brand_urls_cited || []).map((u) => (
            <a key={u} className="at-cite" href={u} target="_blank" rel="noreferrer">{u}</a>
          ))}
        </div>
      )}
      {!r.extraction_failed && !mentioned && (
        <div className="at-verdict-body">
          {(r.recommended_entities || []).length > 0 ? (
            <div className="at-instead">
              <span className="d-dim">Recommended instead:</span>{" "}
              {r.recommended_entities.map((e, idx) => (
                <span key={idx} className="at-chip">{idx + 1}. {e.name}</span>
              ))}
            </div>
          ) : (
            <EmptyRecommendation ctx={ctx} />
          )}
        </div>
      )}
      <button className="at-rawtoggle" onClick={() => setShowRaw((s) => !s)}>
        {showRaw ? "Hide" : "View"} full response
      </button>
      {showRaw && <RawText text={r.raw_response} error={r.error} highlight={r.mention_context} />}
    </div>
  );
}

/* Gap-to-action for a zero-mention prompt: why not you, and 2–4 grounded actions. */
function GapToAction({ gap }) {
  if (!gap) return null;
  return (
    <div className="at-gap">
      <div className="at-gap-h">Why not you — and what to do</div>
      {gap.why && <div className="at-gap-why">{gap.why}</div>}
      {gap.has_signal && (gap.actions || []).length > 0 ? (
        <ul className="at-gap-actions">{gap.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
      ) : (
        <div className="d-dim" style={{ fontSize: 12 }}>Not enough signal yet to give specific actions.</div>
      )}
    </div>
  );
}

function RawText({ text, error, highlight }) {
  if (error) return <div className="at-raw-t d-dim">No answer — {error}</div>;
  if (!text) return <div className="at-raw-t d-dim">(empty)</div>;
  if (highlight && text.includes(highlight)) {
    const [before, after] = text.split(highlight);
    return <div className="at-raw-t">{before}<mark className="at-mark">{highlight}</mark>{after}</div>;
  }
  return <div className="at-raw-t">{text}</div>;
}

function TrendChart({ trend }) {
  const data = trend.runs.map((r, i) => ({
    i: i + 1, rate: r.mention_rate,
    // A model OR web-search change makes this point not comparable to the prior one.
    changed: r.model_changed || r.search_changed,
    searchChanged: r.search_changed,
    date: fmtDate(r.created_at),
  }));
  const anySearchChange = data.some((d) => d.searchChanged);
  const renderDot = ({ cx, cy, payload }) => (
    payload.changed
      ? <rect x={cx - 4} y={cy - 4} width={8} height={8} fill="var(--warn)" stroke="var(--panel)" strokeWidth={1.5} />
      : <circle cx={cx} cy={cy} r={3} fill="var(--accent)" />
  );
  return (
    <div className="at-block">
      <div className="at-block-h">Trend <span className="d-dim">· ▪ marks a model{anySearchChange ? " or web-search" : ""} change</span></div>
      <div className="at-chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 10, bottom: 0, left: -22 }}>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
            <Tooltip />
            <Line type="monotone" dataKey="rate" stroke="var(--accent)" strokeWidth={2}
                  dot={renderDot} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
