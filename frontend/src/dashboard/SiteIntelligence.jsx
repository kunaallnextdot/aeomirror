/* Site Intelligence — the URL Intelligence Engine + AI Expert Analysis Engine's
   front door for ONE monitored site: start a crawl (sitemap + internal-link
   discovery, deterministic evidence extraction, template-level AI analysis), watch
   its progress, and work the resulting Expert Action Center findings list (filter,
   sort by an explainable priority score, update status, recrawl to verify a fix).
   Free plan gets a short, real preview of the highest-priority findings; Pro
   unlocks the full list, bulk status updates, and recrawl/verification. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle, ChevronLeft, Clock, Info, Lock, Play, RefreshCw, Sparkles, X, XCircle,
} from "lucide-react";
import {
  bulkUpdateFindingStatus, cancelCrawlJob, createCrawlJob, getCrawlJob, listCrawlJobs,
  listCrawlTemplates, listUrlFindings, recrawlFinding, updateFindingStatus, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { Shell, Cell, Button, Skeleton } from "./aurora.jsx";
import "./SiteIntelligence.aurora.css";

const ACTIVE_PHASES = new Set(["pending", "discovering", "crawling", "analyzing"]);
const PHASE_LABEL = {
  pending: "Queued", discovering: "Discovering URLs", crawling: "Crawling pages",
  analyzing: "Analyzing with AI", completed: "Completed", failed: "Failed", cancelled: "Cancelled",
};
const PRIORITY_LABEL = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
const STATUS_LABEL = { open: "Open", in_progress: "In progress", fixed: "Fixed", wont_fix: "Won't fix" };
const VERIFY_LABEL = {
  verified: "Verified fixed", partially_improved: "Partially improved",
  unchanged: "Unchanged", regressed: "Regressed", not_comparable: "Could not re-check",
};

export default function SiteIntelligence({ monitorId, onBack }) {
  const { openUpgrade } = useUpgrade();
  const [job, setJob] = useState(undefined);        // undefined = loading, null = none yet
  const [findings, setFindings] = useState(null);
  const [templates, setTemplates] = useState(null);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [bulkStatus, setBulkStatus] = useState("in_progress");
  const [recrawling, setRecrawling] = useState(null);
  const pollRef = useRef(null);

  const loadLatestJob = useCallback(async () => {
    try {
      const { jobs } = await listCrawlJobs(monitorId);
      setJob(jobs.length ? jobs[0] : null);
      setError(null);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load crawl jobs.");
      setJob(null);
    }
  }, [monitorId]);

  useEffect(() => { loadLatestJob(); }, [loadLatestJob]);

  const loadResults = useCallback(async (jobId) => {
    try {
      const [f, t] = await Promise.all([listUrlFindings(monitorId, jobId), listCrawlTemplates(monitorId, jobId)]);
      setFindings(f);
      setTemplates(t.templates || []);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load Site Intelligence results.");
    }
  }, [monitorId]);

  useEffect(() => {
    if (job && !ACTIVE_PHASES.has(job.phase)) loadResults(job.id);
    else { setFindings(null); setTemplates(null); }
  }, [job, loadResults]);

  // Poll while a crawl is actively running — cleared on unmount or once terminal.
  useEffect(() => {
    if (!job || !ACTIVE_PHASES.has(job.phase)) return;
    pollRef.current = setInterval(async () => {
      try { setJob(await getCrawlJob(monitorId, job.id)); } catch { /* keep showing the last known state */ }
    }, 4000);
    return () => clearInterval(pollRef.current);
  }, [job, monitorId]);

  const startCrawl = async () => {
    setCreating(true); setError(null);
    try { setJob(await createCrawlJob(monitorId)); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not start the crawl."); }
    finally { setCreating(false); }
  };

  const cancel = async () => {
    try { setJob(await cancelCrawlJob(monitorId, job.id)); } catch { /* keep current state */ }
  };

  const setStatus = async (findingId, status) => {
    try {
      await updateFindingStatus(monitorId, job.id, findingId, status);
      await loadResults(job.id);
    } catch (e) {
      if (e instanceof ScanError && e.code === 402) openUpgrade("site_intelligence");
    }
  };

  const toggleSelected = (id) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const applyBulk = async () => {
    if (!selected.size) return;
    try {
      await bulkUpdateFindingStatus(monitorId, job.id, Array.from(selected), bulkStatus);
      setSelected(new Set());
      await loadResults(job.id);
    } catch (e) {
      if (e instanceof ScanError && e.code === 402) openUpgrade("site_intelligence");
    }
  };

  const recrawl = async (findingId) => {
    setRecrawling(findingId);
    try {
      await recrawlFinding(monitorId, job.id, findingId);
      await loadResults(job.id);
    } catch (e) {
      if (e instanceof ScanError && e.code === 402) openUpgrade("site_intelligence");
    } finally { setRecrawling(null); }
  };

  return (
    <Shell className="au-si">
      <div className="au-si-toolbar">
        <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to monitor</Button>
      </div>
      <Header />

      {error && <div className="au-si-banner err"><AlertTriangle size={14} /> {error}</div>}

      {job === undefined && <Skeleton h={200} />}

      {job === null && (
        <StartCrawlCard onStart={startCrawl} creating={creating} />
      )}

      {job && (
        <JobStatusCard job={job} onCancel={cancel} onStartAnother={startCrawl} creating={creating} />
      )}

      {job && !ACTIVE_PHASES.has(job.phase) && job.phase === "completed" && templates && (
        <TemplatesCard templates={templates} />
      )}

      {job && !ACTIVE_PHASES.has(job.phase) && findings && (
        <FindingsCard
          findings={findings} selected={selected} onToggleSelected={toggleSelected}
          bulkStatus={bulkStatus} onBulkStatusChange={setBulkStatus} onApplyBulk={applyBulk}
          onSetStatus={setStatus} onRecrawl={recrawl} recrawling={recrawling}
          onUpgrade={() => openUpgrade("site_intelligence")}
        />
      )}
    </Shell>
  );
}

function Header() {
  return (
    <header className="au-si-head">
      <h1><Sparkles size={20} /> Site Intelligence</h1>
      <p className="au-si-sub">
        Crawls this site's sitemap and internal links, extracts real deterministic
        evidence from each page (titles, headings, structured data, product
        attributes), groups pages into templates, and runs an AI Expert Analysis on
        one representative page per template — never on raw HTML, never inventing a
        problem the evidence doesn't support.
      </p>
    </header>
  );
}

function StartCrawlCard({ onStart, creating }) {
  return (
    <Cell className="au-si-card">
      <h2>Start a Site Intelligence crawl</h2>
      <p>Discovers pages from this site's sitemap and internal links, classifies each
        by business page type, and prioritizes evidence-backed findings — free plans
        get a small sample, Pro reaches much larger sites.</p>
      <Button variant="primary" onClick={onStart} loading={creating}>
        <Play size={14} /> Start crawl
      </Button>
    </Cell>
  );
}

function JobStatusCard({ job, onCancel, onStartAnother, creating }) {
  const active = ACTIVE_PHASES.has(job.phase);
  return (
    <Cell className="au-si-card">
      <div className="au-si-job-top">
        <h2>{active ? <RefreshCw size={16} className="au-si-spin" /> : job.phase === "failed" ? <XCircle size={16} /> : <Clock size={16} />} {PHASE_LABEL[job.phase] || job.phase}</h2>
        {active && <Button variant="ghost" onClick={onCancel}><X size={13} /> Cancel</Button>}
        {!active && <Button variant="ghost" onClick={onStartAnother} loading={creating}><Play size={13} /> Run again</Button>}
      </div>
      {job.error && <p className="au-si-job-error">{job.error}</p>}
      <div className="au-si-job-stats">
        <span>{job.discovered_count} discovered</span>
        <span>{job.crawled_count} crawled</span>
        <span>{job.analyzed_count} analyzed</span>
        {job.failed_count > 0 && <span>{job.failed_count} failed</span>}
        {job.skipped_count > 0 && <span>{job.skipped_count} skipped</span>}
      </div>
      {job.ai_calls_total > 0 && (
        <p className="au-si-sub-sm">
          {job.ai_calls_total} AI analysis call(s) ({job.ai_calls_cached} reused from cache) —
          one call per page TEMPLATE, never per URL.
        </p>
      )}
      <p className="au-si-sub-sm">Started {fmtDate(job.started_at || job.created_at)}
        {job.finished_at && ` · finished ${fmtDate(job.finished_at)}`}</p>
    </Cell>
  );
}

function TemplatesCard({ templates }) {
  if (!templates.length) return null;
  return (
    <Cell className="au-si-card">
      <h2>Page templates found</h2>
      <p className="au-si-sub-sm">Pages grouped by a structural + page-type fingerprint — the AI
        engine analyzes one representative per group, then applies the finding across every
        page that shares it.</p>
      <table className="au-si-table">
        <thead><tr><th>Page type</th><th>Pages</th><th>Representative URL</th></tr></thead>
        <tbody>
          {templates.map((t) => (
            <tr key={t.template_id}>
              <td>{t.page_type || "other"}</td><td>{t.url_count}</td>
              <td><a href={t.representative_url} target="_blank" rel="noreferrer">{t.representative_url}</a></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Cell>
  );
}

function FindingsCard({ findings, selected, onToggleSelected, bulkStatus, onBulkStatusChange,
                       onApplyBulk, onSetStatus, onRecrawl, recrawling, onUpgrade }) {
  const { findings: rows, is_pro: isPro, locked_count, total } = findings;
  if (!total) {
    return (
      <Cell className="au-si-card">
        <Info size={16} />
        <p>No findings yet for this crawl — either every page looked clean on the evidence
          checked, or the crawl is still analyzing.</p>
      </Cell>
    );
  }
  return (
    <Cell className="au-si-card">
      <h2>Expert Action Center — findings</h2>
      <p className="au-si-sub-sm">Ranked by an explainable priority score (technical severity ×
        confidence × business page-type intent × pages affected) — every score has a reason,
        never a black box.</p>

      {isPro && (
        <div className="au-si-bulk">
          <select value={bulkStatus} onChange={(e) => onBulkStatusChange(e.target.value)}>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <Button variant="ghost" disabled={!selected.size} onClick={onApplyBulk}>
            Apply to {selected.size} selected
          </Button>
        </div>
      )}

      <div className="au-si-findings">
        {rows.map((f) => (
          <FindingRow key={f.id} f={f} isPro={isPro} selected={selected.has(f.id)}
                     onToggleSelected={() => onToggleSelected(f.id)}
                     onSetStatus={(s) => onSetStatus(f.id, s)}
                     onRecrawl={() => onRecrawl(f.id)} recrawling={recrawling === f.id} />
        ))}
      </div>

      {!isPro && locked_count > 0 && (
        <button className="au-si-locked-banner" onClick={onUpgrade}>
          <Lock size={14} /> {locked_count} more finding(s) on this crawl — unlock the full
          Expert Action Center with Pro
        </button>
      )}
    </Cell>
  );
}

function FindingRow({ f, isPro, selected, onToggleSelected, onSetStatus, onRecrawl, recrawling }) {
  return (
    <div className={`au-si-finding pri-${f.priority}`}>
      <div className="au-si-finding-top">
        {isPro && <input type="checkbox" checked={selected} onChange={onToggleSelected} />}
        <span className={`au-si-pri pri-${f.priority}`}>{PRIORITY_LABEL[f.priority] || f.priority}</span>
        <strong>{f.finding_title}</strong>
        {f.affected_url_count > 1 && <span className="au-si-affected">{f.affected_url_count} pages</span>}
      </div>
      {f.url && <a className="au-si-finding-url" href={f.url} target="_blank" rel="noreferrer">{f.url}</a>}
      <p className="au-si-finding-why"><strong>Why it matters:</strong> {f.why_it_matters}</p>
      <p className="au-si-finding-fix"><strong>Recommended fix:</strong> {f.recommended_fix}</p>
      <p className="au-si-finding-meta">
        Confidence: {f.confidence} · {f.cache_hit ? "reused from a prior analysis" : f.model_used ? "AI-analyzed" : "deterministic check only"}
        {f.verification_status && ` · ${VERIFY_LABEL[f.verification_status] || f.verification_status}`}
      </p>
      {f.evidence_limitations && <p className="au-si-finding-limits">Limitations: {f.evidence_limitations}</p>}
      {isPro && (
        <div className="au-si-finding-actions">
          <select value={f.status} onChange={(e) => onSetStatus(e.target.value)}>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <Button variant="ghost" onClick={onRecrawl} loading={recrawling}>
            <RefreshCw size={12} /> Recrawl &amp; verify
          </Button>
        </div>
      )}
    </div>
  );
}
