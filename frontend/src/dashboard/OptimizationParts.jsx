/* Shared pieces of URL-wise optimization: status labels, the per-action controls
   (start / mark fixed / re-check / ignore / reopen / note), the activity timeline and
   the before-and-after performance table. Used by the Optimization tab and by Page
   Intelligence's drawer. A user marking something fixed only queues a check. */
import React, { useState } from "react";
import { fmtDate } from "./ui.jsx";
import { Button } from "../shared/spectrum.jsx";

export const ACTION_STATUS = {
  new: "New", needs_attention: "Needs attention", in_progress: "In progress",
  ready_for_verification: "Ready for verification", verified: "Verified", partially_fixed: "Partially fixed",
  not_fixed: "Not fixed", reopened: "Reopened", ignored: "Ignored",
};
export const RESULT = { fixed: "Fixed", partially_fixed: "Partially fixed", not_fixed: "Not fixed", unable_to_verify: "Couldn't verify" };
const METHOD = { recrawl: "page re-check", crawl: "site crawl" };
const METRIC = {
  sessions: "All sessions", ai_sessions: "AI-assistant sessions", organic_sessions: "Organic search sessions",
  key_events: "Key events (GA4)", revenue: "Revenue", verified_conversions: "Verified conversions", value: "Conversion value",
  clicks: "Google clicks", impressions: "Google impressions", ctr_pct: "Google CTR (%)", position: "Average position",
  answers: "Tracked answers", answers_citing_page: "Answers citing this page",
};

export function StatusPill({ status }) {
  return <span className={`sp-opt-st st-${status}`}>{ACTION_STATUS[status] || status}</span>;
}

export function verifyText(a) {
  if (a.verification_method === "performance") return "Tracked by before-and-after performance; a page check can't confirm it.";
  if (a.verification_method === "site_crawl") return "Checked at the next full site crawl.";
  if (a.recheck_requested || (a.next_check_at && a.status === "ready_for_verification")) return "A page re-check is queued.";
  if (a.next_check_at) return `Next page re-check ${fmtDate(a.next_check_at)}.`;
  return "Checked again at the next site crawl.";
}

/* The controls for one recommendation. `onChange(op, extra)` resolves when the server
   has applied the change (it returns the refreshed record). */
export function ActionBar({ a, canRun, onChange }) {
  const [mode, setMode] = useState(null);   // "ignore" | "note"
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const run = async (op, extra) => {
    setBusy(op); setErr(null);
    try { await onChange(op, extra); setMode(null); setText(""); }
    catch (e) { setErr(e?.message || "Could not update this action."); }
    finally { setBusy(null); }
  };
  const s = a.status;
  const canStart = ["new", "needs_attention", "not_fixed", "partially_fixed", "reopened", "ready_for_verification"].includes(s);
  const canReady = ["new", "needs_attention", "in_progress", "not_fixed", "partially_fixed", "reopened"].includes(s);
  const canRecheck = s !== "ignored" && a.verification_method === "page_check" && !a.next_check_at;
  return (
    <div className="sp-opt-bar">
      <div className="sp-opt-state">
        <StatusPill status={s} />
        {a.last_result && <span className="sp-si-muted"> Last check: {RESULT[a.last_result]} ({METHOD[a.last_check_method] || "check"}, {fmtDate(a.last_checked_at)})</span>}
        {a.check_error && <div className="sp-si-muted">{a.check_error}</div>}
        {s === "ignored" && a.ignored_reason && <div className="sp-si-muted">Ignored: {a.ignored_reason}</div>}
        <div className="sp-si-muted">{verifyText(a)}</div>
      </div>
      {canRun && (
        <div className="sp-opt-btns">
          {canStart && s !== "in_progress" && <Button variant="ghost" loading={busy === "start"} onClick={() => run("start")}>Start</Button>}
          {canReady && <Button variant="ghost" loading={busy === "ready"} onClick={() => run("ready")}>Mark fixed</Button>}
          {canRecheck && <Button variant="ghost" loading={busy === "recheck"} onClick={() => run("recheck")}>Re-check now</Button>}
          {a.next_check_at && s !== "ignored" && <Button variant="ghost" loading={busy === "cancel_recheck"} onClick={() => run("cancel_recheck")}>Cancel re-check</Button>}
          {(s === "verified" || s === "ignored") && <Button variant="ghost" loading={busy === "reopen"} onClick={() => run("reopen")}>Reopen</Button>}
          {s !== "ignored" && <Button variant="ghost" onClick={() => setMode(mode === "ignore" ? null : "ignore")}>Ignore</Button>}
          <Button variant="ghost" onClick={() => setMode(mode === "note" ? null : "note")}>Add note</Button>
        </div>
      )}
      {mode && (
        <form className="sp-opt-form" onSubmit={(e) => { e.preventDefault(); run(mode, mode === "ignore" ? { reason: text } : { note: text }); }}>
          <input aria-label={mode === "ignore" ? "Reason for ignoring" : "Note"} value={text} maxLength={mode === "ignore" ? 500 : 2000}
                 placeholder={mode === "ignore" ? "Why is this being ignored?" : "Add a note for your team"}
                 onChange={(e) => setText(e.target.value)} />
          <Button type="submit" disabled={!text.trim()} loading={busy === mode}>{mode === "ignore" ? "Ignore" : "Save note"}</Button>
        </form>
      )}
      {s === "ready_for_verification" && <p className="sp-si-muted">Marked fixed by your team. It counts as verified only after a check finds the issue gone.</p>}
      {err && <p className="sp-si-job-error" role="alert">{err}</p>}
    </div>
  );
}

function Evidence({ v }) {
  if (!v) return <span className="sp-si-muted">—</span>;
  const entries = Object.entries(v).filter(([, x]) => x !== null && x !== undefined);
  if (!entries.length) return <span className="sp-si-muted">—</span>;
  return <ul className="sp-opt-ev">{entries.map(([k, x]) => (
    <li key={k}><span className="sp-si-muted">{k.replace(/_/g, " ")}:</span> {typeof x === "object" ? JSON.stringify(x) : String(x)}</li>
  ))}</ul>;
}

export function Timeline({ items }) {
  if (!items?.length) return <p className="sp-si-muted">No activity yet.</p>;
  return (
    <ol className="sp-opt-tl">
      {items.map((t, i) => (
        <li key={`${t.kind}-${t.at}-${i}`}>
          <div><span className="sp-si-muted">{fmtDate(t.at)}</span> · <strong>{t.text}</strong>{t.issue && <> — {t.issue}</>}</div>
          {t.note && <div className="sp-opt-note">“{t.note}”</div>}
          {t.kind === "verification" && t.detail && (
            <details className="sp-opt-detail">
              <summary>Before and after evidence</summary>
              {t.detail.reason && <p className="sp-si-muted">{t.detail.reason}</p>}
              <div className="sp-pi-cols">
                <div><h4>Before (when found)</h4>{t.detail.before_observed && <p className="sp-si-muted">{t.detail.before_observed}</p>}<Evidence v={t.detail.before} /></div>
                <div><h4>After ({METHOD[t.detail.method]})</h4>{t.detail.after_observed && <p className="sp-si-muted">{t.detail.after_observed}</p>}<Evidence v={t.detail.after} /></div>
              </div>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

const fmtVal = (v) => (v == null ? "—" : typeof v === "number" ? v.toLocaleString() : String(v));

function Side({ s }) {
  if (!s) return <span className="sp-si-muted">Not captured</span>;
  const label = s.status === "complete" ? `${s.window[0]}${s.window[1] && s.window[1] !== s.window[0] ? ` to ${s.window[1]}` : ""}`
    : s.status === "pending" ? "Waiting for data" : "Unavailable";
  return <>{label}{s.reason && s.status !== "complete" && <div className="sp-si-muted">{s.reason}</div>}</>;
}

export function FixImpact({ cmp }) {
  if (!cmp) return null;
  return (
    <div className="sp-opt-impact">
      {cmp.excluded_period && <p className="sp-si-muted">Compares {cmp.window_days}-day windows before work started and after the fix
        was verified; {cmp.excluded_period[0]} to {cmp.excluded_period[1]} (the change period) is left out of both.</p>}
      {cmp.sources.map((s) => {
        const metrics = Object.keys({ ...(s.before?.metrics || {}), ...(s.after?.metrics || {}) }).filter((k) => METRIC[k]);
        return (
          <div key={s.source} className="sp-opt-src">
            <h4>{s.label}</h4>
            <div className="sp-si-table-wrap">
              <table className="sp-si-table sp-opt-table">
                <thead><tr><th>Measure</th><th>Before</th><th>After</th><th>Observed change</th></tr></thead>
                <tbody>
                  <tr><td>Period</td><td><Side s={s.before} /></td><td><Side s={s.after} /></td><td /></tr>
                  {metrics.map((k) => {
                    const c = s.changes[k];
                    return (
                      <tr key={k}><td>{METRIC[k]}</td>
                        <td>{s.before?.status === "complete" ? fmtVal(s.before.metrics?.[k]) : "—"}</td>
                        <td>{s.after?.status === "complete" ? fmtVal(s.after.metrics?.[k]) : "—"}</td>
                        <td>{c ? `${c.delta > 0 ? "+" : ""}${c.delta.toLocaleString()}${c.pct != null ? ` (${c.pct > 0 ? "+" : ""}${c.pct}%)` : ""}` : "—"}</td></tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {s.after?.status === "complete" && s.after.reason && <p className="sp-si-muted">{s.after.reason}</p>}
          </div>
        );
      })}
      <p className="sp-si-muted">{cmp.disclaimer}</p>
    </div>
  );
}
