/* AI Answer Tracking — MIGRATED to Aurora. Data flow, hooks, effects, handlers, polling,
   and the exported helpers/components (groupByProvider, gapCountLabel, Leaderboard,
   ProviderGroup) are byte-for-byte unchanged; only JSX + class names + chart theming changed.
   `.aurora-screen`-scoped. (Fixed a latent pre-existing bug: Trash2 was used but never
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
import { Shell, Cell, Button, Skeleton } from "./aurora.jsx";
import "./AnswerTracking.aurora.css";

const RUN_STATUS_LABEL = {
  pending: "Pending", running: "Running", completed: "Completed",
  failed: "Failed", partial: "Partial",
};
const RUN_STATUS_COLOR = {
  completed: "var(--au-mint-d)", partial: "var(--au-lemon-d)", failed: "var(--au-peach-d)",
  running: "var(--au-primary)", pending: "var(--au-muted)",
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

  const active = monitors && selectedMonitorId ? monitors.find((m) => m.id === selectedMonitorId) || null : null;
  const showSelector = monitors && monitors.length > 1;

  return (
    <div className="aurora-screen">
      <Shell>
        <Explainer />

        {error && !monitors ? (
          <Cell solid style={{ marginTop: 14 }}><div className="au-card-center" role="alert">
            <div className="au-ill au-ill-bad"><AlertTriangle size={26} /></div>
            <div className="au-card-s" style={{ marginBottom: 20 }}>{error}</div>
            <Button variant="accent" onClick={load}>Retry</Button>
          </div></Cell>
        ) : !monitors ? (
          <Cell solid style={{ marginTop: 14 }}><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} h={44} />)}</div></Cell>
        ) : monitors.length === 0 ? (
          <Cell solid style={{ marginTop: 14 }}>
            <div className="au-dim" style={{ padding: "10px 2px", fontSize: 13 }}>
              No sites yet. Add a site under <b>Monitoring</b> first — Answer Tracking prompts
              belong to a site.
            </div>
          </Cell>
        ) : (
          <>
            {showSelector && (
              <div className="au-at-siteselect">
                <label className="au-at-siteselect-h" htmlFor="at-site">Site</label>
                <select id="at-site" className="au-select" value={selectedMonitorId || ""}
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
                  <Cell solid style={{ marginTop: 14 }}>
                    <div className="au-dim" style={{ fontSize: 13, padding: "6px 2px" }}>
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
    <div className="au-at-explainer">
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

  if (error && !data) return <Cell solid style={{ marginTop: 14 }}><div className="au-at-err">{error}</div><Button variant="accent" onClick={load}>Retry</Button></Cell>;
  if (!data) return <Cell solid style={{ marginTop: 14 }}><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} h={40} />)}</div></Cell>;

  const estimate = data.estimate;
  const estCalls = estimate?.call_count ?? 0;
  const estCost = estimate?.estimated_cost_usd;
  const noProviders = estimate && (!estimate.providers || estimate.providers.length === 0);
  const existing = new Set((data.prompts || []).map((p) => p.text.trim().toLowerCase()));
  const suggestions = (data.suggestions || []).filter((s) => !existing.has(s.trim().toLowerCase()));

  return (
    <>
      <Cell solid className="au-at-detail">
        <div className="au-at-detail-h">
          <span>{data.site_name || data.brand_name || data.site_url}</span>
          <span className="au-at-detail-sub">{data.prompts.length}/{data.max_prompts} prompts</span>
        </div>

        {error && <div className="au-at-err">{error}</div>}

        <ul className="au-at-prompts">
          {data.prompts.length === 0 && (
            <li className="au-dim" style={{ fontSize: 13 }}>No prompts yet — add one below.</li>
          )}
          {data.prompts.map((p) => (
            <PromptRow key={p.id} p={p} canRun={canRun}
                       onToggle={() => toggle(p)} onRemove={() => removePrompt(p)}
                       onSaved={load} onError={setError} />
          ))}
        </ul>

        {canRun && (
          <>
            <div className="au-at-add">
              <input className="au-input" placeholder={atMax ? "Prompt limit reached" : "Add a prompt…"}
                     value={text} maxLength={2000} disabled={atMax}
                     onChange={(e) => setText(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && add()} />
              <Button variant="accent" disabled={busy || atMax || !text.trim()} onClick={() => add()}>
                <Plus size={15} /> Add
              </Button>
            </div>
            {!atMax && suggestions.length > 0 && (
              <div className="au-at-suggest">
                <span className="au-at-suggest-h">Try one:</span>
                {suggestions.map((s) => (
                  <button key={s} className="au-at-suggest-chip" disabled={busy} onClick={() => add(s)}>
                    <Plus size={12} /> {s}
                  </button>
                ))}
              </div>
            )}
            <div className="au-at-guide">
              Track <b>category queries</b> a buyer would ask — where you’d want to appear:
              <span className="au-at-guide-good">“best {"<category>"} tools for {"<audience>"}”</span>
              <span className="au-at-guide-good">“{"<category>"} software compared”</span>
              Avoid general-knowledge questions unrelated to your category
              <span className="au-at-guide-bad">“what is agentic AI?”</span>
              — those show 0% with noisy, irrelevant competitors.
            </div>
          </>
        )}
        {atMax && (
          <div className="au-at-note">
            This site has the maximum of {data.max_prompts} prompts. Remove one to add another.
          </div>
        )}

        {canRun && (
          <div className="au-at-runbar">
            <Button variant="primary" loading={busy} disabled={busy || noProviders || estCalls === 0} onClick={run}>
              {!busy && <Play size={15} />} Run now
            </Button>
            <div className="au-at-est">
              {noProviders ? (
                <span className="au-at-est-warn">No providers configured — a run would make 0 calls.</span>
              ) : (
                <>Estimated this run: <b>{estCalls}</b> provider call{estCalls === 1 ? "" : "s"}
                  {estimate?.providers?.length ? <> across {estimate.providers.join(", ")}</> : null}
                  {estCost != null && (
                    <div className="au-at-est-cost">
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
        {runMsg && <div className={runMsg.ok ? "au-at-run-ok" : "au-at-run-err"}>{runMsg.text}</div>}

        {data.runs?.length > 0 && (
          <div className="au-at-runs">
            <div className="au-at-runs-h">Recent runs</div>
            {data.runs.map((r) => (
              <Link key={r.id} className="au-at-run-row"
                    to={`/app/answer-tracking/${encodeURIComponent(monitorId)}/runs/${encodeURIComponent(r.id)}`}>
                <span className="au-at-run-status" style={{ color: RUN_STATUS_COLOR[r.status] }}>
                  {RUN_STATUS_LABEL[r.status] || r.status}
                </span>
                <span className="au-at-run-meta">
                  {r.total_calls} call{r.total_calls === 1 ? "" : "s"}
                  {r.failed_calls ? ` · ${r.failed_calls} failed` : ""}
                  {r.estimated_cost_usd != null ? ` · $${r.estimated_cost_usd.toFixed(2)}` : ""}
                </span>
                <span className="au-at-run-date">{fmtDate(r.completed_at || r.created_at)}</span>
              </Link>
            ))}
          </div>
        )}
      </Cell>
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
    <li className={`au-at-prompt ${p.is_active ? "" : "off"}`}>
      {editing ? (
        <>
          <input className="au-input" value={val} maxLength={2000} autoFocus
                 onChange={(e) => setVal(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && save()} />
          <button className="au-iconbtn" onClick={save} title="Save"><Check size={14} /></button>
          <button className="au-iconbtn" onClick={() => { setEditing(false); setVal(p.text); }} title="Cancel">
            <X size={14} />
          </button>
        </>
      ) : (
        <>
          <span className="au-at-prompt-text">{p.text}</span>
          {canRun && (
            <div className="au-at-prompt-actions">
              <button className="au-iconbtn" onClick={onToggle}
                      title={p.is_active ? "Deactivate" : "Activate"}>
                {p.is_active ? "Active" : "Off"}
              </button>
              <button className="au-iconbtn" onClick={() => setEditing(true)} title="Edit"><Pencil size={13} /></button>
              <button className="au-iconbtn au-danger" onClick={onRemove} title="Remove"><Trash2 size={13} /></button>
            </div>
          )}
        </>
      )}
    </li>
  );
}


/* ============================ Part B — results ============================ */
const SENT_COLOR = { positive: "var(--au-mint-d)", neutral: "var(--au-muted)", negative: "var(--au-peach-d)" };
const AXIS = { fill: "var(--au-muted)", fontSize: 10, fontFamily: "'DM Mono', monospace" };

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
      <Cell solid className="au-at-results">
        <div className="au-panel-h">Results</div>
        <div className="au-dim" style={{ fontSize: 13, padding: "6px 2px" }}>
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
    <Cell solid className="au-at-results">
      <div className="au-panel-h">Results <span className="au-sub">latest run</span></div>

      {error && <div className="au-at-err">{error}</div>}
      {analysing && (
        <div className="au-at-analysing"><RefreshCw size={14} className="spin" /> Analysing responses…</div>
      )}
      {failedExt && <div className="au-at-err">Analysis failed for this run. Try re-analysing it.</div>}

      {summary && !analysing && (
        <>
          {summary.excluded_extraction_failures > 0 && (
            <div className="au-at-note"><AlertTriangle size={13} /> {summary.excluded_extraction_failures} sample(s)
              excluded — extraction failed on them (not counted as “not mentioned”).</div>
          )}

          {/* headline */}
          <div className="au-at-headline">
            <div>
              <div className="au-at-metric-v">{pct(summary.mention_rate)}
                {delta != null && <DeltaBadge d={delta} />}
              </div>
              <div className="au-at-metric-l">Mention rate <span className="au-dim">· {summary.analyzed_count} samples</span></div>
            </div>
            <div>
              <div className="au-at-metric-v">{summary.citation_count}</div>
              <div className="au-at-metric-l">Brand citations</div>
              {summary.citation_count === 0 && (
                <div className="au-at-cite-why">{citationWhy(summary.citation_diagnosis)}</div>
              )}
            </div>
            {summary.average_position != null && (
              <div>
                <div className="au-at-metric-v">#{summary.average_position}</div>
                <div className="au-at-metric-l">Avg. position</div>
              </div>
            )}
          </div>

          {/* provider breakdown */}
          {summary.per_provider.length > 0 && (
            <div className="au-at-block">
              <div className="au-at-block-h">By provider</div>
              {summary.per_provider.map((p) => <Bar key={p.provider} label={p.provider} value={p.mention_rate} />)}
            </div>
          )}

          {/* competitor share of voice */}
          <div className="au-at-block">
            <div className="au-at-block-h">Share of voice</div>
            <Bar label="Your brand" value={summary.mention_rate} highlight />
            {summary.competitors.length === 0 ? (
              <div className="au-dim" style={{ fontSize: 12 }}>No competitors recommended in these answers.</div>
            ) : (
              <>
                {summary.competitors.some((c) => c.tracked) && (
                  <div className="au-at-comp-group">
                    <div className="au-at-comp-sub">Tracked competitors</div>
                    {summary.competitors.filter((c) => c.tracked).map((c) => (
                      <Bar key={c.name} label={c.name} value={c.mention_rate} />
                    ))}
                  </div>
                )}
                {summary.competitors.some((c) => !c.tracked) && (
                  <div className="au-at-comp-group">
                    <div className="au-at-comp-sub">Other entities detected</div>
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
            <div className="au-at-block">
              <div className="au-at-block-h">Sentiment across mentions</div>
              <div className="au-at-sent">
                {["positive", "neutral", "negative"].filter((k) => summary.sentiment[k]).map((k) => (
                  <span key={k} className="au-at-sent-chip" style={{ color: SENT_COLOR[k] }}>
                    {k}: {summary.sentiment[k]}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* run-level competitive leaderboard */}
          <Leaderboard summary={summary} filterEntity={filterEntity}
                       onSelect={(e) => setFilterEntity(
                         filterEntity && filterEntity.name === e.name ? null : e)} />

          {/* per-prompt table */}
          <div className="au-at-block">
            <div className="au-at-block-h">By prompt <span className="au-dim">· {gapCountLabel(summary.per_prompt)}</span></div>
            {filterEntity && (
              <div className="au-at-lb-filter">
                Showing prompts where <b>{filterEntity.name}</b> appears
                <button className="au-at-lb-clear" onClick={() => setFilterEntity(null)}>clear</button>
              </div>
            )}
            <div className="au-at-ptable">
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
            <div className="au-at-block">
              <div className="au-at-block-h">Cited brand URLs</div>
              {summary.cited_urls.map((u) => (
                <div key={u.url} className="au-at-url-row">
                  <span className="au-at-url">{u.url}</span>
                  <span className="au-at-url-n">{u.count}×</span>
                </div>
              ))}
            </div>
          )}

          {/* trend across recent runs */}
          {trend && trend.runs.length >= 2 && <TrendChart trend={trend} />}
        </>
      )}
    </Cell>
  );
}

export function Leaderboard({ summary, filterEntity, onSelect }) {
  const board = summary.leaderboard || [];
  const excluded = summary.leaderboard_excluded || 0;
  const minApp = summary.leaderboard_min_appearances;
  if (board.length === 0) {
    return (
      <div className="au-at-block">
        <div className="au-at-block-h">Who’s winning your category</div>
        <div className="au-dim" style={{ fontSize: 12 }}>
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
    <div className="au-at-block">
      <div className="au-at-block-h">Who’s winning your category
        <span className="au-dim"> · click a row to filter prompts below</span></div>
      <div className="au-at-lb">
        <div className="au-at-lb-head">
          <span className="au-at-lb-rank">#</span>
          <span className="au-at-lb-name">Entity</span>
          <span className="au-at-lb-delta" title="rank change vs previous run">Δ</span>
          <span className="au-at-lb-bar" />
          <span className="au-at-lb-rate">Rate</span>
          <span className="au-at-lb-cov" title="distinct prompts it appears in">Prompts</span>
          <span className="au-at-lb-h2h" title="prompts where it appears but you do not">Vs you</span>
        </div>
        {board.map((e) => {
          const active = filterEntity && filterEntity.name === e.name;
          return (
            <button key={e.name} type="button"
                    className={`au-at-lb-row${e.is_you ? " you" : ""}${active ? " active" : ""}`}
                    onClick={() => onSelect(e)}>
              <span className="au-at-lb-rank">#{e.rank}</span>
              <span className="au-at-lb-name">
                {e.name}{e.is_you && <span className="au-at-lb-tag">you</span>}
              </span>
              <RankDelta d={e.rank_delta} />
              <span className="au-at-lb-bar">
                <span className="au-at-lb-fill"
                      style={{ width: `${Math.max(2, (e.appearance_rate || 0) / maxRate * 100)}%` }} />
              </span>
              <span className="au-at-lb-rate">{pct(e.appearance_rate)}</span>
              <span className="au-at-lb-cov">{e.prompt_coverage}</span>
              <span className={`au-at-lb-h2h${e.head_to_head > 0 && !e.is_you ? " hot" : ""}`}>
                {e.head_to_head > 0 ? e.head_to_head : "—"}
              </span>
            </button>
          );
        })}
      </div>
      {excluded > 0 && (
        <div className="au-dim" style={{ fontSize: 11, marginTop: 6 }}>
          {excluded} more {excluded === 1 ? "entity" : "entities"} below the {minApp}-sample
          threshold not shown.
        </div>
      )}
    </div>
  );
}

/* Rank movement vs the previous run: up = toward #1 (good). Null when new or no prior run. */
function RankDelta({ d }) {
  if (d == null) return <span className="au-at-lb-delta flat">·</span>;
  if (d === 0) return <span className="au-at-lb-delta flat"><Minus size={10} /></span>;
  const up = d > 0;
  return (
    <span className={`au-at-lb-delta ${up ? "up" : "down"}`}>
      {up ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}{Math.abs(d)}
    </span>
  );
}

function DeltaBadge({ d }) {
  if (d === 0) return <span className="au-at-delta flat"><Minus size={12} /> 0</span>;
  const up = d > 0;
  return (
    <span className={`au-at-delta ${up ? "up" : "down"}`}>
      {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{Math.abs(d)}
    </span>
  );
}

function Bar({ label, value, highlight }) {
  const w = value == null ? 0 : Math.max(2, value);
  return (
    <div className="au-at-bar-row">
      <span className="au-at-bar-lbl" title={label}>{label}</span>
      <div className="au-at-bar-track">
        <div className="au-at-bar-fill" style={{ width: `${w}%`, background: highlight ? "var(--au-primary)" : "var(--au-muted)" }} />
      </div>
      <span className="au-at-bar-v">{pct(value)}</span>
    </div>
  );
}

function PromptResultRow({ row, open, onToggle, results }) {
  const group = results && results.prompts ? results.prompts.find((p) => p.prompt_id === row.prompt_id) : null;
  return (
    <div className={`au-at-prow ${row.is_gap ? "gap" : ""}`}>
      <button className="au-at-prow-head" onClick={onToggle}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="au-at-prow-text">{row.text}</span>
        {row.irrelevant_hint && <span className="au-at-tag warn" title="No mentions and no category competitors">off-category?</span>}
        <span className="au-at-prow-rate" style={{ color: row.is_gap ? "var(--au-peach-d)" : "var(--au-ink)" }}>
          {pct(row.mention_rate)}
        </span>
        <span className="au-at-prow-n">{row.mentions}/{row.samples}</span>
      </button>
      {open && (
        <div className="au-at-prow-body">
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

/* CHANGE 4b — informational-prompt hint (never auto-disables/edits the prompt). */
function InformationalHint() {
  return (
    <div className="au-at-hint">
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
      if (u && !seenU.has(u)) { seenU.add(u); citations.push(u); }
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
    <div className="au-at-verdict">
      <div className="au-at-verdict-h">
        <b>{g.provider}</b>
        {g.allExtractionFailed
          ? <span className="au-at-tag warn">extraction failed</span>
          : g.namedCount > 0
            ? <span className="au-at-tag ok">Named in {g.namedCount} of {g.total}</span>
            : <span className="au-at-tag no">Not named in any of {g.total}</span>}
        {g.total > 1 && (
          g.identical
            ? <span className="au-at-tag" title="Both samples agreed">{g.total} identical responses</span>
            : <span className="au-at-tag warn" title="Samples disagreed — an unstable position">responses differed</span>
        )}
        {g.sentiment && <span className="au-at-tag" style={{ color: SENT_COLOR[g.sentiment] }}>{g.sentiment}</span>}
        {g.position != null && <span className="au-at-tag">#{g.position}</span>}
        {g.searchOff && <span className="au-at-tag warn" title="These samples ran without web search — citations may be unavailable">no web search</span>}
      </div>
      {g.namedCount > 0 && g.sentence && (
        <div className="au-at-verdict-body"><div className="au-at-quote">“{g.sentence}”</div></div>
      )}
      {g.citations.length > 0 && (
        <div className="au-at-verdict-body">
          {g.citations.map((u) => (
            <a key={u} className="au-at-cite" href={u} target="_blank" rel="noreferrer">{u}</a>
          ))}
        </div>
      )}
      {g.recommended.length > 0 ? (
        <div className="au-at-verdict-body">
          <div className="au-at-instead">
            <span className="au-dim">{g.namedCount > 0 ? "Also recommended:" : "Recommended instead:"}</span>{" "}
            {g.recommended.map((e, idx) => (
              <span key={idx} className="au-at-chip">{idx + 1}. {e.name}</span>
            ))}
          </div>
        </div>
      ) : (g.namedCount === 0 && !g.allExtractionFailed && (
        <div className="au-at-verdict-body"><EmptyRecommendation ctx={ctx} /></div>
      ))}
      <button className="au-at-rawtoggle" onClick={() => setShowRaw((s) => !s)}>
        {showRaw ? "Hide" : "View"} full response{!g.identical && g.total > 1 ? "s" : ""}
      </button>
      {showRaw && (
        <div className="au-at-verdict-samples">
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
    return <div className="au-dim" style={{ fontSize: 12 }}>
      No brand recommended — see “Why not you — and what to do” above.</div>;
  }
  if (ctx?.informational) {
    return <div className="au-dim" style={{ fontSize: 12 }}>
      No provider recommended a brand — this reads as an informational question, not a buying
      query, so it can’t show brand visibility.</div>;
  }
  return <div className="au-dim" style={{ fontSize: 12 }}>No specific brands were recommended in this response.</div>;
}

/* One prompt×provider sample as a structured VERDICT. `label` is a human "Response A/B". */
function SampleVerdict({ r, label, ctx }) {
  const [showRaw, setShowRaw] = useState(false);
  const mentioned = r.brand_mentioned === true;
  return (
    <div className="au-at-verdict">
      <div className="au-at-verdict-h">
        <b>{r.provider}</b>
        {label && <span className="au-dim"> · {label}</span>}
        {r.extraction_failed
          ? <span className="au-at-tag warn">extraction failed</span>
          : mentioned
            ? <span className="au-at-tag ok">MENTIONED</span>
            : <span className="au-at-tag no">NOT MENTIONED</span>}
        {!r.extraction_failed && mentioned && r.sentiment
          && <span className="au-at-tag" style={{ color: SENT_COLOR[r.sentiment] }}>{r.sentiment}</span>}
        {!r.extraction_failed && mentioned && r.position != null && <span className="au-at-tag">#{r.position}</span>}
      </div>
      {!r.extraction_failed && mentioned && (
        <div className="au-at-verdict-body">
          {r.mention_context && <div className="au-at-quote">“{r.mention_context}”</div>}
          {(r.brand_urls_cited || []).map((u) => (
            <a key={u} className="au-at-cite" href={u} target="_blank" rel="noreferrer">{u}</a>
          ))}
        </div>
      )}
      {!r.extraction_failed && !mentioned && (
        <div className="au-at-verdict-body">
          {(r.recommended_entities || []).length > 0 ? (
            <div className="au-at-instead">
              <span className="au-dim">Recommended instead:</span>{" "}
              {r.recommended_entities.map((e, idx) => (
                <span key={idx} className="au-at-chip">{idx + 1}. {e.name}</span>
              ))}
            </div>
          ) : (
            <EmptyRecommendation ctx={ctx} />
          )}
        </div>
      )}
      <button className="au-at-rawtoggle" onClick={() => setShowRaw((s) => !s)}>
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
    <div className="au-at-gap">
      <div className="au-at-gap-h">Why not you — and what to do</div>
      {gap.why && <div className="au-at-gap-why">{gap.why}</div>}
      {gap.has_signal && (gap.actions || []).length > 0 ? (
        <ul className="au-at-gap-actions">{gap.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
      ) : (
        <div className="au-dim" style={{ fontSize: 12 }}>Not enough signal yet to give specific actions.</div>
      )}
    </div>
  );
}

function RawText({ text, error, highlight }) {
  if (error) return <div className="au-at-raw-t au-dim">No answer — {error}</div>;
  if (!text) return <div className="au-at-raw-t au-dim">(empty)</div>;
  if (highlight && text.includes(highlight)) {
    const [before, after] = text.split(highlight);
    return <div className="au-at-raw-t">{before}<mark className="au-at-mark">{highlight}</mark>{after}</div>;
  }
  return <div className="au-at-raw-t">{text}</div>;
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
      ? <rect x={cx - 4} y={cy - 4} width={8} height={8} fill="var(--au-lemon-d)" stroke="var(--au-solid)" strokeWidth={1.5} />
      : <circle cx={cx} cy={cy} r={3} fill="var(--au-primary)" />
  );
  return (
    <div className="au-at-block">
      <div className="au-at-block-h">Trend <span className="au-dim">· ▪ marks a model{anySearchChange ? " or web-search" : ""} change</span></div>
      <div className="au-at-chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 10, bottom: 0, left: -22 }}>
            <CartesianGrid stroke="var(--au-line)" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ background: "var(--au-solid)", border: "1px solid var(--au-line)", borderRadius: 12, fontSize: 12, boxShadow: "var(--au-sh-s)" }} />
            <Line type="monotone" dataKey="rate" stroke="var(--au-primary)" strokeWidth={2}
                  dot={renderDot} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
