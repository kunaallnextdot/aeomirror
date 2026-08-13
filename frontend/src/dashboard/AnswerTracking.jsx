/* AI Answer Tracking (Part A) — prompt management only.
   Create prompt sets, add/edit/remove/toggle prompts (max enforced in the UI),
   see a live cost estimate on the run button, trigger a run, and watch its status.
   There is NO results/analysis surface here — that is Part B. */
import React, { useCallback, useEffect, useState } from "react";
import {
  MessageSquare, Plus, Play, Trash2, Info, RefreshCw, Check, X, Pencil,
} from "lucide-react";
import {
  listPromptSets, createPromptSet, getPromptSet, deletePromptSet,
  addPrompt, updatePrompt, deletePrompt, estimatePromptSet, runPromptSet, ScanError,
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

export default function AnswerTracking() {
  const { hasPermission } = useAuth();
  const canRun = hasPermission("scan:run");
  const canDelete = hasPermission("scan:delete");

  const [sets, setSets] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
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
      setSelectedId(ps.id);
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
              <li key={s.id} className={s.id === selectedId ? "on" : ""}
                  onClick={() => setSelectedId(s.id)}>
                <span className="at-list-name">{s.name}</span>
                <span className="at-list-meta">
                  {s.active_prompt_count}/{s.prompt_count} active · {s.prompt_count}/{s.max_prompts} prompts
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {selectedId && (
        <PromptSetDetail key={selectedId} setId={selectedId} canRun={canRun} canDelete={canDelete}
                         onChanged={load} onDeleted={() => { setSelectedId(null); load(); }} />
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

function PromptSetDetail({ setId, canRun, canDelete, onChanged, onDeleted }) {
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
        <div className="at-add">
          <input className="d-input" placeholder={atMax ? "Prompt limit reached" : "Add a prompt…"}
                 value={text} maxLength={2000} disabled={atMax}
                 onChange={(e) => setText(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && add()} />
          <button className="d-btn" disabled={busy || atMax || !text.trim()} onClick={add}>
            <Plus size={15} /> Add
          </button>
        </div>
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
                {estCost != null && <> · <b>${estCost.toFixed(2)}</b></>}
                {estimate?.providers?.length ? <> across {estimate.providers.join(", ")}</> : null}
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
            <div key={r.id} className="at-run-row">
              <span className="at-run-status" style={{ color: RUN_STATUS_COLOR[r.status] }}>
                {RUN_STATUS_LABEL[r.status] || r.status}
              </span>
              <span className="at-run-meta">
                {r.total_calls} call{r.total_calls === 1 ? "" : "s"}
                {r.failed_calls ? ` · ${r.failed_calls} failed` : ""}
                {r.estimated_cost_usd != null ? ` · $${r.estimated_cost_usd.toFixed(2)}` : ""}
              </span>
              <span className="at-run-date">{fmtDate(r.completed_at || r.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
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
