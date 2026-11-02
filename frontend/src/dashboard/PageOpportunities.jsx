/* Page Opportunities — the business-value view of Site Intelligence
   (/app/site-intelligence/:monitorId?view=opportunities). Which pages matter most to
   the business and what to fix on them, from the backend's persisted scoring run
   (GET /monitors/:id/page-opportunities…). Nothing is computed or estimated here.

   Honesty rules carried into the UI: a null value is "Not measured" (never 0); the
   score's measured and inferred parts are labelled; uncertain page types say so;
   Search Console is Google Search data, never AI-assistant data; a high score is not
   a revenue promise. Supporting evidence sits behind a row's detail drawer. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, ExternalLink, Info, Link2, RefreshCw, Search, Target, X,
} from "lucide-react";
import {
  cancelPageOpportunitiesRun, connectSearchConsole, getPageOpportunities, getPageOpportunity,
  getSearchConsoleStatus, listPageOpportunities, listSearchConsoleSites, refreshPageOpportunities,
  selectSearchConsoleSite, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import PageIntelligence from "./PageIntelligence.jsx";
import "./PageOpportunities.spectrum.css";

export const PO_POLL_MS = 5000;
export const PAGE_TYPE_LABEL = {
  product: "Product", category: "Category", service: "Service", landing: "Landing",
  hotel_property: "Hotel / property", booking: "Booking", blog: "Blog / editorial", homepage: "Homepage",
  utility: "Utility", legal: "Legal / policy", login_account: "Login / account", unknown: "Other / unknown",
};
const PRIORITY_LABEL = { high: "High", medium: "Medium", low: "Low", none: "Nothing to fix", not_evaluated: "Not checked" };
const STATUS_LABEL = { confirmed: "Confirmed", likely: "Likely", uncertain: "Uncertain" };
const SORTS = [
  ["-priority", "Highest priority"], ["-score", "Highest value"], ["-ai_sessions", "Most AI sessions"],
  ["-organic_sessions", "Most organic sessions"], ["-gsc_clicks", "Most Google clicks"],
  ["-conversions", "Most conversions"], ["url", "URL A–Z"],
];
const HAS = [["", "Any page"], ["ai", "With AI traffic"], ["organic", "With organic traffic"],
  ["search", "With Google clicks"], ["conversions", "With conversions"], ["issues", "With issues"]];
const CONV_SOURCE = {
  first_party_verified: "server-verified conversions (AEOMirror tracking)",
  ga4_key_events: "GA4 key events",
};
const GSC_ERRORS = {
  access_denied: "Access to Search Console was declined.",
  scope_not_granted: "Search Console access wasn't granted on Google's consent screen.",
  token_exchange_failed: "Google didn't complete the sign-in. Please try again.",
  invalid_state: "The connection link expired. Please try again.",
  integration_disabled: "Google integrations aren't configured on this server.",
};

const msgOf = (e, fallback) => (e instanceof ScanError ? e.message : fallback);
const num = (v) => (v == null ? null : Number(v).toLocaleString());
const NM = <span className="sp-po-nm">Not measured</span>;

export default function PageOpportunities({ monitorId, canRun, canManage, onGated, onShowSiteHealth, gscFlash }) {
  const [data, setData] = useState(undefined);
  const [err, setErr] = useState(null);
  const [gated, setGated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    let timer = null;
    const tick = async () => {
      try {
        const o = await getPageOpportunities(monitorId);
        if (!alive) return;
        setData(o); setErr(null);
        if (o.latest_run?.active) timer = setTimeout(tick, PO_POLL_MS);
      } catch (e) {
        if (!alive) return;
        if (e instanceof ScanError && e.code === 402) { setGated(true); setData(null); return; }
        setData((d) => (d === undefined ? null : d));
        setErr(msgOf(e, "Could not load Page Opportunities for this website."));
      }
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [monitorId, reloadKey]);

  const score = async () => {
    setBusy(true); setErr(null);
    try { await refreshPageOpportunities(monitorId); reload(); }
    catch (e) { if (!onGated(e)) setErr(msgOf(e, "Could not start scoring.")); }
    finally { setBusy(false); }
  };
  const cancel = async () => {
    setBusy(true);
    try { await cancelPageOpportunitiesRun(monitorId, data.latest_run.id); reload(); }
    catch (e) { setErr(msgOf(e, "Could not cancel scoring.")); }
    finally { setBusy(false); }
  };

  if (gated) {
    return (
      <Cell className="sp-si-card sp-si-empty">
        <h2><Target size={16} /> Page Opportunities is a Pro feature</h2>
        <p>See which of your pages matter most to your business, using your analytics, Search Console and
          crawl data, and what to fix on them first.</p>
        <Button variant="gradient" onClick={() => onGated(new ScanError("Pro", 402))}>See Pro plans</Button>
      </Cell>
    );
  }

  const run = data?.latest_run;
  const results = data?.results;
  return (
    <div className="sp-po">
      {gscFlash && <GscFlash flash={gscFlash} />}
      {err && <div className="sp-si-banner err" role="alert"><AlertTriangle size={14} /> {err}</div>}
      {data === undefined && <><Skeleton h={110} /><div style={{ height: 14 }} /><Skeleton h={260} /></>}

      {data && !data.has_completed_crawl && !results && (
        <Cell className="sp-si-card sp-si-empty">
          <h2><Target size={16} /> Analyze this website first</h2>
          <p>Page Opportunities scores the pages a Site Intelligence analysis has crawled. Once an analysis
            finishes, every page is classified and scored automatically.</p>
          <Button variant="ghost" onClick={onShowSiteHealth}>Go to site analysis <ArrowRight size={12} /></Button>
        </Cell>
      )}

      {data && run?.active && <RunProgress run={run} hasResults={!!results} canRun={canRun} busy={busy} onCancel={cancel} />}

      {data && data.has_completed_crawl && !results && !run?.active && (
        <Cell className="sp-si-card sp-si-empty">
          <h2><Target size={16} /> Score this website's pages</h2>
          {run?.status === "failed" && <p className="sp-si-job-error">The last scoring run failed: {run.error}</p>}
          <p>Each crawled page gets a page type, a Business Value Score and a priority, from the data available
            for this website. Missing data is shown as not measured, never as zero.</p>
          {canRun ? <Button variant="gradient" onClick={score} loading={busy}><RefreshCw size={13} /> Score pages</Button>
            : <p className="sp-si-sub-sm">Ask an admin or member of your organization to start scoring.</p>}
        </Cell>
      )}

      {results && (
        <>
          <Header results={results} run={run} canRun={canRun} busy={busy} onScore={score} />
          <Kpis counts={results.counts} />
          <Sources sources={results.sources} monitorId={monitorId} canManage={canManage} onGated={onGated} />
          <PageTable monitorId={monitorId} runId={results.run.id} sources={results.sources} canRun={canRun} />
          <p className="sp-po-foot"><Info size={12} /> {data.definitions.score} {data.definitions.search_console}</p>
        </>
      )}
    </div>
  );
}

function GscFlash({ flash }) {
  if (flash.status === "connected") {
    return <div className="sp-si-banner ok" role="status"><Info size={14} /> Search Console connected. Choose a property below.</div>;
  }
  return (
    <div className="sp-si-banner err" role="alert"><AlertTriangle size={14} />{" "}
      {GSC_ERRORS[flash.reason] || "Search Console couldn't be connected. Please try again."}</div>
  );
}

function RunProgress({ run, hasResults, canRun, busy, onCancel }) {
  const pct = run.total_urls ? Math.min(100, Math.round((run.processed_urls / run.total_urls) * 100)) : null;
  return (
    <Cell className="sp-si-card" aria-live="polite">
      <div className="sp-si-status-top">
        <h2><RefreshCw size={15} className="sp-si-spin" aria-hidden="true" />
          {run.status === "pending" ? " Preparing to score pages" : " Scoring pages"}</h2>
        {canRun && <Button variant="ghost" onClick={onCancel} loading={busy}><X size={13} /> Cancel</Button>}
      </div>
      {pct != null && (
        <div className="sp-si-progress">
          <div className="sp-si-bar" role="progressbar" aria-valuemin={0} aria-valuemax={run.total_urls}
               aria-valuenow={run.processed_urls} aria-label="Scoring progress"><span style={{ width: `${pct}%` }} /></div>
          <span className="sp-si-progress-txt">{num(run.processed_urls)} of {num(run.total_urls)} pages</span>
        </div>
      )}
      {hasResults && <p className="sp-si-sub-sm">Results below are from the previous scoring until this one finishes.</p>}
    </Cell>
  );
}

function Header({ results, run, canRun, busy, onScore }) {
  return (
    <div className="sp-po-head">
      <span className="sp-si-muted">Scored {fmtDate(results.scored_at)}
        {results.stale ? " · a newer site analysis has finished since — rescore to include it" : ""}</span>
      {canRun && !run?.active && (
        <Button variant="ghost" onClick={onScore} loading={busy}><RefreshCw size={13} /> Rescore pages</Button>
      )}
    </div>
  );
}

function Kpis({ counts }) {
  const items = [
    ["Discovered URLs", counts.discovered_urls, "Every URL the site analysis found."],
    ["Commercial pages", counts.commercial_pages, "Product, category, service, landing, hotel, booking and home pages (not counting uncertain classifications)."],
    ["High-priority opportunities", counts.high_priority, "Valuable pages with a medium or worse issue."],
    ["Pages with AI traffic", counts.pages_with_ai_traffic, "Pages where GA4 recorded sessions from AI assistants."],
    ["Pages with organic traffic", counts.pages_with_organic_traffic, "Pages where GA4 recorded organic search sessions."],
    ["Pages with conversions", counts.pages_with_conversions, "Pages whose sessions produced a recorded conversion."],
  ];
  return (
    <dl className="sp-po-kpis">
      {items.map(([label, v, tip]) => (
        <div key={label} className="sp-po-kpi" title={tip}>
          <dt>{label}</dt><dd>{v == null ? NM : num(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ---------------------------------------------------------------- data sources */

function Sources({ sources, monitorId, canManage, onGated }) {
  const ga4 = sources.ga4 || {};
  const gsc = sources.gsc || {};
  return (
    <details className="sp-si-card sp-po-sources">
      <summary>
        <strong>Data used</strong>
        <span className="sp-po-chips">
          <Chip ok={ga4.available} label="Google Analytics" />
          <Chip ok={gsc.available} label="Search Console" />
          <Chip ok={!!sources.conversion_source} label="Conversions" />
          <Chip ok={sources.links?.available} label="Internal links" />
        </span>
      </summary>
      <dl className="sp-si-kv">
        <dt>Google Analytics</dt>
        <dd>{ga4.available
          ? <>Linked property {ga4.property_name}, last {sources.window?.days} days, synced {fmtDate(ga4.as_of)}.
              {!ga4.source_split_complete && " Some older days have no source split, so AI and organic sessions may be understated."}</>
          : ga4.reason} <Link className="sp-si-link" to="/app/analytics/ai-traffic">Manage</Link></dd>
        <dt>Search Console</dt>
        <dd>{gsc.available
          ? <>{gsc.site_url}, {gsc.window?.start} to {gsc.window?.end}, synced {fmtDate(gsc.as_of)}.
              {gsc.truncated && " Search Console returned more pages than one sync stores; pages beyond that show as not measured."}</>
          : gsc.reason}</dd>
        <dt>Conversions</dt>
        <dd>{sources.conversion_source
          ? `Counted from ${CONV_SOURCE[sources.conversion_source]} on sessions that started on each page.`
          : "Not measured: the linked GA4 property has no key events and no server-verified conversions were recorded."}
          {sources.conversion_source && !sources.revenue_available && " Revenue isn't shown: no revenue with a known currency."}</dd>
        <dt>Internal links</dt>
        <dd>{sources.links?.available ? "Counted from the pages this analysis crawled (a lower bound)." : "Not measured: run a new site analysis to count internal links."}</dd>
      </dl>
      <SearchConsolePanel monitorId={monitorId} canManage={canManage} onGated={onGated} />
      <p className="sp-si-muted">Search Console shows Google Search clicks and impressions. It isn't AI-assistant traffic,
        and its data isn't mixed into AI sessions.</p>
    </details>
  );
}

function Chip({ ok, label }) {
  return <span className={`sp-po-chip ${ok ? "on" : "off"}`}>{label}{ok ? "" : " · not used"}</span>;
}

function SearchConsolePanel({ monitorId, canManage, onGated }) {
  const [st, setSt] = useState(null);
  const [sites, setSites] = useState(null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [k, setK] = useState(0);
  useEffect(() => {
    let alive = true;
    getSearchConsoleStatus().then((s) => { if (alive) setSt(s); })
      .catch((e) => { if (alive) setErr(msgOf(e, "Could not load the Search Console connection.")); });
    return () => { alive = false; };
  }, [k]);
  const pending = st?.status === "pending_property" && canManage;
  useEffect(() => {
    if (!pending) return undefined;
    let alive = true;
    listSearchConsoleSites().then((r) => { if (alive) setSites(r.sites || []); })
      .catch((e) => { if (alive) setErr(msgOf(e, "Could not load your Search Console properties.")); });
    return () => { alive = false; };
  }, [pending]);

  const connect = async () => {
    setBusy(true); setErr(null);
    try {
      const { authorize_url } = await connectSearchConsole();
      if (!authorize_url) throw new ScanError("Could not start the connection.", 500);
      window.location.assign(authorize_url);   // real browser navigation to Google
    } catch (e) { if (!onGated(e)) setErr(msgOf(e, "Could not start the connection.")); setBusy(false); }
  };
  const choose = async () => {
    setBusy(true); setErr(null);
    try { await selectSearchConsoleSite(pick); setK((x) => x + 1); }
    catch (e) { setErr(msgOf(e, "Could not select that property.")); }
    finally { setBusy(false); }
  };

  if (!st) return err ? <p className="sp-si-job-error">{err}</p> : null;
  let body;
  if (st.status === "not_configured") body = <p className="sp-si-muted">Google integrations aren't set up on this server.</p>;
  else if (st.status === "not_available") body = null;
  else if (st.status === "not_connected" || st.status === "revoked" || st.status === "error") {
    body = (
      <>
        {st.status === "error" && <p className="sp-si-job-error">The Search Console connection needs to be reconnected.</p>}
        <p className="sp-si-muted">Read-only access (Google's "webmasters.readonly" permission). Separate from your
          Google Analytics connection.</p>
        {canManage ? <Button variant="ghost" onClick={connect} loading={busy}><Link2 size={13} /> Connect Search Console</Button>
          : <p className="sp-si-muted">Ask an admin to connect Search Console.</p>}
      </>
    );
  } else if (st.status === "pending_property") {
    body = !canManage ? <p className="sp-si-muted">Ask an admin to choose a Search Console property.</p>
      : sites === null ? <Skeleton h={36} />
      : !sites.length ? <p className="sp-si-muted">This Google account has no verified Search Console properties.</p>
      : (
        <div className="sp-po-gsc-pick">
          <select aria-label="Search Console property" value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Choose a verified property…</option>
            {sites.map((s) => <option key={s.site_url} value={s.site_url}>{s.site_url}</option>)}
          </select>
          <Button variant="ghost" disabled={!pick} onClick={choose} loading={busy}>Use this property</Button>
        </div>
      );
  } else {
    const covers = (st.covered_monitor_ids || []).includes(monitorId);
    body = (
      <p className="sp-si-muted">Connected to {st.site_url}
        {st.last_synced_at ? `, last synced ${fmtDate(st.last_synced_at)}` : ", first sync pending"}.
        {!covers && " This property doesn't cover this website's address, so its data isn't used here."}</p>
    );
  }
  return (
    <div className="sp-po-gsc">
      <h3>Google Search Console</h3>
      {err && <p className="sp-si-job-error">{err}</p>}
      {body}
    </div>
  );
}

/* ---------------------------------------------------------------- table */

function PageTable({ monitorId, runId, sources, canRun }) {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [priority, setPriority] = useState("");
  const [has, setHas] = useState("");
  const [commercial, setCommercial] = useState(false);
  const [sort, setSort] = useState("-priority");
  const filterKey = `${q}|${type}|${priority}|${has}|${commercial}|${sort}|${runId}`;
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
    listPageOpportunities(monitorId, {
      q: q || undefined, page_type: type || undefined, priority: priority || undefined, has: has || undefined,
      commercial: commercial ? "true" : undefined, sort, page, page_size: 25,
    })
      .then((r) => { if (seq.current === mine) setData(r); })
      .catch((e) => { if (seq.current === mine) setErr(msgOf(e, "Could not load pages.")); })
      .finally(() => { if (seq.current === mine) setLoading(false); });
  }, [monitorId, q, type, priority, has, commercial, sort, page, runId]);

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const showRevenue = !!sources.revenue_available;
  return (
    <Cell className="sp-si-card">
      <h2>Pages by business value</h2>
      <form className="sp-si-filters" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); }}>
        <label className="sp-si-search"><Search size={13} />
          <input aria-label="Search pages" placeholder="Search URL or title…" value={qInput}
                 onChange={(e) => setQInput(e.target.value)} onBlur={() => setQ(qInput.trim())} />
        </label>
        <select aria-label="Page type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All page types</option>
          {Object.entries(PAGE_TYPE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select aria-label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="">Any priority</option>
          {["high", "medium", "low", "none", "not_evaluated"].map((p) => <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>)}
        </select>
        <select aria-label="Data filter" value={has} onChange={(e) => setHas(e.target.value)}>
          {HAS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label className="sp-po-check"><input type="checkbox" checked={commercial}
                                              onChange={(e) => setCommercial(e.target.checked)} /> Commercial only</label>
        <select aria-label="Sort pages" value={sort} onChange={(e) => setSort(e.target.value)}>
          {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </form>
      {err && <p className="sp-si-job-error">{err}</p>}
      {!data && loading && <Skeleton h={220} />}
      {data && (
        <>
          <p className="sp-si-muted">{num(data.total)} page{data.total === 1 ? "" : "s"}{loading ? " · updating…" : ""}</p>
          {!data.pages.length ? <p className="sp-si-empty-inline">No pages match these filters.</p> : (
            <div className="sp-si-table-wrap">
              <table className="sp-si-table sp-po-table">
                <thead><tr>
                  <th>Page</th><th>Type</th><th title="Business Value Score, 0–100">Value</th><th>AI sessions</th>
                  <th>Organic sessions</th><th title="Google Search clicks / impressions">Google clicks / impr.</th>
                  <th>Conversions</th>{showRevenue && <th>Revenue</th>}<th>Main issue</th><th>Priority</th>
                </tr></thead>
                <tbody>
                  {data.pages.map((p) => (
                    <tr key={p.crawl_url_id}>
                      <td>
                        <button type="button" className="sp-si-rowlink" onClick={() => setOpenId(p.crawl_url_id)}>
                          {p.page_title || p.url}
                        </button>
                        {p.page_title && <div className="sp-si-muted sp-si-url">{p.url}</div>}
                      </td>
                      <td>{PAGE_TYPE_LABEL[p.page_type]}
                        {p.classification_status === "uncertain" && <span className="sp-po-unsure"> · uncertain</span>}</td>
                      <td><strong>{Math.round(p.score)}</strong></td>
                      <td>{p.ai_sessions == null ? NM : num(p.ai_sessions)}</td>
                      <td>{p.organic_sessions == null ? NM : num(p.organic_sessions)}</td>
                      <td>{p.gsc_clicks == null ? NM : `${num(p.gsc_clicks)} / ${num(p.gsc_impressions)}`}</td>
                      <td>{p.conversions == null ? NM : num(p.conversions)}</td>
                      {showRevenue && <td>{p.revenue == null ? NM : money(p.revenue, p.revenue_currency)}</td>}
                      <td>{p.main_issue ? p.main_issue.title : "—"}</td>
                      <td><span className={`sp-si-pri pri-${p.priority}`}>{PRIORITY_LABEL[p.priority]}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pages > 1 && (
            <div className="sp-si-pager">
              <Button variant="ghost" disabled={page <= 1} onClick={() => setPageState({ key: filterKey, page: page - 1 })}>Previous</Button>
              <span className="sp-si-muted">Page {page} of {pages}</span>
              <Button variant="ghost" disabled={page >= pages} onClick={() => setPageState({ key: filterKey, page: page + 1 })}>Next</Button>
            </div>
          )}
        </>
      )}
      {openId && <OpportunityDetail monitorId={monitorId} crawlUrlId={openId} canRun={canRun}
                                    onClose={() => setOpenId(null)} />}
    </Cell>
  );
}

export function money(v, currency) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(v); }
  catch { return `${Number(v).toLocaleString()} ${currency || ""}`.trim(); }
}

/* ---------------------------------------------------------------- detail */

export function OpportunityDetail({ monitorId, crawlUrlId, canRun, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const closeRef = useRef(null);
  useEffect(() => {
    let alive = true;
    getPageOpportunity(monitorId, crawlUrlId)
      .then((r) => { if (alive) setD(r); })
      .catch((e) => { if (alive) setErr(msgOf(e, "Could not load this page.")); });
    return () => { alive = false; };
  }, [monitorId, crawlUrlId]);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ctr = d && d.gsc_impressions ? ((d.gsc_clicks || 0) / d.gsc_impressions) * 100 : null;
  return createPortal(
    <div className="sp-si-drawer-backdrop" onClick={onClose}>
      <aside className="sp-si-drawer sp-po-drawer" role="dialog" aria-modal="true" aria-label="Page intelligence"
             onClick={(e) => e.stopPropagation()}>
        <div className="sp-si-drawer-head">
          <h2>Page intelligence</h2>
          <button ref={closeRef} type="button" className="sp-si-close" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </div>
        {err && <p className="sp-si-job-error">{err}</p>}
        {!d && !err && <Skeleton h={240} />}
        {d && (
          <>
            <a className="sp-si-detail-url" href={d.url} target="_blank" rel="noreferrer">{d.url} <ExternalLink size={12} /></a>

            <PageIntelligence monitorId={monitorId} crawlUrlId={crawlUrlId} canRun={canRun}
                              fallbackAction={d.recommended_action}>
              {(analysed) => <>
            <h3>Why this page matters</h3>
            <ul className="sp-po-why">{d.why_it_matters.map((w) => <li key={w}>{w}</li>)}</ul>
            <p className="sp-po-score">Business Value Score <strong>{Math.round(d.score)}</strong> / 100 ·
              priority <span className={`sp-si-pri pri-${d.priority}`}>{PRIORITY_LABEL[d.priority]}</span></p>
            <p className="sp-si-muted">{d.priority_reason} A high score doesn't guarantee revenue.</p>

            <h3>Page type</h3>
            <p>{PAGE_TYPE_LABEL[d.classification.page_type]} · <span className={`sp-po-status st-${d.classification.status}`}>
              {STATUS_LABEL[d.classification.status]}</span> ({Math.round(d.classification.confidence * 100)}% confidence)</p>
            <ul className="sp-po-why">{d.classification.evidence.map((e) => <li key={e}>{e}</li>)}</ul>

            <h3>Traffic and conversions (last {d.sources.window?.days} days)</h3>
            <dl className="sp-si-kv">
              <dt>AI assistant sessions</dt><dd>{d.ai_sessions == null ? NM : num(d.ai_sessions)}</dd>
              <dt>Organic search sessions</dt><dd>{d.organic_sessions == null ? NM : num(d.organic_sessions)}</dd>
              <dt>All sessions (GA4)</dt><dd>{d.total_sessions == null ? NM : num(d.total_sessions)}</dd>
              <dt>Conversions</dt><dd>{d.conversions == null ? NM : <>{num(d.conversions)} <span className="sp-si-muted">({CONV_SOURCE[d.conversion_source]})</span></>}</dd>
              <dt>Revenue</dt><dd>{d.revenue == null ? NM : money(d.revenue, d.revenue_currency)}</dd>
              <dt>Internal links to it</dt><dd>{d.inbound_links == null ? NM : num(d.inbound_links)}</dd>
            </dl>

            <h3>Google Search Console</h3>
            {d.gsc_clicks == null ? <p className="sp-si-muted">{d.sources.gsc?.available
              ? "Not measured: this page wasn't among the rows Search Console returned."
              : `Not measured: ${d.sources.gsc?.reason || "Search Console isn't connected."}`}</p> : (
              <dl className="sp-si-kv">
                <dt>Clicks</dt><dd>{num(d.gsc_clicks)}</dd>
                <dt>Impressions</dt><dd>{num(d.gsc_impressions)}</dd>
                <dt>Click-through rate</dt><dd>{ctr == null ? "—" : `${ctr.toFixed(1)}%`}</dd>
                <dt>Average position</dt><dd>{d.gsc_position == null ? "—" : d.gsc_position.toFixed(1)}</dd>
              </dl>
            )}
            <p className="sp-si-muted">Google Search data ({d.sources.gsc?.window?.start || "—"} to {d.sources.gsc?.window?.end || "—"}) — not AI-assistant prompts.</p>

            {!analysed && <><h3>Issues on this page</h3>
            {!d.issues.length && !d.data_signals.length ? <p className="sp-si-muted">No checked issues.</p> : (
              <ul className="sp-si-issues">
                {d.issues.map((i) => (
                  <li key={i.code}><span className={`sp-si-pri pri-${i.severity}`}>{i.severity}</span> <strong>{i.title}</strong>
                    {i.observed && <div className="sp-si-muted">Observed: <code>{i.observed}</code></div>}</li>
                ))}
                {d.data_signals.map((s) => (
                  <li key={s.code}><span className={`sp-si-pri pri-${s.severity}`}>{s.severity}</span> <strong>{s.title}</strong>
                    <div className="sp-si-muted">{s.why}</div></li>
                ))}
              </ul>
            )}
            {d.findings.length > 0 && (
              <p className="sp-si-muted">{d.findings.length} Action Center finding{d.findings.length === 1 ? "" : "s"} include this page.</p>
            )}</>}

            <details className="sp-po-breakdown">
              <summary>How the score was calculated</summary>
              <table className="sp-si-table">
                <thead><tr><th>Signal</th><th>Kind</th><th>Points</th><th>Basis</th></tr></thead>
                <tbody>
                  {d.score_breakdown.map((c) => (
                    <tr key={c.key}>
                      <td>{c.label}</td>
                      <td>{c.kind === "measured" ? "Measured" : "Inferred"}</td>
                      <td>{c.available ? `${c.points} / ${c.max_points}` : NM}</td>
                      <td>{c.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="sp-si-muted">The score is the points earned out of the points available. Signals that
                aren't measured for this website are left out instead of counting as zero.</p>
            </details>
              </>}
            </PageIntelligence>
          </>
        )}
      </aside>
    </div>,
    document.body,
  );
}
