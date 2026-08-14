/* AI Answer Tracking — prompt management (Part A) + results (Part B).
   Create prompt sets, manage prompts (max enforced in the UI), run with a live cost
   estimate, and below the prompt list see the analysis: mention rate + delta, provider
   and competitor share of voice, per-prompt gaps, cited URLs, expandable raw responses
   with the mention highlighted, and a run-over-run trend that marks model changes. */
import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  MessageSquare, Plus, Play, Trash2, Info, RefreshCw, Check, X, Pencil,
  ArrowUpRight, ArrowDownRight, Minus, AlertTriangle, ChevronRight, ChevronDown,
} from "lucide-react";
import {
  Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  listPromptSets, createPromptSet, getPromptSet, deletePromptSet,
  addPrompt, updatePrompt, deletePrompt, estimatePromptSet, runPromptSet,
  getPromptRun, getPromptRunSummary, getPromptSetTrend, getPromptRunResults, ScanError,
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

export default function AnswerTracking({ selectedSetId = null, selectedRunId = null }) {
  const { hasPermission } = useAuth();
  const navigate = useNavigate();
  const canRun = hasPermission("scan:run");
  const canDelete = hasPermission("scan:delete");

  // The selected set + run are the URL's job (single source of truth), not local state.
  const selectedId = selectedSetId;
  const selectSet = useCallback((id) => navigate(`/app/answer-tracking/${encodeURIComponent(id)}`), [navigate]);

  const [sets, setSets] = useState(null);
  const [error, setError] = useState(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await listPromptSets();
      setSets(res.prompt_sets || []);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load prompt sets.");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      const ps = await createPromptSet({ name });
      setNewName("");
      await load();
      selectSet(ps.id);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not create the prompt set.");
    } finally { setCreating(false); }
  };

  if (error && !sets) return <ErrorState message={error} onRetry={load} />;
  if (!sets) return <TableSkeleton rows={4} />;

  return (
    <div className="at-wrap">
      <Explainer />

      <div className="d-panel" style={{ marginTop: 14 }}>
        <div className="d-panel-h">Prompt sets</div>
        {canRun && (
          <div className="at-create">
            <input className="d-input" placeholder="New prompt set name…" value={newName}
                   maxLength={120} onChange={(e) => setNewName(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && create()} />
            <button className="d-btn" disabled={creating || !newName.trim()} onClick={create}>
              <Plus size={15} /> Create
            </button>
          </div>
        )}
        {sets.length === 0 ? (
          <div className="d-dim" style={{ padding: "10px 2px", fontSize: 13 }}>
            No prompt sets yet. Create one to start tracking how AI assistants answer.
          </div>
        ) : (
          <ul className="at-list">
            {sets.map((s) => (
              <li key={s.id} className={s.id === selectedId ? "on" : ""}>
                {/* whole row is the link target — cmd/ctrl/middle-click opens a new tab */}
                <Link className="at-list-link" to={`/app/answer-tracking/${encodeURIComponent(s.id)}`}>
                  <span className="at-list-name">{s.name}</span>
                  <span className="at-list-meta">
                    {s.active_prompt_count}/{s.prompt_count} active · {s.prompt_count}/{s.max_prompts} prompts
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      {selectedId && (
        <PromptSetDetail key={selectedId} setId={selectedId} selectedRunId={selectedRunId} canRun={canRun} canDelete={canDelete}
                         onChanged={load} onDeleted={() => { navigate("/app/answer-tracking"); load(); }} />
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

function PromptSetDetail({ setId, selectedRunId, canRun, canDelete, onChanged, onDeleted }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [runMsg, setRunMsg] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [d, est] = await Promise.all([
        getPromptSet(setId),
        estimatePromptSet(setId).catch(() => null),
      ]);
      setData(d);
      setEstimate(est);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load this prompt set.");
    }
  }, [setId]);

  useEffect(() => { load(); }, [load]);

  const atMax = data && data.prompt_count >= data.max_prompts;

  const add = async () => {
    const t = text.trim();
    if (!t) return;
    setBusy(true); setError(null);
    try {
      await addPrompt(setId, t);
      setText("");
      await load(); onChanged?.();
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not add the prompt.");
    } finally { setBusy(false); }
  };

  const toggle = async (p) => {
    try { await updatePrompt(p.id, { is_active: !p.is_active }); await load(); onChanged?.(); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not update the prompt."); }
  };

  const removePrompt = async (p) => {
    try { await deletePrompt(p.id); await load(); onChanged?.(); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not remove the prompt."); }
  };

  const removeSet = async () => {
    if (!window.confirm("Delete this prompt set and all its prompts and runs?")) return;
    try { await deletePromptSet(setId); onDeleted?.(); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not delete the set."); }
  };

  const run = async () => {
    setBusy(true); setRunMsg(null); setError(null);
    try {
      const res = await runPromptSet(setId);
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

  const estCalls = estimate?.call_count ?? 0;
  const estCost = estimate?.estimated_cost_usd;
  const noProviders = estimate && (!estimate.providers || estimate.providers.length === 0);

  return (
    <>
    <div className="d-panel at-detail" style={{ marginTop: 14 }}>
      <div className="d-panel-h at-detail-h">
        <span>{data.name}</span>
        {canDelete && (
          <button className="d-iconbtn danger" onClick={removeSet} title="Delete set">
            <Trash2 size={14} />
          </button>
        )}
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
                     onSaved={() => { load(); onChanged?.(); }} onError={setError} />
        ))}
      </ul>

      {canRun && (
        <>
          <div className="at-add">
            <input className="d-input" placeholder={atMax ? "Prompt limit reached" : "Add a prompt…"}
                   value={text} maxLength={2000} disabled={atMax}
                   onChange={(e) => setText(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && add()} />
            <button className="d-btn" disabled={busy || atMax || !text.trim()} onClick={add}>
              <Plus size={15} /> Add
            </button>
          </div>
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
          This set has the maximum of {data.max_prompts} prompts. Remove one to add another.
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
                  to={`/app/answer-tracking/${encodeURIComponent(setId)}/runs/${encodeURIComponent(r.id)}`}>
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
    <ResultsPanel setId={setId} runs={data.runs} brand={data} selectedRunId={selectedRunId} />
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

function ResultsPanel({ setId, runs, selectedRunId }) {
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
            getPromptSetTrend(setId, { n: 10 }).catch(() => null),
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
  }, [targetId, setId]);

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

          {/* per-prompt table — gaps first, click to expand raw responses */}
          <div className="at-block">
            <div className="at-block-h">By prompt <span className="d-dim">· 0% rows are your gaps</span></div>
            <div className="at-ptable">
              {summary.per_prompt.map((row) => (
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
          {row.irrelevant_hint && (
            <div className="at-hint">
              This prompt returned no mentions and no category competitors — it may not be a
              category query for your brand. We won’t change it; that’s your call.
            </div>
          )}
          {row.gap && <GapToAction gap={row.gap} />}
          {group && group.results.map((r, i) => <SampleVerdict key={i} r={r} />)}
        </div>
      )}
    </div>
  );
}

/* One prompt×provider sample as a structured VERDICT — not a wall of prose. The full raw
   response is available behind an explicit expander, closed by default. */
function SampleVerdict({ r }) {
  const [showRaw, setShowRaw] = useState(false);
  const mentioned = r.brand_mentioned === true;
  return (
    <div className="at-verdict">
      <div className="at-verdict-h">
        <b>{r.provider}</b>
        <span className="d-dim"> · sample {r.run_index}{r.is_adaptive_run ? " (adaptive)" : ""}</span>
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
            <div className="d-dim" style={{ fontSize: 12 }}>No specific brands were recommended for this query.</div>
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
    i: i + 1, rate: r.mention_rate, changed: r.model_changed,
    date: fmtDate(r.created_at),
  }));
  const renderDot = ({ cx, cy, payload }) => (
    payload.changed
      ? <rect x={cx - 4} y={cy - 4} width={8} height={8} fill="var(--warn)" stroke="var(--panel)" strokeWidth={1.5} />
      : <circle cx={cx} cy={cy} r={3} fill="var(--accent)" />
  );
  return (
    <div className="at-block">
      <div className="at-block-h">Trend <span className="d-dim">· ▪ marks a model change</span></div>
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
