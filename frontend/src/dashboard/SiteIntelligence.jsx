/* Site Intelligence (/app/site-intelligence/:monitorId) — one website's pages: what
   was discovered and crawled, what each page's evidence shows, and which fixes matter
   first. Every number comes from the backend's persisted crawl (GET
   /monitors/:id/site-intelligence and the inventory/detail endpoints); nothing is
   estimated here. Progress is shown only as the stored counters report it — no
   animated or guessed percentage — and polling stops the moment the analysis reaches
   a terminal state.

   Findings are labeled by how they are supported: "Checked on each page"
   (deterministic: each listed page has the issue) vs "AI analysis of a representative
   page" (applied to its template, not checked page by page), and by kind: confirmed
   issue / potential opportunity / insufficient evidence. Free plan: a short, real
   preview of the top findings; Pro unlocks the full list, status changes and
   recrawl-to-verify. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, ChevronDown, ChevronUp, Clock, ExternalLink, History, Info, Lock,
  Network, Play, RefreshCw, Search, X, XCircle,
} from "lucide-react";
import {
  bulkUpdateFindingStatus, cancelCrawlJob, compareSiteIntelligence, createCrawlJob, getCrawlUrl,
  getSiteIntelligence, getSiteProfile, listCrawlTemplates, listCrawlUrls, listMonitors,
  listUrlFindings, recrawlFinding, updateFindingStatus, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import PageOpportunities from "./PageOpportunities.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { Shell, Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import "./SiteIntelligence.spectrum.css";

export const POLL_MS = 5000;
const PRIORITY_LABEL = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
const STATUS_LABEL = { open: "Open", in_progress: "In progress", fixed: "Fixed", wont_fix: "Won't fix" };
const VERIFY_LABEL = {
  verified: "Verified fixed", partially_improved: "Partially improved",
  unchanged: "Still present", regressed: "Regressed", not_comparable: "Could not re-check",
};
const KIND_LABEL = {
  confirmed: "Confirmed issue", potential: "Potential opportunity", insufficient_evidence: "Insufficient evidence",
};
const BASIS_LABEL = {
  deterministic: "Checked on each page", ai_representative: "AI analysis of a representative page",
};
const PAGE_TYPE_LABEL = {
  homepage: "Homepage", product: "Product", product_category: "Category", pricing: "Pricing",
  comparison: "Comparison", booking: "Booking", flight_hotel_inventory: "Inventory", service: "Service",
  lead_generation: "Contact / lead", informational: "Article / resource", legal_utility: "Legal / utility",
  other: "Other",
};
const URL_STATUS_LABEL = {
  discovered: "Discovered", eligible: "Queued", crawled: "Crawled", skipped: "Skipped", failed: "Failed",
};
const VIEWS = [
  ["all", "All pages"], ["has_issues", "With issues"], ["high_priority", "High priority"],
  ["not_crawled", "Not crawled"], ["not_analyzed", "Not analyzed"], ["failed", "Failed"], ["skipped", "Skipped"],
];
const SORTS = [
  ["-issues", "Most severe first"], ["url", "URL A–Z"], ["-crawled_at", "Recently crawled"],
  ["http_status", "HTTP status"], ["discovered", "Discovery order"],
];

const msgOf = (e, fallback) => (e instanceof ScanError ? e.message : fallback);

export function useDebouncedValue(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function SiteIntelligence({ monitorId, onSelectMonitor, onOpenMonitor, view = "health",
                                          onChangeView = () => {}, gscFlash = null }) {
  const { openUpgrade } = useUpgrade();
  const { hasPermission } = useAuth();
  const canRun = hasPermission("scan:run");
  const canManage = hasPermission("org:update");
  const [monitors, setMonitors] = useState(null);
  const [data, setData] = useState(undefined);       // undefined = loading
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    listMonitors()
      .then((r) => { if (alive) setMonitors(r.monitors || []); })
      .catch((e) => { if (alive) { setMonitors([]); setError(msgOf(e, "Could not load your websites.")); } });
    return () => { alive = false; };
  }, []);

  // No website in the URL: open the first one (replacing history, so Back still works).
  useEffect(() => {
    if (!monitorId && monitors?.length) onSelectMonitor(monitors[0].id, { replace: true });
  }, [monitorId, monitors, onSelectMonitor]);

  const known = !monitorId || !monitors || monitors.some((m) => m.id === monitorId);

  // One overview request, re-polled only while the analysis is actually running.
  // A setTimeout chain (never setInterval) so requests can't overlap, and it is
  // cleared on unmount or when the website changes.
  useEffect(() => {
    if (!monitorId || !known) return undefined;
    let alive = true;
    let timer = null;
    const tick = async () => {
      try {
        const o = await getSiteIntelligence(monitorId);
        if (!alive) return;
        setData(o); setError(null);
        if (o.status?.active) timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        if (!alive) return;
        setData((d) => (d === undefined ? null : d));
        setError(msgOf(e, "Could not load Site Intelligence for this website."));
      }
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [monitorId, known, reloadKey]);

  const start = async () => {
    setBusy(true); setError(null);
    try { await createCrawlJob(monitorId); reload(); }
    catch (e) { setError(msgOf(e, "Could not start the analysis.")); }
    finally { setBusy(false); }
  };
  const cancel = async () => {
    setBusy(true);
    try { await cancelCrawlJob(monitorId, data.latest_job.id); reload(); }
    catch (e) { setError(msgOf(e, "Could not cancel the analysis.")); }
    finally { setBusy(false); }
  };
  const onGated = (e) => {
    if (e instanceof ScanError && e.code === 402) { openUpgrade("site_intelligence"); return true; }
    return false;
  };

  if (monitors && !monitors.length) {
    return (
      <Shell className="sp-si">
        <Cell className="sp-si-card sp-si-empty">
          <h2><Network size={16} /> Add a website first</h2>
          <p>Site Intelligence analyzes one of your monitored websites. Add a website under
            Monitoring, then come back here to analyze its pages.</p>
          <Link className="sp-si-link" to="/app/monitoring">Go to Monitoring <ArrowRight size={12} /></Link>
        </Cell>
      </Shell>
    );
  }

  const results = data?.results;
  const status = data?.status;
  return (
    <Shell className="sp-si">
      <Toolbar monitors={monitors} monitorId={monitorId} onSelect={onSelectMonitor}
               onOpenMonitor={onOpenMonitor} data={data} canRun={canRun} busy={busy}
               onStart={start} onCancel={cancel} />

      {error && <div className="sp-si-banner err" role="alert"><AlertTriangle size={14} /> {error}</div>}

      {!known && (
        <Cell className="sp-si-card">
          <p>This website isn't in your organization. Choose one of your websites above.</p>
        </Cell>
      )}

      {known && monitorId && (
        <div className="sp-si-viewtabs" role="tablist" aria-label="Site Intelligence view">
          <button type="button" role="tab" aria-selected={view !== "opportunities"} onClick={() => onChangeView("health")}>
            Site health</button>
          <button type="button" role="tab" aria-selected={view === "opportunities"} onClick={() => onChangeView("opportunities")}>
            Page opportunities</button>
        </div>
      )}

      {known && monitorId && view === "opportunities" && (
        <PageOpportunities key={monitorId} monitorId={monitorId} canRun={canRun} canManage={canManage}
                           onGated={onGated} onShowSiteHealth={() => onChangeView("health")} gscFlash={gscFlash} />
      )}

      {view !== "opportunities" && <>
      {known && data === undefined && <><Skeleton h={120} /><div style={{ height: 14 }} /><Skeleton h={240} /></>}

      {known && data && !data.latest_job && (
        <StartCard canRun={canRun} busy={busy} onStart={start} website={data.website} />
      )}

      {known && status && (status.active || status.phase !== "completed" || !results) && (
        <StatusPanel status={status} job={data.latest_job} hasResults={!!results} />
      )}

      {known && results && (
        <>
          <Summary results={results} />
          <Priorities monitorId={monitorId} results={results} isPro={data.is_pro} canRun={canRun}
                      onChanged={reload} onGated={onGated} onUpgrade={() => openUpgrade("site_intelligence")} />
          <Comparison monitorId={monitorId} results={results} />
          <Inventory monitorId={monitorId} jobId={results.job.id} />
          <TemplatesAndProfile monitorId={monitorId} jobId={results.job.id} />
        </>
      )}
      </>}
    </Shell>
  );
}

/* ---------------------------------------------------------------- toolbar */

function Toolbar({ monitors, monitorId, onSelect, onOpenMonitor, data, canRun, busy, onStart, onCancel }) {
  const status = data?.status;
  return (
    <div className="sp-si-toolbar">
      <div className="sp-si-toolbar-l">
        <select className="sp-si-select" aria-label="Website" value={monitorId || ""}
                onChange={(e) => onSelect(e.target.value)} disabled={!monitors}>
          {!monitors && <option value="">Loading websites…</option>}
          {(monitors || []).map((m) => <option key={m.id} value={m.id}>{m.name || m.domain || m.url}</option>)}
        </select>
        {monitorId && (
          <button type="button" className="sp-si-linkbtn" onClick={() => onOpenMonitor(monitorId)}>
            Monitoring for this site <ArrowRight size={12} />
          </button>
        )}
      </div>
      {canRun && data && (
        <div className="sp-si-toolbar-r">
          {status?.can_cancel && (
            <Button variant="ghost" onClick={onCancel} loading={busy}><X size={13} /> Cancel analysis</Button>
          )}
          {data.can_start && (
            <Button variant={data.latest_job ? "ghost" : "gradient"} onClick={onStart} loading={busy}>
              <Play size={13} /> {data.latest_job ? "Run a new analysis" : "Start site analysis"}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function StartCard({ canRun, busy, onStart, website }) {
  return (
    <Cell className="sp-si-card sp-si-empty">
      <h2><Network size={16} /> Analyze {website?.domain || "this website"}</h2>
      <p>Site Intelligence finds this website's pages from its sitemap and internal links,
        fetches each one, records what the page actually contains (title, description,
        headings, structured data, links, images), groups similar pages, and turns the
        problems it can show on each page into a prioritized list of fixes.</p>
      {canRun
        ? <Button variant="gradient" onClick={onStart} loading={busy}><Play size={14} /> Start site analysis</Button>
        : <p className="sp-si-sub-sm">Ask an admin or member of your organization to start the analysis.</p>}
    </Cell>
  );
}

/* ---------------------------------------------------------------- status */

function StatusPanel({ status, job, hasResults }) {
  const p = status.progress;
  const pct = p && p.total ? Math.min(100, Math.round((p.done / p.total) * 100)) : null;
  const icon = status.active ? <RefreshCw size={15} className="sp-si-spin" aria-hidden="true" />
    : status.phase === "failed" ? <XCircle size={15} /> : <Clock size={15} />;
  return (
    <Cell className={`sp-si-card sp-si-status st-${status.phase}`} aria-live="polite">
      <div className="sp-si-status-top">
        <h2>{icon} {status.label}</h2>
        {status.last_progress_at && <span className="sp-si-muted">Last update {fmtDate(status.last_progress_at)}</span>}
      </div>
      <p className="sp-si-sub-sm">{status.description}</p>
      {p && (
        <div className="sp-si-progress">
          {pct != null && (
            <div className="sp-si-bar" role="progressbar" aria-valuemin={0} aria-valuemax={p.total} aria-valuenow={p.done}
                 aria-label={status.label}>
              <span style={{ width: `${pct}%` }} />
            </div>
          )}
          <span className="sp-si-progress-txt">
            {p.done.toLocaleString()}{p.total != null ? ` of ${p.total.toLocaleString()}` : ""} {p.unit}
            {p.note ? ` · ${p.note}` : ""}
          </span>
        </div>
      )}
      {status.stalled && <p className="sp-si-warn"><AlertTriangle size={13} /> {status.stalled_note}</p>}
      {job?.error && <p className="sp-si-job-error">{job.error}</p>}
      {status.phase === "cancelled" && <p className="sp-si-sub-sm">You can start a new analysis at any time.</p>}
      {(status.phase === "failed" || status.phase === "cancelled") && hasResults && (
        <p className="sp-si-sub-sm">Results below are from the last analysis that finished.</p>
      )}
      {status.active && hasResults && (
        <p className="sp-si-sub-sm">Results below are from the previous completed analysis until this one finishes.</p>
      )}
    </Cell>
  );
}

/* ---------------------------------------------------------------- summary */

function Summary({ results }) {
  const c = results.counts;
  const s = results.status;
  const metrics = [
    ["Discovered", c.discovered, "Every URL found, including skipped ones."],
    ["Crawled", c.crawled, "Fetched and checked."],
    ["Analyzed", c.analyzed, "Crawled pages whose page group has been analyzed."],
    ["Failed", c.failed, "Could not be fetched."],
    ["Pending", c.pending, "Found but not fetched (the analysis stopped first, or the page budget was reached)."],
    ["With issues", c.with_issues, "Pages with at least one checked issue."],
  ];
  return (
    <Cell className="sp-si-card">
      <div className="sp-si-status-top">
        <h2>{s.outcome === "completed_with_warnings" ? <AlertTriangle size={15} /> : <Clock size={15} />}
          {" "}{s.outcome === "completed_with_warnings" ? "Completed with warnings" : "Completed"}</h2>
        <span className="sp-si-muted">Finished {fmtDate(results.job.finished_at)}</span>
      </div>
      <dl className="sp-si-metrics">
        {metrics.map(([label, value, tip]) => (
          <div key={label} title={tip}><dt>{label}</dt><dd>{value?.toLocaleString() ?? "—"}</dd></div>
        ))}
      </dl>
      <p className="sp-si-coverage">
        Crawl coverage <strong>{c.crawl_coverage_pct ?? "—"}{c.crawl_coverage_pct != null ? "%" : ""}</strong>
        <span className="sp-si-muted"> ({c.definitions.crawl_coverage_pct})</span>
        <br />
        Analysis coverage <strong>{c.analysis_coverage_pct ?? "—"}{c.analysis_coverage_pct != null ? "%" : ""}</strong>
        <span className="sp-si-muted"> ({c.definitions.analysis_coverage_pct})</span>
      </p>
      {s.warnings.length > 0 && (
        <ul className="sp-si-warnings">{s.warnings.map((w) => <li key={w}><Info size={12} /> {w}</li>)}</ul>
      )}
    </Cell>
  );
}

/* ---------------------------------------------------------------- priorities */

function Priorities({ monitorId, results, isPro, canRun, onChanged, onGated, onUpgrade }) {
  const [showAll, setShowAll] = useState(false);
  const fc = results.findings;
  return (
    <Cell className="sp-si-card">
      <div className="sp-si-status-top">
        <h2>What to fix first</h2>
        <Link className="sp-si-link" to="/app/action-center">Open Action Center <ArrowRight size={12} /></Link>
      </div>
      <p className="sp-si-sub-sm">
        {fc.open} open finding{fc.open === 1 ? "" : "s"} ({fc.confirmed} confirmed, {fc.potential} potential).
        Ranked by an explainable score: technical severity × evidence confidence × page-type
        intent × pages affected. It measures how urgent a fix is, not revenue.
        {fc.insufficient_evidence > 0 && ` ${fc.insufficient_evidence} AI reading(s) didn't have enough evidence to support a finding and are not listed.`}
      </p>
      {!results.top_priorities.length ? (
        <p className="sp-si-empty-inline">No open findings. Every crawled page passed the checks it was given.</p>
      ) : (
        <div className="sp-si-findings">
          {results.top_priorities.map((f) => (
            <FindingCard key={f.id} f={f} monitorId={monitorId} jobId={results.job.id} isPro={isPro}
                         canRun={canRun} onChanged={onChanged} onGated={onGated} />
          ))}
        </div>
      )}
      {!isPro && results.locked_count > 0 && (
        <button className="sp-si-locked-banner" onClick={onUpgrade}>
          <Lock size={14} /> {results.locked_count} more finding(s) — unlock the full list with Pro
        </button>
      )}
      {isPro && fc.open > results.top_priorities.length && (
        <button type="button" className="sp-si-more" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
          {showAll ? "Hide the full list" : `Show all ${fc.total} findings`}
        </button>
      )}
      {showAll && <AllFindings monitorId={monitorId} jobId={results.job.id} canRun={canRun}
                               onChanged={onChanged} onGated={onGated} />}
    </Cell>
  );
}

function AllFindings({ monitorId, jobId, canRun, onChanged, onGated }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [bulk, setBulk] = useState("in_progress");
  const [err, setErr] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    listUrlFindings(monitorId, jobId, { page, page_size: 25 })
      .then((r) => { if (alive) setData(r); })
      .catch((e) => { if (alive) setErr(msgOf(e, "Could not load findings.")); });
    return () => { alive = false; };
  }, [monitorId, jobId, page, tick]);
  const changed = () => { setTick((t) => t + 1); onChanged(); };
  const applyBulk = async () => {
    try { await bulkUpdateFindingStatus(monitorId, jobId, [...selected], bulk); setSelected(new Set()); changed(); }
    catch (e) { if (!onGated(e)) setErr(msgOf(e, "Could not update findings.")); }
  };
  if (err) return <p className="sp-si-job-error">{err}</p>;
  if (!data) return <Skeleton h={120} />;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  return (
    <div className="sp-si-allfindings">
      {canRun && (
        <div className="sp-si-bulk">
          <select aria-label="Status for selected findings" value={bulk} onChange={(e) => setBulk(e.target.value)}>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <Button variant="ghost" disabled={!selected.size} onClick={applyBulk}>Apply to {selected.size} selected</Button>
        </div>
      )}
      <div className="sp-si-findings">
        {data.findings.map((f) => (
          <FindingCard key={f.id} f={f} monitorId={monitorId} jobId={jobId} isPro canRun={canRun}
                       onChanged={changed} onGated={onGated}
                       selectable={canRun} selected={selected.has(f.id)}
                       onToggle={() => setSelected((s) => { const n = new Set(s); n.has(f.id) ? n.delete(f.id) : n.add(f.id); return n; })} />
        ))}
      </div>
      <Pager page={page} pages={pages} onPage={setPage} />
    </div>
  );
}

function FindingCard({ f, monitorId, jobId, isPro, canRun, onChanged, onGated, selectable, selected, onToggle }) {
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [err, setErr] = useState(null);
  const urls = f.affected_urls || [];
  const setStatus = async (status) => {
    setErr(null);
    try { await updateFindingStatus(monitorId, jobId, f.id, status); onChanged(); }
    catch (e) { if (!onGated(e)) setErr(msgOf(e, "Could not update the status.")); }
  };
  const recheck = async () => {
    setWorking(true); setErr(null);
    try { await recrawlFinding(monitorId, jobId, f.id); onChanged(); }
    catch (e) { if (!onGated(e)) setErr(msgOf(e, "Could not re-check this finding.")); }
    finally { setWorking(false); }
  };
  const lv = f.last_verification;
  return (
    <div className={`sp-si-finding pri-${f.priority}`}>
      <div className="sp-si-finding-top">
        {selectable && <input type="checkbox" aria-label={`Select ${f.finding_title}`} checked={selected} onChange={onToggle} />}
        <span className={`sp-si-pri pri-${f.priority}`}>{PRIORITY_LABEL[f.priority] || f.priority}</span>
        <button type="button" className="sp-si-finding-title" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {f.finding_title} {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>
      <div className="sp-si-tags">
        <span className={`sp-si-tag k-${f.kind}`}>{KIND_LABEL[f.kind] || f.kind}</span>
        <span className="sp-si-tag">{BASIS_LABEL[f.evidence_basis] || "Earlier analysis"}</span>
        <span className="sp-si-tag">{f.affected_url_count} page{f.affected_url_count === 1 ? "" : "s"}</span>
        <span className="sp-si-tag">{STATUS_LABEL[f.status] || f.status}</span>
        {f.verification_status && <span className="sp-si-tag">{VERIFY_LABEL[f.verification_status] || f.verification_status}</span>}
      </div>
      {open && (
        <div className="sp-si-finding-body">
          <p><strong>What's wrong:</strong> {f.why_it_matters}</p>
          <div>
            <strong>Affected pages{f.affected_url_count > urls.length ? ` (first ${urls.length} of ${f.affected_url_count})` : ""}:</strong>
            <ul className="sp-si-urls">
              {urls.map((u) => <li key={u}><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
            </ul>
            {f.evidence_basis === "ai_representative" && (
              <p className="sp-si-muted">Analyzed on this representative page; the other pages sharing its
                template were not checked individually for this finding.</p>
            )}
          </div>
          {f.observations?.length > 0 && (
            <div>
              <strong>Evidence:</strong>
              <ul className="sp-si-urls">
                {f.observations.map((o) => <li key={o.url}><code>{o.observed}</code> — {o.url}</li>)}
              </ul>
            </div>
          )}
          <p><strong>Recommended fix:</strong> {f.recommended_fix}</p>
          {f.verification_procedure && <p><strong>How to verify:</strong> {f.verification_procedure}</p>}
          <p className="sp-si-muted">
            Priority: {f.priority_reason}. Confidence: {f.confidence}.
            {f.evidence_limitations ? ` ${f.evidence_limitations}` : ""}
            {f.first_detected_at ? ` First detected ${fmtDate(f.first_detected_at)}.` : ""}
          </p>
          {lv && (
            <p className="sp-si-muted">
              Last re-check {fmtDate(lv.at)}: {lv.pages.filter((x) => x.still_present === false).length} of {lv.pages.length} re-checked
              page(s) no longer have the issue{lv.affected_count > lv.pages.length ? ` (a sample of ${lv.affected_count} affected pages)` : ""}.
            </p>
          )}
          {isPro && canRun && (
            <div className="sp-si-finding-actions">
              <label className="sp-si-muted">Status{" "}
                <select value={f.status} onChange={(e) => setStatus(e.target.value)}>
                  {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </label>
              <Button variant="ghost" onClick={recheck} loading={working}><RefreshCw size={12} /> Re-check now</Button>
            </div>
          )}
          {err && <p className="sp-si-job-error">{err}</p>}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- comparison */

function Comparison({ monitorId, results }) {
  const cmp = results.comparison;
  const [pages, setPages] = useState(null);
  const [loading, setLoading] = useState(false);
  const loadPages = async () => {
    setLoading(true);
    try { setPages(await compareSiteIntelligence(monitorId, { job_id: results.job.id })); }
    catch { setPages({ available: false, message: "Could not load the page-level comparison." }); }
    finally { setLoading(false); }
  };
  return (
    <Cell className="sp-si-card">
      <h2><History size={15} /> Since the previous analysis</h2>
      {!cmp.available ? <p className="sp-si-sub-sm">{cmp.message}</p> : (
        <>
          <p className="sp-si-sub-sm">Compared with the analysis finished {fmtDate(cmp.baseline_finished_at)}.</p>
          <dl className="sp-si-metrics">
            <div><dt>New findings</dt><dd>{cmp.findings.new}</dd></div>
            <div><dt>Resolved</dt><dd>{cmp.findings.resolved}</dd></div>
            <div><dt>Still present</dt><dd>{cmp.findings.recurring}</dd></div>
          </dl>
          {cmp.findings.new_sample.length > 0 && (
            <ul className="sp-si-urls">{cmp.findings.new_sample.map((f) => <li key={f.id}>New: {f.title}</li>)}</ul>
          )}
          {cmp.findings.resolved_sample.length > 0 && (
            <ul className="sp-si-urls">{cmp.findings.resolved_sample.map((f) => <li key={f.id}>Resolved: {f.title}</li>)}</ul>
          )}
          {!pages && <Button variant="ghost" onClick={loadPages} loading={loading}>Show page-level changes</Button>}
          {pages?.available === false && <p className="sp-si-sub-sm">{pages.message}</p>}
          {pages?.pages && (
            <dl className="sp-si-metrics">
              <div><dt>New pages</dt><dd>{pages.pages.added}</dd></div>
              <div><dt>No longer found</dt><dd>{pages.pages.removed}</dd></div>
              <div><dt>More issues</dt><dd>{pages.pages.more_issues}</dd></div>
              <div><dt>Fewer issues</dt><dd>{pages.pages.fewer_issues}</dd></div>
            </dl>
          )}
          {pages?.pages?.more_issues_sample?.length > 0 && (
            <ul className="sp-si-urls">
              {pages.pages.more_issues_sample.map((r) => <li key={r.url}>{r.url}: {r.before} → {r.after} issues</li>)}
            </ul>
          )}
        </>
      )}
    </Cell>
  );
}

/* ---------------------------------------------------------------- inventory */

function Inventory({ monitorId, jobId }) {
  const [view, setView] = useState("all");
  const [http, setHttp] = useState("");
  const [sort, setSort] = useState("-issues");
  const [qInput, setQInput] = useState("");
  const q = useDebouncedValue(qInput.trim(), 300);
  // The page number belongs to one filter combination: changing a filter shows page 1
  // without a second request for the stale page.
  const filterKey = `${view}|${http}|${sort}|${q}`;
  const [pageState, setPageState] = useState({ key: filterKey, page: 1 });
  const page = pageState.key === filterKey ? pageState.page : 1;
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState(null);
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true); setErr(null);
    listCrawlUrls(monitorId, jobId, { view, http: http || undefined, sort, q: q || undefined, page, page_size: 50 })
      .then((r) => { if (seq.current === mine) setData(r); })
      .catch((e) => { if (seq.current === mine) setErr(msgOf(e, "Could not load pages.")); })
      .finally(() => { if (seq.current === mine) setLoading(false); });
  }, [monitorId, jobId, view, http, sort, q, page]);

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <Cell className="sp-si-card">
      <h2>Pages</h2>
      <div className="sp-si-views" role="tablist" aria-label="Page filter">
        {VIEWS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={view === k} className={view === k ? "on" : ""}
                  onClick={() => setView(k)}>{label}</button>
        ))}
      </div>
      <div className="sp-si-filters">
        <label className="sp-si-search"><Search size={13} />
          <input aria-label="Search pages" placeholder="Search URL or title…" value={qInput}
                 onChange={(e) => setQInput(e.target.value)} />
        </label>
        <select aria-label="HTTP status" value={http} onChange={(e) => setHttp(e.target.value)}>
          <option value="">Any HTTP status</option>
          {["2xx", "3xx", "4xx", "5xx"].map((h) => <option key={h} value={h}>{h}</option>)}
        </select>
        <select aria-label="Sort pages" value={sort} onChange={(e) => setSort(e.target.value)}>
          {SORTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      </div>
      {err && <p className="sp-si-job-error">{err}</p>}
      {!data && loading && <Skeleton h={200} />}
      {data && (
        <>
          <p className="sp-si-muted">{data.total.toLocaleString()} page{data.total === 1 ? "" : "s"}{loading ? " · updating…" : ""}</p>
          {!data.urls.length ? <p className="sp-si-empty-inline">No pages match these filters.</p> : (
            <div className="sp-si-table-wrap">
              <table className="sp-si-table sp-si-inventory">
                <thead><tr><th>Page</th><th>Type</th><th>Status</th><th>HTTP</th><th>Issues</th><th>Priority</th><th>Crawled</th></tr></thead>
                <tbody>
                  {data.urls.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <button type="button" className="sp-si-rowlink" onClick={() => setOpenId(u.id)}>
                          {u.page_title || u.url}
                        </button>
                        {u.page_title && <div className="sp-si-muted sp-si-url">{u.url}</div>}
                      </td>
                      <td>{PAGE_TYPE_LABEL[u.page_type] || "—"}</td>
                      <td>{URL_STATUS_LABEL[u.status] || u.status}{u.status === "crawled" && !u.analyzed ? " · not analyzed" : ""}</td>
                      <td>{u.http_status ?? "—"}</td>
                      <td title={u.issue_count == null && u.status === "crawled" ? "Crawled before per-page checks existed" : undefined}>
                        {u.issue_count ?? "—"}
                      </td>
                      <td>{u.priority ? <span className={`sp-si-pri pri-${u.priority}`}>{PRIORITY_LABEL[u.priority]}</span> : "—"}</td>
                      <td>{u.crawled_at ? fmtDate(u.crawled_at) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pager page={page} pages={pages} onPage={(p) => setPageState({ key: filterKey, page: p })} />
        </>
      )}
      {openId && <PageDetail monitorId={monitorId} jobId={jobId} urlId={openId} onClose={() => setOpenId(null)} />}
    </Cell>
  );
}

function Pager({ page, pages, onPage }) {
  if (pages <= 1) return null;
  return (
    <div className="sp-si-pager">
      <Button variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
      <span className="sp-si-muted">Page {page} of {pages}</span>
      <Button variant="ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
    </div>
  );
}

/* ---------------------------------------------------------------- page detail */

function PageDetail({ monitorId, jobId, urlId, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const closeRef = useRef(null);
  useEffect(() => {
    let alive = true;
    getCrawlUrl(monitorId, jobId, urlId)
      .then((r) => { if (alive) setD(r); })
      .catch((e) => { if (alive) setErr(msgOf(e, "Could not load this page.")); });
    return () => { alive = false; };
  }, [monitorId, jobId, urlId]);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const ev = d?.evidence || {};
  // Portaled to <body>: the cards use backdrop-filter, which makes position:fixed
  // resolve against the card instead of the viewport.
  return createPortal(
    <div className="sp-si-drawer-backdrop" onClick={onClose}>
      <aside className="sp-si-drawer" role="dialog" aria-modal="true" aria-label="Page details"
             onClick={(e) => e.stopPropagation()}>
        <div className="sp-si-drawer-head">
          <h2>Page details</h2>
          <button ref={closeRef} type="button" className="sp-si-close" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </div>
        {err && <p className="sp-si-job-error">{err}</p>}
        {!d && !err && <Skeleton h={240} />}
        {d && (
          <>
            <a className="sp-si-detail-url" href={d.url} target="_blank" rel="noreferrer">{d.url} <ExternalLink size={12} /></a>
            <dl className="sp-si-kv">
              <dt>Status</dt><dd>{URL_STATUS_LABEL[d.status] || d.status}{d.skip_reason ? ` (${d.skip_reason.replace(/_/g, " ")})` : ""}</dd>
              <dt>HTTP</dt><dd>{d.http_status ?? "—"}</dd>
              <dt>Page type</dt><dd>{PAGE_TYPE_LABEL[d.page_type] || "—"}</dd>
              <dt>Crawled</dt><dd>{d.crawled_at ? fmtDate(d.crawled_at) : "—"}</dd>
              {d.template && <><dt>Page group</dt><dd>{d.template.page_count} similar page(s){d.template.is_representative ? " · this is the representative page" : ""}</dd></>}
              {d.error && <><dt>Error</dt><dd>{d.error}</dd></>}
            </dl>

            <h3>Issues checked on this page</h3>
            {!d.issues_evaluated ? <p className="sp-si-muted">This page wasn't checked (it wasn't fetched).</p>
              : !d.issues.length ? <p className="sp-si-muted">No issues found by the checks run on this page.</p>
              : (
                <ul className="sp-si-issues">
                  {d.issues.map((i) => (
                    <li key={i.code}>
                      <div><span className={`sp-si-pri pri-${i.severity}`}>{PRIORITY_LABEL[i.severity]}</span>{" "}
                        <strong>{i.title}</strong> <span className={`sp-si-tag k-${i.kind}`}>{KIND_LABEL[i.kind]}</span></div>
                      {i.observed && <div className="sp-si-muted">Observed: <code>{i.observed}</code></div>}
                      <div>{i.why_it_matters}</div>
                      <div><strong>Fix:</strong> {i.recommended_fix}</div>
                      <div className="sp-si-muted">Verify: {i.verification_procedure}</div>
                    </li>
                  ))}
                </ul>
              )}

            {d.findings.length > 0 && (
              <>
                <h3>Findings that include this page</h3>
                <ul className="sp-si-issues">
                  {d.findings.map((f) => (
                    <li key={f.id}><strong>{f.finding_title}</strong>
                      <div className="sp-si-muted">{f.applies_because} · {STATUS_LABEL[f.status] || f.status}</div></li>
                  ))}
                </ul>
              </>
            )}

            <h3>Traffic to this page (last {d.traffic.window_days} days)</h3>
            <dl className="sp-si-kv">
              <dt>AEOMirror tracking</dt><dd>{trafficText(d.traffic.first_party)}</dd>
              <dt>Google Analytics</dt><dd>{trafficText(d.traffic.ga4)}</dd>
            </dl>

            {d.evidence && (
              <>
                <h3>Evidence from the page</h3>
                <dl className="sp-si-kv">
                  <dt>Title</dt><dd>{ev.title || "—"}{ev.title_length ? ` (${ev.title_length} chars)` : ""}</dd>
                  <dt>Meta description</dt><dd>{ev.meta_description || "—"}</dd>
                  <dt>Canonical</dt><dd>{ev.canonical_url || "—"}</dd>
                  <dt>Robots</dt><dd>{ev.robots_meta || "—"}</dd>
                  <dt>H1</dt><dd>{(ev.h1_texts || []).join(" | ") || "—"}</dd>
                  <dt>Words</dt><dd>{ev.word_count ?? "—"}</dd>
                  <dt>Links</dt><dd>{ev.internal_link_count ?? "—"} internal · {ev.external_link_count ?? "—"} external</dd>
                  <dt>Images</dt><dd>{ev.image_count != null ? `${ev.image_count} (${ev.images_missing_alt} without alt)` : "—"}</dd>
                  <dt>Structured data</dt><dd>{(ev.schema?.types || []).join(", ") || "none found"}</dd>
                </dl>
                <p className="sp-si-muted">Extracted {fmtDate(d.evidence_extracted_at)} when the page was crawled.</p>
                <details className="sp-si-raw"><summary>Technical details</summary><pre>{JSON.stringify(d.evidence, null, 2)}</pre></details>
              </>
            )}
          </>
        )}
      </aside>
    </div>,
    document.body,
  );
}

function trafficText(t) {
  if (!t) return "—";
  if (t.sessions != null && (t.status === "available" || t.status === "zero" || t.status === "stale")) {
    return `${t.sessions.toLocaleString()} session${t.sessions === 1 ? "" : "s"}`
      + (t.status === "stale" ? " (data is out of date)" : "")
      + (t.partial_window ? " (tracking started during this window)" : "");
  }
  return t.reason || "Not available";
}

/* ---------------------------------------------------------------- templates + profile */

function TemplatesAndProfile({ monitorId, jobId }) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState(null);
  const [profile, setProfile] = useState(null);
  useEffect(() => {
    if (!open || templates) return undefined;
    let alive = true;
    Promise.all([listCrawlTemplates(monitorId, jobId), getSiteProfile(monitorId, jobId).catch(() => ({ available: false }))])
      .then(([t, p]) => { if (alive) { setTemplates(t.templates || []); setProfile(p); } })
      .catch(() => { if (alive) setTemplates([]); });
    return () => { alive = false; };
  }, [open, templates, monitorId, jobId]);
  return (
    <Cell className="sp-si-card">
      <button type="button" className="sp-si-collapse" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <h2>Page groups and site profile</h2> {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && !templates && <Skeleton h={100} />}
      {open && profile?.available && (
        <div className="sp-si-profile-row">
          <div><div className="sp-si-profile-label">Likely business type</div>
            <div className="sp-si-profile-value">{profile.business_type.label}</div>
            <div className="sp-si-profile-conf">{profile.business_type.confidence} confidence</div></div>
          <div><div className="sp-si-profile-label">Detected entity</div>
            <div className="sp-si-profile-value">{profile.entity.detected_name || "Not detected"}</div>
            <div className="sp-si-profile-conf">{profile.entity.confidence} confidence</div></div>
        </div>
      )}
      {open && templates?.length > 0 && (
        <>
          <p className="sp-si-sub-sm">Pages grouped by structure and page type (a heuristic). AI analysis
            reads one representative page per group; every page's own checks are listed above.</p>
          <div className="sp-si-table-wrap">
            <table className="sp-si-table">
              <thead><tr><th>Page type</th><th>Pages</th><th>Representative page</th></tr></thead>
              <tbody>
                {templates.map((t) => (
                  <tr key={t.template_id}><td>{PAGE_TYPE_LABEL[t.page_type] || t.page_type}</td><td>{t.url_count}</td>
                    <td><a href={t.representative_url} target="_blank" rel="noreferrer">{t.representative_url}</a></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Cell>
  );
}
