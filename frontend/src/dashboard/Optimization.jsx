/* Optimization (Site Intelligence's third view): what is wrong with each important URL,
   what is being fixed, what a check has verified, and what to do next. Built on the
   Page Opportunities scores and Page Intelligence analyses; clicking a URL opens the same
   Page intelligence drawer, where each recommendation's fix is tracked. */
import React, { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { cancelOptimizationRechecks, getOptimization, listOptimizationUrls, ScanError } from "../api.js";
import { fmtDate } from "./ui.jsx";
import { Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import { OpportunityDetail, PAGE_TYPE_LABEL } from "./PageOpportunities.jsx";
import { FixImpact, RESULT, StatusPill } from "./OptimizationParts.jsx";
import "./PageOpportunities.spectrum.css";

const msgOf = (e, fallback) => (e instanceof ScanError ? e.message : fallback);
const PRIORITY = { high: "High", medium: "Medium", low: "Low", none: "Nothing to fix", not_evaluated: "Not checked" };
const FILTERS = [["", "All important URLs"], ["open", "With open issues"], ["in_progress", "In progress"],
  ["ready_for_verification", "Awaiting verification"], ["verified", "Verified"], ["reopened", "Reopened"],
  ["not_fixed", "Not fixed"], ["ignored", "Ignored"]];

export default function Optimization({ monitorId, canRun, onGated, onShowOpportunities }) {
  const [ov, setOv] = useState(undefined);
  const [err, setErr] = useState(null);
  const [gated, setGated] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const gatedRef = useRef(onGated);
  gatedRef.current = onGated;

  useEffect(() => {
    let alive = true;
    getOptimization(monitorId)
      .then((r) => { if (alive) { setOv(r); setErr(null); } })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ScanError && e.code === 402) { setGated(true); gatedRef.current?.(e); return; }
        setErr(msgOf(e, "Could not load optimization progress."));
      });
    return () => { alive = false; };
  }, [monitorId, tick]);

  const cancelAll = async () => {
    setBusy(true);
    try { await cancelOptimizationRechecks(monitorId); setTick((t) => t + 1); }
    catch (e) { setErr(msgOf(e, "Could not cancel the queued re-checks.")); }
    finally { setBusy(false); }
  };

  if (gated) return <Cell className="sp-si-card"><p>Tracking fixes URL by URL is a Pro feature.</p></Cell>;
  if (err && !ov) return <Cell className="sp-si-card"><p className="sp-si-job-error" role="alert">{err}</p></Cell>;
  if (ov === undefined) return <><Skeleton h={90} /><div style={{ height: 12 }} /><Skeleton h={240} /></>;
  if (!ov.available) {
    return (
      <Cell className="sp-si-card sp-si-empty">
        <h2>Nothing to track yet</h2>
        <p>{ov.reason}</p>
        <Button variant="ghost" onClick={onShowOpportunities}>Go to Page opportunities</Button>
      </Cell>
    );
  }
  const c = ov.counts;
  const r = ov.rechecks;
  return (
    <div className="sp-opt">
      {err && <p className="sp-si-job-error" role="alert">{err}</p>}
      <dl className="sp-po-kpis" aria-label="Optimization overview">
        <div className="sp-po-kpi"><dt>Important URLs</dt><dd>{c.important_urls}</dd></div>
        <div className="sp-po-kpi"><dt>Open issues</dt><dd>{c.open_issues}</dd></div>
        <div className="sp-po-kpi"><dt>In progress</dt><dd>{c.in_progress}</dd></div>
        <div className="sp-po-kpi"><dt>Awaiting verification</dt><dd>{c.awaiting_verification}</dd></div>
        <div className="sp-po-kpi"><dt>Verified fixes</dt><dd>{c.verified}</dd></div>
        <div className="sp-po-kpi"><dt>Reopened</dt><dd>{c.reopened}</dd></div>
      </dl>
      <p className="sp-si-muted sp-opt-checks">
        {r.scheduled ? <>{r.scheduled} page re-check{r.scheduled === 1 ? "" : "s"} scheduled{r.next_at && <>, next {fmtDate(r.next_at)}</>}.</>
          : "No page re-checks scheduled; fixes are also checked at every site crawl."}{" "}
        {r.used_today} of {r.daily_limit} checks used today.{" "}
        {canRun && r.scheduled > 0 && <Button variant="ghost" loading={busy} onClick={cancelAll}>Cancel scheduled re-checks</Button>}
      </p>

      <Cell className="sp-si-card">
        <h2>Next best actions</h2>
        {!ov.next_actions.length ? <p className="sp-si-muted">Nothing waiting: no open issue on your important pages needs a new action.</p> : (
          <ol className="sp-opt-next">
            {ov.next_actions.map((n) => (
              <li key={`${n.crawl_url_id}-${n.code}`}>
                <button type="button" className="sp-si-rowlink" onClick={() => setOpenId(n.crawl_url_id)}>
                  <strong>{n.issue}</strong></button> <StatusPill status={n.status} />
                <div className="sp-si-muted sp-si-url">{n.page_title || n.url}</div>
                {n.recommended_change && <div>{n.recommended_change}</div>}
                <div className="sp-si-muted">{n.reasons.join(" ")}</div>
              </li>
            ))}
          </ol>
        )}
        {ov.held_back.length > 0 && (
          <details className="sp-pi-sec">
            <summary>Held back ({ov.held_back.length})</summary>
            <ul className="sp-po-why">{ov.held_back.map((h) => <li key={`${h.crawl_url_id}-${h.code}`}>{h.issue} — {h.url}. {h.reason}</li>)}</ul>
          </details>
        )}
      </Cell>

      <UrlTable monitorId={monitorId} onOpen={setOpenId} refreshKey={tick} />

      <Cell className="sp-si-card">
        <h2>Performance after verified fixes</h2>
        {!ov.impact.length ? <p className="sp-si-muted">No verified fix has before-and-after data yet. Snapshots start when work on a fix begins.</p>
          : ov.impact.map((x) => (
            <details key={x.action_id} className="sp-pi-sec">
              <summary>{x.issue} — <span className="sp-si-muted">{x.url}</span> · verified {fmtDate(x.verified_at)}</summary>
              <FixImpact cmp={x.comparison} />
            </details>
          ))}
      </Cell>

      {openId && <OpportunityDetail monitorId={monitorId} crawlUrlId={openId} canRun={canRun}
                                    onClose={() => { setOpenId(null); setTick((t) => t + 1); }} />}
    </div>
  );
}

function UrlTable({ monitorId, onOpen, refreshKey }) {
  const [status, setStatus] = useState("open");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const key = `${status}|${q}`;
  const [pg, setPg] = useState({ key, page: 1 });
  const page = pg.key === key ? pg.page : 1;
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    setErr(null);
    listOptimizationUrls(monitorId, { status: status || undefined, q: q || undefined, page, page_size: 25 })
      .then((r) => { if (seq.current === mine) setData(r); })
      .catch((e) => { if (seq.current === mine) setErr(msgOf(e, "Could not load URLs.")); });
  }, [monitorId, status, q, page, refreshKey]);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <Cell className="sp-si-card">
      <h2>URL-wise optimization</h2>
      <form className="sp-si-filters" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); }}>
        <label className="sp-si-search"><Search size={13} />
          <input aria-label="Search URLs" placeholder="Search URL or title…" value={qInput}
                 onChange={(e) => setQInput(e.target.value)} onBlur={() => setQ(qInput.trim())} />
        </label>
        <select aria-label="Status filter" value={status} onChange={(e) => setStatus(e.target.value)}>
          {FILTERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </form>
      {err && <p className="sp-si-job-error">{err}</p>}
      {!data && !err && <Skeleton h={200} />}
      {data && (!data.urls.length ? <p className="sp-si-empty-inline">No URLs match.</p> : (
        <div className="sp-si-table-wrap">
          <table className="sp-si-table sp-po-table">
            <thead><tr><th>URL</th><th>Value</th><th>Priority</th><th>Main issue</th><th>Current action</th>
              <th>Verification</th><th>Last checked</th><th>Next check</th></tr></thead>
            <tbody>{data.urls.map((u) => (
              <tr key={u.crawl_url_id}>
                <td><button type="button" className="sp-si-rowlink" onClick={() => onOpen(u.crawl_url_id)}>{u.page_title || u.url}</button>
                  <div className="sp-si-muted sp-si-url">{u.url} · {PAGE_TYPE_LABEL[u.page_type] || u.page_type}</div></td>
                <td><strong>{Math.round(u.score)}</strong></td>
                <td><span className={`sp-si-pri pri-${u.priority}`}>{PRIORITY[u.priority] || u.priority}</span></td>
                <td>{u.main_issue ? u.main_issue.issue : "—"}{u.open_issues > 1 && <span className="sp-si-muted"> +{u.open_issues - 1} more</span>}</td>
                <td>{u.main_issue ? <StatusPill status={u.main_issue.status} /> : <span className="sp-si-muted">Nothing open</span>}</td>
                <td>{u.verification ? RESULT[u.verification.result] : <span className="sp-si-muted">Not checked yet</span>}</td>
                <td>{fmtDate(u.last_checked_at)}</td>
                <td>{u.next_check_at ? fmtDate(u.next_check_at) : <span className="sp-si-muted">Next site crawl</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ))}
      {pages > 1 && (
        <div className="sp-si-pager">
          <Button variant="ghost" disabled={page <= 1} onClick={() => setPg({ key, page: page - 1 })}>Previous</Button>
          <span className="sp-si-muted">Page {page} of {pages}</span>
          <Button variant="ghost" disabled={page >= pages} onClick={() => setPg({ key, page: page + 1 })}>Next</Button>
        </div>
      )}
    </Cell>
  );
}
