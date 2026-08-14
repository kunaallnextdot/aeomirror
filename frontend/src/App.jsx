import React, { useState, useEffect, useRef, Suspense } from "react";
import {
  Search, Lock, ArrowRight, Radar, LayoutDashboard, ScanLine, MessageSquareText,
  Users, Wrench, FileText, Settings, RefreshCw, AlertTriangle, ChevronDown, Download,
  ExternalLink, Sparkles, Globe, Mail, LifeBuoy
} from "lucide-react";
import { scanUrl, getScanById, bulkScanUrls, bulkScanFile, billing, downloadReport, ScanError } from "./api";
import { UpgradeProvider, useUpgrade } from "./dashboard/UpgradeModal.jsx";
import { setPendingScan, takePendingScan, freeScanUsed, markFreeScanUsed } from "./auth/pendingScan.js";
import { useAuth } from "./auth/AuthContext.jsx";
import { Routes, Route, useParams } from "react-router-dom";
import { navigate, RouterBridge } from "./auth/router.jsx";
import { RequireAdmin } from "./app/guards.jsx";
import { RouteErrorBoundary } from "./app/RouteErrorBoundary.jsx";
import { Avatar } from "./auth/ui.jsx";
import Login from "./auth/pages/Login.jsx";
import Register from "./auth/pages/Register.jsx";
import ForgotPassword from "./auth/pages/ForgotPassword.jsx";
import ResetPassword from "./auth/pages/ResetPassword.jsx";
import VerifyEmail from "./auth/pages/VerifyEmail.jsx";
import AcceptInvitation from "./auth/pages/AcceptInvitation.jsx";
import { Loader2 } from "lucide-react";

// Code-split the heavy authenticated bundles (charts, admin) so the public
// marketing + auth pages stay small and fast to load.
const AppRoot = React.lazy(() => import("./app/AppRoot.jsx"));
const AdminApp = React.lazy(() => import("./admin/AdminApp.jsx"));
const Contact = React.lazy(() => import("./pages/Contact.jsx"));
const PublicReport = React.lazy(() => import("./dashboard/PublicReport.jsx"));
const SUPPORT_EMAIL = "aeomirror.support@gmail.com";

/* localStorage key holding only the id of the most recent successful scan.
   The report itself is always re-fetched from the backend (source of truth). */
const LAST_SCAN_KEY = "aeomirror:last_scan_id";
/* Per-browser list of past scans (ids + summary only). The full report is always
   re-fetched from the backend via GET /v1/scan/{id} — this is a convenience index,
   never the source of truth. There is no auth, so history is intentionally local
   to the browser rather than a global server list. */
const HISTORY_KEY = "aeomirror:scan_history";
const HISTORY_LIMIT = 25;

/* Adapt a backend ScanResponse into the shape the UI components consume.
   The dashboard/report components read `report.issues`; the backend contract
   returns `top_issues`. We alias it here rather than changing the API. All other
   fields (ars, domain, url, families, crawlers, rubric_version, scan_id) pass
   through unchanged. */
function adaptReport(resp) {
  if (!resp) return null;
  return { ...resp, issues: resp.issues || resp.top_issues || [] };
}

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; }
  catch { return []; }
}

/* Prepend a scan summary to history (dedup by id, capped). Returns the new list. */
function pushHistory(rep) {
  if (!rep?.scan_id || String(rep.scan_id).startsWith("mock-")) return loadHistory();
  const entry = {
    id: rep.scan_id, url: rep.url, domain: rep.domain, ars: rep.ars,
    rubric_version: rep.rubric_version, date: Date.now(), status: "complete",
  };
  const next = [entry, ...loadHistory().filter((h) => h.id !== rep.scan_id)].slice(0, HISTORY_LIMIT);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)); } catch { /* non-fatal */ }
  return next;
}

/* =====================================================================
   AEOMirror prototype (walking skeleton)
   - Free scanner runs live inside the app as its own layer
   - Single-site Overview dashboard at full fidelity
   - All nav routes to real screens; secondary screens are lighter
   The free scanner and the Overview dashboard render REAL backend results
   (POST /v1/scan, GET /v1/scan/{id}). Paid/secondary screens remain
   illustrative until their endpoints ship (see INTEGRATIONS.md).
   ===================================================================== */

/* NOTE: the dashboard previously rendered from an in-file mock `scan()` generator.
   That local generator has been removed so the dashboard renders ONLY the real
   backend report (see App/AppShell). The demo/offline mock still exists in
   ./api.js, gated behind VITE_ENABLE_MOCK_SCANNER. */

/* ---------- color helpers ---------- */
function band(v) { return v >= 75 ? "good" : v >= 45 ? "warn" : "bad"; }
function bandColor(v) { return v >= 75 ? "var(--good)" : v >= 45 ? "var(--warn)" : "var(--bad)"; }
const STATUS_COLOR = { pass: "var(--good)", warn: "var(--warn)", fail: "var(--bad)" };
const STATUS_LABEL = { pass: "PASS", warn: "WARN", fail: "FAIL" };

/* ---------- gauge ---------- */
function polar(cx, cy, r, deg) { const a = (deg - 180) * Math.PI / 180; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
function arc(cx, cy, r, s, e) {
  const [x1, y1] = polar(cx, cy, r, s); const [x2, y2] = polar(cx, cy, r, e);
  const large = e - s <= 180 ? 0 : 1;
  return `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`;
}
function Gauge({ value, size = 200, label = "AI Readiness Score" }) {
  const cx = size / 2, cy = size / 2, r = size / 2 - 16;
  const va = 180 * (value / 100);
  const col = bandColor(value);
  // Horizontal padding in the viewBox so the end tick labels (0 and 100) never
  // clip against the SVG edge. Only 0 and 100 are shown to keep the dial clean.
  const PAD = 12;
  return (
    <div className="gauge" style={{ width: size }}>
      <svg width={size} height={size / 2 + 30}
           viewBox={`${-PAD} 0 ${size + PAD * 2} ${size / 2 + 30}`}>
        <path d={arc(cx, cy, r, 0, 180)} className="gauge-track" />
        <path d={arc(cx, cy, r, 0, Math.max(0.1, va))} style={{ stroke: col }} className="gauge-val" />
        {[0, 100].map((t) => {
          const [x, y] = polar(cx, cy, r, 180 * (t / 100));
          // Anchor the ends inward (0 = start, 100 = end) and drop them just below
          // the arc so both stay fully inside the padded viewBox.
          return (
            <text key={t} x={x} y={y + 15} textAnchor={t === 0 ? "start" : "end"}
                  className="gauge-tick">{t}</text>
          );
        })}
      </svg>
      <div className="gauge-center">
        <div className="gauge-num" style={{ color: col }}>{value}</div>
        <div className="gauge-den">/ 100</div>
      </div>
      <div className="gauge-label">{label}</div>
    </div>
  );
}

/* ---------- crawler signal strip (the signature element) ---------- */
const CRAWLER_META = {
  // accepts both the backend ids (…_allowed / _ok) and the local mock short ids
  gptbot: { name: "GPTBot", sub: "OpenAI / ChatGPT" },
  gptbot_allowed: { name: "GPTBot", sub: "OpenAI / ChatGPT" },
  claudebot: { name: "ClaudeBot", sub: "Anthropic / Claude" },
  claudebot_allowed: { name: "ClaudeBot", sub: "Anthropic / Claude" },
  perplexitybot: { name: "PerplexityBot", sub: "Perplexity" },
  perplexitybot_allowed: { name: "PerplexityBot", sub: "Perplexity" },
  google_extended: { name: "Google-Extended", sub: "Gemini / AI Mode" },
  google_extended_ok: { name: "Google-Extended", sub: "Gemini / AI Mode" },
};
function CrawlerStrip({ crawlers }) {
  return (
    <div className="strip">
      <div className="strip-head">
        <span>AI crawler visibility</span>
        <span className="strip-sub">can each engine reach this site</span>
      </div>
      <div className="strip-rows">
        {crawlers.map((c) => {
          const m = CRAWLER_META[c.id];
          const ok = c.status === "pass";
          const warn = c.status === "warn";
          return (
            <div key={c.id} className="strip-row">
              <div className="strip-bars">
                {[0, 1, 2].map((i) => (
                  <span key={i} className="bar" style={{
                    height: 8 + i * 5,
                    background: ok ? "var(--good)" : warn && i < 2 ? "var(--warn)" : (!ok && !warn && i < 1) ? "var(--bad)" : "var(--line-2)"
                  }} />
                ))}
              </div>
              <div className="strip-name">
                <div className="strip-n">{m.name}</div>
                <div className="strip-s">{m.sub}</div>
              </div>
              <div className="strip-status" style={{ color: STATUS_COLOR[c.status] }}>
                {ok ? "Reachable" : warn ? "Undeclared" : "Blocked"}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- signal bars (per-signal score breakdown) ----------
   Reads the same 10 signals that produce the headline score, so the breakdown and
   the gauge always agree (previously this showed the legacy 6-family ARS view whose
   totals diverged from the headline). */
function SignalBars({ sections = [] }) {
  return (
    <div className="fam">
      {sections.map((s) => {
        const v = Math.max(0, Math.min(100, s.score ?? 0));
        return (
          <div key={s.id} className="fam-row">
            <div className="fam-label">{s.label}</div>
            <div className="fam-track">
              <div className="fam-fill" style={{ width: `${v}%`, background: bandColor(v) }} />
            </div>
            <div className="fam-num mono" style={{ color: bandColor(v) }}>{s.score}<span className="fam-den">/100</span></div>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- issue list ----------
   Two modes:
   - locked (signed-out): lock icons, fix hint teased, no expansion.
   - expandable (signed-in, any plan): no locks; each row is clickable and expands
     to reveal the finding + how to fix it, like the dashboard scan detail. */
function IssueList({ issues, locked = false, max = 5 }) {
  const [open, setOpen] = useState({});
  const list = issues.slice(0, max);
  const toggle = (i) => setOpen((o) => ({ ...o, [i]: !o[i] }));

  if (locked) {
    return (
      <div className="issues">
        {list.map((c, i) => (
          <div key={i} className="issue">
            <span className="issue-chip" style={{ color: STATUS_COLOR[c.status], borderColor: STATUS_COLOR[c.status] }}>
              {STATUS_LABEL[c.status]}
            </span>
            <div className="issue-body">
              <div className="issue-label">{c.label} <span className="issue-fam">{c.family}</span></div>
              <div className="issue-fix"><Lock size={11} /> {c.fix || c.fix_hint}</div>
            </div>
            <button className="issue-btn locked"><Lock size={12} /> Fix</button>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="issues">
      {list.map((c, i) => {
        const isOpen = !!open[i];
        const fix = c.fix || c.fix_hint;
        return (
          <div key={i} className={`issue expandable${isOpen ? " open" : ""}`}>
            <button className="issue-main" onClick={() => toggle(i)} aria-expanded={isOpen}>
              <span className="issue-chip" style={{ color: STATUS_COLOR[c.status], borderColor: STATUS_COLOR[c.status] }}>
                {STATUS_LABEL[c.status]}
              </span>
              <div className="issue-body">
                <div className="issue-label">{c.label} <span className="issue-fam">{c.family}</span></div>
                <div className="issue-fix"><Wrench size={11} /> {isOpen ? "How to fix ↓" : "See the fix"}</div>
              </div>
              <ChevronDown size={15} className="issue-chev" style={{ transform: isOpen ? "rotate(180deg)" : "none" }} />
            </button>
            {isOpen && (
              <div className="issue-detail">
                {c.detail && <p className="issue-detail-finding">{c.detail}</p>}
                {fix && (
                  <div className="issue-detail-fix">
                    <div className="issue-detail-h"><Wrench size={11} /> Recommended fix</div>
                    <p>{fix}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


/* ============================= FREE SCANNER LAYER ============================= */
function FreeScanner({ compact, onFull, onScanComplete }) {
  const { isAuthenticated } = useAuth();
  // Plan + upgrade modal come from the shared subscription context (present only when
  // signed in — MarketingRoot wraps signed-in visitors in <UpgradeProvider>). Signed-out
  // visitors get the DEFAULT (plan null, openUpgrade no-op) and never reach that branch.
  const { plan, openUpgrade } = useUpgrade();
  const isPro = plan === "pro";
  const [url, setUrl] = useState("");
  const [state, setState] = useState("idle"); // idle | scanning | done
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [gate402, setGate402] = useState(null);   // scan-quota message (signed-in, out of scans)
  // Bulk scanning is a signed-in feature; anonymous stays single-page only.
  const [tab, setTab] = useState("single");   // "single" | "bulk"
  const inFlight = useRef(false); // synchronous guard against duplicate submits

  useEffect(() => {
    if (!isAuthenticated) setTab("single");
  }, [isAuthenticated]);

  // Send a signed-out visitor into Register, preserving the URL so it auto-runs after
  // they sign up.
  const toRegister = (theUrl) => { setPendingScan(theUrl, "single"); navigate("/register"); };

  const run = async (overrideUrl) => {
    // onClick passes a DOM event as the first arg — only honor a STRING override so the
    // event is never mistaken for a URL. (Auto-run after auth calls run(url).)
    const theUrl = (typeof overrideUrl === "string" ? overrideUrl : url).trim();
    if (!theUrl) return;
    if (inFlight.current) return; // guard against duplicate submits
    // First scan is FREE with no login (tracked per browser). Once used, the next scan
    // opens the register flow — the pending URL auto-runs after they sign up.
    if (!isAuthenticated && freeScanUsed()) { toRegister(theUrl); return; }
    inFlight.current = true;
    setState("scanning");
    setError(null); setGate402(null);
    try {
      // real backend call — no silent mock fallback; a failure surfaces below
      const [result] = await Promise.all([
        scanUrl(theUrl),
        new Promise((r) => setTimeout(r, 1200)), // keep the scan animation legible
      ]);
      const adapted = adaptReport(result);
      setReport(adapted);
      setState("done");
      if (!isAuthenticated) markFreeScanUsed();   // consume the one free scan for this browser
      onScanComplete?.(adapted); // lift the real report to the app root
    } catch (err) {
      // Out of scan jobs for the month (signed-in) → show the upgrade gate.
      if (err && err.code === 402) { setGate402(err.message); setState("idle"); return; }
      // A stale/invalid session on an anonymous scan → send them to register.
      if (err && err.code === 401 && !isAuthenticated) { toRegister(theUrl); return; }
      // Show a clear, safe message. Never fall back to fake scores.
      setError(err instanceof ScanError ? err.message : "Scan failed. Please try again.");
      setState("idle");
    } finally {
      inFlight.current = false;
    }
  };

  // After registration/login the pending URL is auto-scanned without re-entry.
  useEffect(() => {
    if (!isAuthenticated) return;
    const pending = takePendingScan();
    if (pending?.url) { setUrl(pending.url); run(pending.url); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated]);

  // The AI Readiness Score = the honest 10-signal aggregate (falls back to the
  // legacy family score only for pre-signal reports). For a site scan the headline
  // is the site average across the crawled pages. Same number the breakdown below.
  const headline = report ? (report.overall_score ?? report.ars) : null;

  // Signed-in CTAs: open this scan's full detail in the dashboard, and (Pro) export the PDF.
  const viewFullReport = () => {
    if (report?.scan_id) navigate(`/app/scans/${encodeURIComponent(report.scan_id)}`);
  };
  const downloadPdf = async () => {
    if (!report?.scan_id || pdfBusy) return;
    setPdfBusy(true); setError(null);
    try { await downloadReport(report.scan_id, "pdf"); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not download the PDF. Please try again."); }
    finally { setPdfBusy(false); }
  };

  return (
    <div className={compact ? "scanner compact" : "scanner"}>
      {isAuthenticated && (
        <div className="scan-mode" role="group" aria-label="Scan type">
          <button type="button" className={tab === "single" ? "on" : ""} onClick={() => setTab("single")}>Single page</button>
          <button type="button" className={tab === "bulk" ? "on" : ""} onClick={() => setTab("bulk")}>Bulk — up to 50 URLs</button>
        </div>
      )}

      {tab === "single" ? (
        <>
          <div className="scan-input">
            <Globe size={16} className="scan-globe" />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="Paste any website URL"
              spellCheck={false}
            />
            <button className="scan-go" onClick={() => run()} disabled={state === "scanning"}>
              {state === "scanning" ? <><ScanLine size={15} className="spin-slow" /> Scanning</> : <>Scan free <ArrowRight size={15} /></>}
            </button>
          </div>
          <div className="scan-hint">Checks crawler access, indexability, schema, content structure, internal linking, performance and freshness across 10 signals. Free account — 1 scan a month, plus a one-time 50-URL bulk trial.</div>
        </>
      ) : (
        <BulkScanPanel onGate={setGate402} />
      )}

      {error && (
        <div className="scan-error" role="alert">
          <AlertTriangle size={14} /> <span>{error}</span>
        </div>
      )}

      {gate402 && (
        <div className="scan-error scan-gate" role="alert">
          <AlertTriangle size={14} /> <span>{gate402}</span>
          <button className="scan-gate-up" onClick={() => navigate("/app")}>Upgrade to Pro <ArrowRight size={13} /></button>
        </div>
      )}

      {state === "scanning" && (
        <div className="scan-progress">
          <div className="scan-line" />
          <div className="scan-steps mono">
            <span>fetch</span><span>render</span><span>parse robots.txt</span><span>read schema</span><span>score</span>
          </div>
        </div>
      )}

      {state === "done" && report && (
        <div className="report">
          <div className="report-top">
            <div className="report-domain">
              <div className="report-label mono">RESULT</div>
              <div className="report-url">{report.domain}</div>
              <span className="report-badge" style={{ color: bandColor(headline), borderColor: bandColor(headline) }}>
                {band(headline) === "good" ? "AI ready" : band(headline) === "warn" ? "Needs work" : "At risk"}
              </span>
            </div>
            <Gauge value={headline} size={168} />
          </div>

          <div className="report-grid">
            <div className="panel">
              <div className="panel-h">Score by signal <span className="panel-sub">10 checks</span></div>
              <SignalBars sections={report.sections} />
            </div>
            <CrawlerStrip crawlers={report.crawlers} />
          </div>

          <div className="panel">
            <div className="panel-h">Top issues
              {!isAuthenticated && <span className="panel-sub">fixes unlock with a free account</span>}
            </div>
            <IssueList issues={report.issues} locked={!isAuthenticated} max={5} />
          </div>

          {!isAuthenticated ? (
            <div className="report-cta">
              <div>
                <div className="cta-title">Unlock the full report and the fixes</div>
                <div className="cta-sub">Generated schema, llms.txt, an FAQ block, and a re-scan that proves the score moved.</div>
              </div>
              <button className="cta-btn" onClick={onFull}>Create free account <ArrowRight size={15} /></button>
            </div>
          ) : (
            <div className="report-cta">
              <div>
                <div className="cta-title">Your full report is ready</div>
                <div className="cta-sub">
                  {isPro
                    ? "Open the full breakdown in your dashboard, or download the PDF to share."
                    : "Open the full breakdown in your dashboard — every signal, fix and evidence detail."}
                </div>
              </div>
              <div className="report-cta-actions">
                <button className="cta-btn" onClick={viewFullReport}>View full report <ArrowRight size={15} /></button>
                {isPro ? (
                  <button className="cta-link" onClick={downloadPdf} disabled={pdfBusy}>
                    <Download size={14} /> {pdfBusy ? "Preparing…" : "Download PDF"}
                  </button>
                ) : (
                  <button className="cta-link" onClick={() => openUpgrade("report", { scanId: report.scan_id })}>
                    <Sparkles size={14} /> Unlock exports &amp; AI-written report
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}

    </div>
  );
}

/* Bulk scan (signed-in): paste up to 50 URLs or upload a CSV/XLSX. Submits to the
   background bulk endpoint and navigates to the live progress view (202). */
const BULK_MAX = 50;
// Mirror of the backend `bulk_upload_max_bytes` cap (config.py) so the user gets
// instant feedback instead of a round-trip 413. The server check stays authoritative.
const BULK_UPLOAD_MAX_BYTES = 2_000_000;
const BULK_ACCEPT = ".csv,.xlsx,.txt,.json,.jsonl,.tsv";
const SKIP_LABEL = {
  invalid: "Invalid URL", duplicate: "Duplicate", ssrf_blocked: "Blocked (unsafe address)",
  over_limit: `Beyond the ${BULK_MAX}-URL limit`,
};

function BulkScanPanel({ onGate }) {
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [skipped, setSkipped] = useState(null);
  const [trial, setTrial] = useState({ available: false, isPro: false });
  const fileRef = useRef(null);

  useEffect(() => {
    let ok = true;
    billing.subscription()
      .then((s) => { if (ok) setTrial({ available: !!s?.usage?.bulk_trial?.available, isPro: s?.plan === "pro" }); })
      .catch(() => {});
    return () => { ok = false; };
  }, []);

  const lines = text.split(/[\n,]+/).map((l) => l.trim()).filter(Boolean);
  const count = lines.length;
  const over = count > BULK_MAX;

  const submit = async () => {
    if (busy) return;
    setErr(null); setSkipped(null);
    if (!file && count === 0) { setErr("Paste at least one URL, or upload a .csv, .xlsx, .txt or .json file."); return; }
    setBusy(true);
    try {
      const res = file ? await bulkScanFile(file) : await bulkScanUrls(lines);
      navigate(`/app/scans/${res.scan_id}`);   // 202 → live progress view
    } catch (e) {
      if (e && e.code === 402) { onGate?.(e.message); }
      else {
        setErr(e instanceof ScanError ? e.message : "Bulk scan failed. Please try again.");
        if (e?.summary?.skipped?.length) setSkipped(e.summary.skipped);
      }
    } finally { setBusy(false); }
  };

  const chooseFile = (f) => {
    if (!f) { setFile(null); return; }
    if (f.size > BULK_UPLOAD_MAX_BYTES) {
      // Instant feedback; the server enforces the same cap authoritatively (413).
      setErr(`That file is too large (max ${BULK_UPLOAD_MAX_BYTES / 1_000_000} MB). `
             + "Split it up or paste fewer URLs.");
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    setErr(null);
    setFile(f);
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) chooseFile(f);
  };

  return (
    <div className="bulk">
      {!trial.isPro && trial.available && (
        <div className="bulk-badge"><Sparkles size={12} /> 1 free bulk trial</div>
      )}
      <textarea className="bulk-text" rows={5} spellCheck={false}
                placeholder={"One URL per line…\nhttps://example.com/\nhttps://example.com/pricing"}
                value={text} onChange={(e) => setText(e.target.value)} />
      <div
        className={`bulk-drop${dragOver ? " over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
        role="button" tabIndex={0}
      >
        <FileText size={15} />
        <span>{file ? file.name : "Drop a .csv, .xlsx, .txt or .json here, or click to browse"}</span>
        {file && <button className="bulk-clear" onClick={(e) => { e.stopPropagation(); setFile(null); if (fileRef.current) fileRef.current.value = ""; }}>Clear</button>}
        <input ref={fileRef} type="file" accept={BULK_ACCEPT} hidden
               onChange={(e) => chooseFile(e.target.files?.[0] || null)} />
      </div>

      <div className="bulk-foot">
        <span className={`bulk-count${over ? " over" : ""}`}>
          {file ? "URLs read from file" : `${count} / ${BULK_MAX} URLs`}{over ? " · extras skipped" : ""}
        </span>
        <button className="scan-go" onClick={submit} disabled={busy}>
          {busy ? <><ScanLine size={15} className="spin-slow" /> Starting…</>
            : <>Scan {file ? "file" : `${Math.min(count, BULK_MAX)} URL${count === 1 ? "" : "s"}`} <ArrowRight size={15} /></>}
        </button>
      </div>

      {err && <div className="scan-error" role="alert"><AlertTriangle size={14} /> <span>{err}</span></div>}
      {skipped?.length > 0 && <SkippedList skipped={skipped} />}
      <div className="scan-hint">Bulk scans run in the background — we'll take you to a live progress view.</div>
    </div>
  );
}

function SkippedList({ skipped }) {
  return (
    <div className="bulk-skipped">
      <div className="bulk-skipped-h">{skipped.length} URL{skipped.length === 1 ? "" : "s"} skipped</div>
      {skipped.slice(0, 8).map((s, i) => (
        <div key={i} className="bulk-skipped-row">
          <span className="bulk-skipped-url">{s.url}</span>
          <span className="bulk-skipped-reason">{SKIP_LABEL[s.reason] || s.reason}</span>
        </div>
      ))}
      {skipped.length > 8 && <div className="d-dim" style={{ fontSize: 11.5, marginTop: 4 }}>+{skipped.length - 8} more</div>}
    </div>
  );
}

/* ============================= MARKETING VIEW ============================= */
function Marketing({ onFull, onScanComplete }) {
  return (
    <div className="mkt">
      <div className="mkt-hero">
        <div className="eyebrow mono"><Radar size={13} /> AEOMIRROR / AI VISIBILITY SCANNER</div>
        <h1>Is AI recommending you,<br />or your competitor?</h1>
        <p className="mkt-lede">
          Search rankings no longer predict AI answers. AEOMirror scans any site and shows exactly what
          ChatGPT, Claude, Gemini and Perplexity can reach, read, and cite. Then it hands you the fixes.
        </p>
        <FreeScanner onFull={onFull} onScanComplete={onScanComplete} />
      </div>

      <div className="mkt-strip">
        <div className="mkt-stat"><div className="mono big">10</div><div>signals scored — free scan, no AI cost</div></div>
        <div className="mkt-stat"><div className="mono big">4</div><div>AI crawlers checked per scan</div></div>
        <div className="mkt-stat"><div className="mono big">AI</div><div>written reports & content insights on Pro</div></div>
      </div>
    </div>
  );
}

/* Legacy AppShell + mock screens removed in Phase 4 (replaced by ./dashboard/Dashboard.jsx). */

/* ============================= AUTH-AWARE MARKETING HEADER ============================= */
function TopBar() {
  const { ready, isAuthenticated, user } = useAuth();
  return (
    <div className="topbar-auth">
      <div className="topbar-brand" onClick={() => navigate("/")} style={{ cursor: "pointer" }}><Radar size={17} /> AEOMirror</div>
      <div className="topbar-actions">
        <button className="tb-btn tb-link" onClick={() => navigate("/contact")}>Contact</button>
        {!ready ? null : isAuthenticated ? (
          <>
            <button className="tb-btn tb-primary" onClick={() => navigate("/app")}>
              <LayoutDashboard size={15} /> Dashboard
            </button>
            <button className="tb-avatar" onClick={() => navigate("/app")} title={user?.name}>
              <Avatar user={user} size={30} />
            </button>
          </>
        ) : (
          <>
            <button className="tb-btn" onClick={() => navigate("/login")}>Log in</button>
            <button className="tb-btn tb-primary" onClick={() => navigate("/register")}>Sign up free</button>
          </>
        )}
      </div>
    </div>
  );
}

function MarketingRoot() {
  const { isAuthenticated } = useAuth();
  const body = (
    <>
      <TopBar />
      <Marketing onFull={() => navigate(isAuthenticated ? "/app" : "/register")} onScanComplete={() => {}} />
      <SiteFooter />
    </>
  );
  // Signed-in visitors get the shared subscription context (plan + usage) and the
  // UpgradeModal, so the result card can render its plan-aware CTA and open the
  // paywall. Signed-out visitors skip it (no authed billing call, no modal needed).
  return isAuthenticated ? <UpgradeProvider>{body}</UpgradeProvider> : body;
}

/* Public /contact page shell: same marketing chrome (top bar + footer). */
function ContactRoot() {
  return (
    <>
      <TopBar />
      <Suspense fallback={<FullScreenLoader />}><Contact /></Suspense>
      <SiteFooter />
    </>
  );
}

/* Website footer with a Contact/Support section. */
function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <div className="footer-brand">
          <div className="footer-logo"><Radar size={16} /> AEOMirror</div>
          <p className="footer-tag">See what AI can reach, read and cite on any site — then fix it.</p>
        </div>
        <div className="footer-help">
          <div className="footer-help-h"><LifeBuoy size={15} /> Need help?</div>
          <a className="footer-mail" href={`mailto:${SUPPORT_EMAIL}`}>
            <Mail size={13} /> {SUPPORT_EMAIL}
          </a>
          <button className="footer-cta" onClick={() => navigate("/contact")}>
            Contact us <ArrowRight size={14} />
          </button>
        </div>
      </div>
      <div className="footer-bottom">© {new Date().getFullYear()} AEOMirror. All rights reserved.</div>
    </footer>
  );
}

function FullScreenLoader() {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", color: "var(--txt-mid)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Loader2 size={18} className="spin-slow" /> Loading…
      </div>
    </div>
  );
}

/* Public shared report (/r/:token) — no dashboard shell, no auth. AuthProvider skips its
   bootstrap for this path, so a signed-out visitor fires no auth call here. */
function PublicReportRoute() {
  const { token } = useParams();
  return (
    <Suspense fallback={<FullScreenLoader />}>
      <PublicReport token={token} />
    </Suspense>
  );
}

/* ============================= ROUTER ============================= */
function AppRouter() {
  return (
    <Routes>
      {/* public shared report — outside the app shell + auth */}
      <Route path="/r/:token" element={<PublicReportRoute />} />

      {/* public marketing + support */}
      <Route path="/" element={<MarketingRoot />} />
      <Route path="/contact" element={<ContactRoot />} />

      {/* public auth pages (paths unchanged — external links point here) */}
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/accept-invitation" element={<AcceptInvitation />} />

      {/* internal admin platform (platform admins only) */}
      <Route path="/admin/*" element={
        <RequireAdmin>
          <Suspense fallback={<FullScreenLoader />}><AdminApp /></Suspense>
        </RequireAdmin>
      } />

      {/* the authenticated app — a lazy chunk that guards + lays out its own nested routes.
          The boundary catches a failed chunk load (stale tab after a deploy) and recovers
          with a single guarded reload; see RouteErrorBoundary. */}
      <Route path="/app/*" element={
        <RouteErrorBoundary>
          <Suspense fallback={<FullScreenLoader />}><AppRoot /></Suspense>
        </RouteErrorBoundary>
      } />

      {/* unknown top-level path: keep the current behaviour (marketing home) */}
      <Route path="*" element={<MarketingRoot />} />
    </Routes>
  );
}

/* ============================= ROOT ============================= */
export default function App() {
  // Scans are persisted server-side and now scoped to the signed-in organization;
  // the dashboard loads its own data from GET /api/scans + /api/dashboard.
  return (
    <div className="root">
      <style>{CSS}</style>
      <RouterBridge />
      <AppRouter />
    </div>
  );
}

/* ============================= STYLES ============================= */
const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@500;600;700;800&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

html,body{margin:0;padding:0;background:#0B0F14}

.root{
  --bg:#0B0F14; --panel:#111922; --panel-2:#16202B; --line:#1E2833; --line-2:#2B3947;
  --txt:#E9EEF2; --txt-mid:#8A97A3; --txt-dim:#5A6772;
  --accent:#34D3E0; --accent-dim:rgba(52,211,224,.12);
  --good:#43C08A; --warn:#E6A94A; --bad:#E5615B;
  background:var(--bg); color:var(--txt);
  font-family:'Inter',system-ui,sans-serif; min-height:100vh; font-size:14px;
}
.root *{box-sizing:border-box}
.mono{font-family:'IBM Plex Mono',monospace}
h1,h2,h3,.brand,.topbar-title,.gauge-num,.cta-title{font-family:'Hanken Grotesk',sans-serif}

/* switcher */
.switcher{position:sticky;top:0;z-index:40;display:flex;gap:4px;justify-content:center;
  padding:8px;background:var(--bg);border-bottom:1px solid var(--line)}
.switcher button{background:transparent;border:1px solid transparent;color:var(--txt-mid);
  padding:6px 14px;border-radius:7px;font-size:12.5px;cursor:pointer;font-weight:500}
.switcher button.on{background:var(--panel-2);color:var(--txt);border-color:var(--line)}

/* auth-aware marketing top bar — solid (not translucent) so scrolled hero content,
   the stats strip and the footer never ghost through it behind the scan card. */
.topbar-auth{position:sticky;top:0;z-index:40;display:flex;justify-content:space-between;align-items:center;
  padding:12px 22px;background:var(--bg);border-bottom:1px solid var(--line)}
.topbar-brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px;font-family:'Hanken Grotesk',sans-serif}
.topbar-brand svg{color:var(--accent)}
.topbar-actions{display:flex;align-items:center;gap:10px}
.tb-btn{display:inline-flex;align-items:center;gap:7px;background:transparent;border:1px solid var(--line-2);
  color:var(--txt);padding:8px 14px;border-radius:8px;font-size:13px;font-weight:500;cursor:pointer;font-family:'Inter',sans-serif}
.tb-btn:hover{border-color:var(--txt-dim)}
.tb-primary{background:var(--accent);color:#04222a;border-color:transparent;font-weight:600}
.tb-link{border-color:transparent;color:var(--txt-mid)}
.tb-link:hover{border-color:transparent;color:var(--txt)}
.tb-avatar{background:none;border:none;cursor:pointer;padding:0;display:flex}

/* site footer */
.site-footer{border-top:1px solid var(--line);background:var(--panel);margin-top:40px}
.footer-inner{max-width:1080px;margin:0 auto;padding:34px 24px 26px;display:flex;justify-content:space-between;gap:32px;flex-wrap:wrap}
.footer-brand{max-width:360px}
.footer-logo{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px;font-family:'Hanken Grotesk',sans-serif}
.footer-logo svg{color:var(--accent)}
.footer-tag{color:var(--txt-mid);font-size:13px;line-height:1.55;margin:10px 0 0}
.footer-help{display:flex;flex-direction:column;gap:10px;align-items:flex-start}
.footer-help-h{display:flex;align-items:center;gap:8px;font-weight:600;font-size:14px}
.footer-help-h svg{color:var(--accent)}
.footer-mail{display:inline-flex;align-items:center;gap:7px;color:var(--txt-mid);text-decoration:none;font-size:13.5px}
.footer-mail:hover{color:var(--accent)}
.footer-cta{display:inline-flex;align-items:center;gap:7px;background:var(--accent);color:#04222a;border:none;
  padding:9px 15px;border-radius:8px;font-weight:600;font-size:13px;cursor:pointer;font-family:'Inter',sans-serif}
.footer-bottom{border-top:1px solid var(--line);color:var(--txt-dim);font-size:12px;text-align:center;padding:16px 24px}

/* marketing */
.mkt{max-width:1080px;margin:0 auto;padding:56px 24px 80px;position:relative;z-index:0}
.mkt-hero{position:relative;z-index:1}
.eyebrow{display:inline-flex;align-items:center;gap:7px;color:var(--accent);font-size:11px;
  letter-spacing:.12em;margin-bottom:22px;border:1px solid var(--line);padding:5px 11px;border-radius:20px}
.mkt-hero h1{font-size:52px;line-height:1.03;font-weight:800;letter-spacing:-.02em;margin:0 0 20px}
.mkt-lede{color:var(--txt-mid);font-size:16.5px;line-height:1.55;max-width:640px;margin:0 0 34px}
.mkt-strip{position:relative;z-index:1;display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:52px;
  padding-top:34px;border-top:1px solid var(--line)}
.mkt-stat{color:var(--txt-mid);font-size:13px}
.mkt-stat .big{font-size:30px;color:var(--txt);font-weight:600;margin-bottom:4px}

/* scanner */
.scanner{max-width:720px}
.scan-input{display:flex;align-items:center;gap:10px;background:var(--panel);
  border:1px solid var(--line-2);border-radius:12px;padding:8px 8px 8px 14px}
.scan-input:focus-within{border-color:var(--accent)}
.scan-globe{color:var(--txt-dim);flex:none}
.scan-input input{flex:1;background:transparent;border:none;outline:none;color:var(--txt);
  font-size:15px;font-family:'IBM Plex Mono',monospace}
.scan-input input::placeholder{color:var(--txt-dim)}
.scan-go{display:inline-flex;align-items:center;gap:7px;background:var(--accent);color:#04222a;
  border:none;padding:10px 16px;border-radius:8px;font-weight:600;font-size:13.5px;cursor:pointer;white-space:nowrap}
.scan-go:disabled{opacity:.7;cursor:default}
.scan-hint{color:var(--txt-dim);font-size:12px;margin-top:10px;line-height:1.5}
.scan-mode{display:inline-flex;gap:4px;margin-top:12px;padding:3px;border:1px solid var(--line);border-radius:9px;background:var(--panel)}
.scan-mode button{background:transparent;border:none;color:var(--txt-mid);padding:6px 12px;border-radius:6px;
  font-size:12.5px;cursor:pointer;font-family:'Inter',sans-serif;white-space:nowrap}
.scan-mode button.on{background:var(--accent-dim);color:var(--accent);font-weight:600}
/* bulk scan panel (homepage, signed-in) */
.bulk{margin-top:14px;text-align:left}
.bulk-badge{display:inline-flex;align-items:center;gap:5px;margin-bottom:10px;font-size:11.5px;font-weight:600;
  color:var(--accent);background:var(--accent-dim);border:1px solid var(--accent);border-radius:20px;padding:3px 10px}
.bulk-text{width:100%;box-sizing:border-box;background:var(--panel);border:1px solid var(--line-2);border-radius:10px;
  color:var(--txt);font-size:13px;font-family:'IBM Plex Mono',monospace;padding:11px 13px;resize:vertical;line-height:1.6;outline:none}
.bulk-text:focus{border-color:var(--accent)}
.bulk-drop{display:flex;align-items:center;gap:9px;margin-top:10px;padding:11px 13px;border:1px dashed var(--line-2);
  border-radius:10px;color:var(--txt-mid);font-size:12.5px;cursor:pointer;background:var(--panel)}
.bulk-drop.over{border-color:var(--accent);background:var(--accent-dim)}
.bulk-drop svg{flex:none;color:var(--txt-dim)}
.bulk-drop span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bulk-clear{background:none;border:none;color:var(--txt-dim);font-size:12px;cursor:pointer;text-decoration:underline}
.bulk-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}
.bulk-count{font-size:12px;color:var(--txt-mid);font-family:'IBM Plex Mono',monospace}
.bulk-count.over{color:var(--warn)}
.bulk-skipped{margin-top:12px;border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--panel)}
.bulk-skipped-h{font-size:12px;font-weight:600;color:var(--warn);margin-bottom:6px}
.bulk-skipped-row{display:flex;justify-content:space-between;gap:12px;font-size:11.5px;padding:2px 0}
.bulk-skipped-url{color:var(--txt-mid);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:'IBM Plex Mono',monospace}
.bulk-skipped-reason{color:var(--txt-dim);flex:none}
.scan-error{display:flex;align-items:center;gap:8px;margin-top:14px;padding:10px 12px;border:1px solid var(--bad);
  border-radius:9px;background:rgba(229,97,91,.10);color:var(--bad);font-size:12.5px}
.scan-error svg{flex:none}
/* scan-quota (402) gate: amber, with an inline upgrade CTA */
.scan-gate{border-color:var(--warn);background:rgba(230,169,74,.10);color:var(--warn)}
.scan-gate-up{margin-left:auto;display:inline-flex;align-items:center;gap:5px;background:var(--warn);color:#2a1e05;
  border:none;padding:6px 12px;border-radius:7px;font-weight:600;font-size:12px;cursor:pointer;white-space:nowrap}
.score-band{display:inline-flex;align-items:center;font-weight:700;font-size:12.5px;font-family:'IBM Plex Mono';
  border:1px solid;padding:3px 10px;border-radius:20px;margin-bottom:2px}

.scan-progress{margin-top:24px}
.scan-line{height:2px;background:linear-gradient(90deg,transparent,var(--accent),transparent);
  background-size:40% 100%;animation:sweep 1.3s linear infinite;border-radius:2px}
@keyframes sweep{0%{background-position:-40% 0}100%{background-position:140% 0}}
.scan-steps{display:flex;gap:16px;margin-top:12px;color:var(--txt-dim);font-size:11px;flex-wrap:wrap}
.spin-slow{animation:spin 1.4s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* report */
.report{margin-top:30px;border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:22px}
.report-top{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap}
.report-label{font-size:10px;letter-spacing:.14em;color:var(--txt-dim)}
.report-url{font-size:22px;font-weight:700;font-family:'Hanken Grotesk';margin:3px 0 8px}
.report-badge{font-size:11px;border:1px solid;padding:3px 9px;border-radius:20px;font-weight:600}
.report-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:22px 0}
.report-cta{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:20px;
  padding:18px;border:1px solid var(--accent);border-radius:12px;background:var(--accent-dim);flex-wrap:wrap}
.cta-title{font-size:16px;font-weight:700;margin-bottom:4px}
.cta-sub{color:var(--txt-mid);font-size:13px;max-width:420px}
.cta-btn{display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#04222a;
  border:none;padding:11px 18px;border-radius:9px;font-weight:600;cursor:pointer;font-size:13.5px;white-space:nowrap}
.cta-btn.small{padding:9px 14px;font-size:13px}
.report-cta-actions{display:flex;flex-direction:column;align-items:flex-end;gap:8px}
.cta-link{display:inline-flex;align-items:center;gap:6px;background:none;border:none;color:var(--accent);
  cursor:pointer;font-size:12.5px;font-weight:500;padding:0;font-family:'Inter',sans-serif}
.cta-link:hover{text-decoration:underline}
.cta-link:disabled{opacity:.6;cursor:default;text-decoration:none}

/* gauge */
.gauge{position:relative;text-align:center;flex:none}
.gauge-track{fill:none;stroke:var(--line);stroke-width:12;stroke-linecap:round}
.gauge-val{fill:none;stroke-width:12;stroke-linecap:round;transition:all .9s cubic-bezier(.2,.7,.2,1)}
.gauge-tick{fill:var(--txt-dim);font-size:9px;font-family:'IBM Plex Mono'}
.gauge-center{position:absolute;top:46%;left:0;right:0;transform:translateY(-50%)}
.gauge-num{font-size:46px;font-weight:800;line-height:1;font-family:'Hanken Grotesk'}
.gauge-den{color:var(--txt-dim);font-size:12px;font-family:'IBM Plex Mono';margin-top:2px}
.gauge-label{color:var(--txt-mid);font-size:12px;margin-top:2px;letter-spacing:.02em}

/* panels */
.panel{border:1px solid var(--line);border-radius:12px;padding:16px;background:var(--panel-2)}
.panel-h{font-size:13px;font-weight:600;margin-bottom:14px;display:flex;gap:8px;align-items:baseline}
.panel-sub,.card-sub{color:var(--txt-dim);font-size:11px;font-weight:400}

/* family bars */
.fam{display:flex;flex-direction:column;gap:11px}
.fam-row{display:grid;grid-template-columns:120px 1fr 54px;align-items:center;gap:12px}
.fam-label{font-size:12.5px;color:var(--txt-mid)}
.fam-track{height:7px;background:var(--line);border-radius:4px;overflow:hidden}
.fam-fill{height:100%;border-radius:4px;transition:width .8s ease}
.fam-num{font-size:12px;text-align:right;color:var(--txt)}
.fam-den{color:var(--txt-dim)}

/* crawler strip */
.strip{border:1px solid var(--line);border-radius:12px;padding:16px;background:var(--panel-2)}
.strip-head{display:flex;flex-direction:column;margin-bottom:14px}
.strip-head>span:first-child{font-size:13px;font-weight:600}
.strip-sub{color:var(--txt-dim);font-size:11px;margin-top:2px}
.strip-rows{display:flex;flex-direction:column;gap:10px}
.strip-row{display:grid;grid-template-columns:34px 1fr auto;align-items:center;gap:12px}
.strip-bars{display:flex;align-items:flex-end;gap:3px;height:20px}
.strip-bars .bar{width:4px;border-radius:1px;transition:background .4s}
.strip-n{font-size:12.5px;font-weight:500}
.strip-s{font-size:10.5px;color:var(--txt-dim)}
.strip-status{font-size:11.5px;font-weight:600;font-family:'IBM Plex Mono'}

/* issues */
.issues{display:flex;flex-direction:column;gap:8px}
.issue{display:flex;align-items:center;gap:12px;padding:11px 12px;border:1px solid var(--line);
  border-radius:9px;background:var(--panel)}
.issue-chip{font-size:9.5px;font-weight:700;border:1px solid;border-radius:5px;padding:2px 6px;
  font-family:'IBM Plex Mono';flex:none;width:44px;text-align:center}
.issue-body{flex:1;min-width:0}
.issue-label{font-size:13px;font-weight:500}
.issue-fam{color:var(--txt-dim);font-size:10.5px;font-weight:400;margin-left:7px}
.issue-fix{color:var(--txt-mid);font-size:11.5px;margin-top:3px;display:flex;align-items:center;gap:5px}
.issue-btn{display:inline-flex;align-items:center;gap:5px;background:var(--panel-2);color:var(--txt);
  border:1px solid var(--line-2);padding:6px 11px;border-radius:7px;font-size:12px;cursor:pointer;flex:none;font-weight:500}
.issue-btn.locked{color:var(--txt-mid)}
/* expandable issue (signed-in): whole row is a toggle, detail drops below */
.issue.expandable{display:block;padding:0;overflow:hidden}
.issue-main{width:100%;display:flex;align-items:center;gap:12px;padding:11px 12px;
  background:transparent;border:none;color:var(--txt);cursor:pointer;text-align:left;font-family:'Inter',sans-serif}
.issue-main:hover,.issue.expandable.open .issue-main{background:var(--panel-2)}
.issue-chev{color:var(--txt-dim);flex:none;transition:transform .2s}
.issue-detail{padding:2px 12px 13px 12px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:10px}
.issue-detail-finding{color:var(--txt-mid);font-size:12px;line-height:1.5;margin:10px 0 0}
.issue-detail-h{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--txt-dim);
  display:flex;align-items:center;gap:6px;margin-bottom:5px}
.issue-detail-h svg{color:var(--accent)}
.issue-detail-fix p{color:var(--txt-mid);font-size:12.5px;line-height:1.5;margin:0}

/* signal report (Phase 3) */
.sig{margin-top:16px}
.sig .panel-h{justify-content:flex-start}
.sig-overall{margin-left:auto;font-size:12.5px;font-weight:600}
.sig-list{display:flex;flex-direction:column;gap:8px}
.sig-card{border:1px solid var(--line);border-radius:10px;background:var(--panel);overflow:hidden}
.sig-head{width:100%;display:flex;align-items:center;gap:10px;background:transparent;border:none;
  color:var(--txt);padding:11px 13px;cursor:pointer;text-align:left;font-size:13px}
.sig-head:hover{background:var(--panel-2)}
.sig-dot{width:8px;height:8px;border-radius:50%;flex:none}
.sig-name{flex:1;font-weight:500}
.sig-chip{font-size:9.5px;font-weight:700;border:1px solid;border-radius:5px;padding:2px 6px;
  font-family:'IBM Plex Mono';width:44px;text-align:center;flex:none}
.sig-score{font-size:13px;font-weight:600;width:26px;text-align:right}
.sig-chev{color:var(--txt-dim);transition:transform .2s;flex:none}
.sig-body{padding:4px 13px 14px 31px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:12px}
.sig-block-h{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--txt-dim);margin:10px 0 6px}
.sig-ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.sig-ul li{display:flex;align-items:flex-start;gap:7px;font-size:12.5px;line-height:1.45}
.sig-ul li svg{margin-top:2px;flex:none}
.sig-issue svg{color:var(--warn)} .sig-rec svg{color:var(--accent)}
.sig-issue span{color:var(--txt-mid)} .sig-rec span{color:var(--txt-mid)}
.sig-clean{display:flex;align-items:center;gap:7px;color:var(--good);font-size:12.5px;margin-top:8px}
.sig-ev{display:grid;grid-template-columns:1fr 1fr;gap:4px 16px}
.sig-ev-row{display:flex;justify-content:space-between;gap:10px;font-size:11px;border-bottom:1px solid var(--line);padding:3px 0}
.sig-ev-k{color:var(--txt-dim)} .sig-ev-v{color:var(--txt-mid);text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%}

/* modal */
.modal-wrap{position:fixed;inset:0;background:rgba(4,7,10,.72);backdrop-filter:blur(3px);
  display:flex;align-items:center;justify-content:center;z-index:60;padding:20px}
.modal{background:var(--panel);border:1px solid var(--line-2);border-radius:16px;padding:28px;max-width:400px;width:100%;position:relative}
.modal-x{position:absolute;top:14px;right:14px;background:transparent;border:none;color:var(--txt-dim);cursor:pointer}
.modal-title{font-size:18px;font-weight:700;font-family:'Hanken Grotesk';line-height:1.25;margin-bottom:8px}
.modal-sub{color:var(--txt-mid);font-size:13px;margin-bottom:18px;line-height:1.5}
.modal-input{width:100%;background:var(--panel-2);border:1px solid var(--line-2);border-radius:9px;
  padding:11px 13px;color:var(--txt);font-size:14px;outline:none;font-family:'IBM Plex Mono'}
.modal-input:focus{border-color:var(--accent)}
.modal-btn{width:100%;margin-top:12px;display:inline-flex;align-items:center;justify-content:center;gap:8px;
  background:var(--accent);color:#04222a;border:none;padding:12px;border-radius:9px;font-weight:600;cursor:pointer}
.modal-fine{color:var(--txt-dim);font-size:10.5px;margin-top:14px;text-align:center}

/* app shell */
.app{display:grid;grid-template-columns:236px 1fr;min-height:calc(100vh - 41px)}
.side{border-right:1px solid var(--line);padding:20px 14px;display:flex;flex-direction:column;gap:18px;background:var(--panel)}
.brand{display:flex;align-items:center;gap:9px;font-size:17px;font-weight:700;color:var(--txt);padding:0 6px}
.brand svg{color:var(--accent)}
.site-switch{position:relative}
.site-btn{width:100%;display:flex;align-items:center;gap:8px;background:var(--panel-2);
  border:1px solid var(--line-2);border-radius:9px;padding:9px 11px;color:var(--txt);cursor:pointer;font-size:12.5px;font-family:'IBM Plex Mono'}
.site-btn>span{flex:1;text-align:left}
.site-menu{position:absolute;top:calc(100% + 4px);left:0;right:0;background:var(--panel-2);
  border:1px solid var(--line-2);border-radius:9px;padding:5px;z-index:20}
.site-menu button{width:100%;display:flex;align-items:center;gap:7px;background:transparent;border:none;
  color:var(--txt-mid);padding:8px 9px;border-radius:6px;cursor:pointer;font-size:12.5px;font-family:'IBM Plex Mono';text-align:left}
.site-menu button:hover,.site-menu button.on{background:var(--panel);color:var(--txt)}
.nav{display:flex;flex-direction:column;gap:2px}
.nav-item{display:flex;align-items:center;gap:11px;background:transparent;border:none;color:var(--txt-mid);
  padding:9px 11px;border-radius:8px;cursor:pointer;font-size:13.5px;font-weight:500;text-align:left}
.nav-item:hover{background:var(--panel-2);color:var(--txt)}
.nav-item.on{background:var(--accent-dim);color:var(--accent)}
.nav-item span{flex:1}
.nav-tag{font-size:8.5px;font-weight:700;letter-spacing:.06em;color:var(--txt-dim);border:1px solid var(--line-2);
  padding:1px 5px;border-radius:4px;font-family:'IBM Plex Mono'}
.exit{margin-top:auto;display:flex;align-items:center;gap:8px;background:transparent;border:1px solid var(--line);
  color:var(--txt-mid);padding:9px 11px;border-radius:8px;cursor:pointer;font-size:12.5px}

.main{display:flex;flex-direction:column;min-width:0}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:18px 26px;border-bottom:1px solid var(--line)}
.crumb{font-size:10px;letter-spacing:.12em;color:var(--txt-dim)}
.topbar-title{font-size:20px;font-weight:700;margin-top:3px}
.rescan{display:inline-flex;align-items:center;gap:7px;background:var(--panel-2);border:1px solid var(--line-2);
  color:var(--txt);padding:9px 14px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500}
.content{padding:24px 26px;overflow:auto}

/* grids + cards */
.grid-main{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{border:1px solid var(--line);border-radius:14px;padding:18px;background:var(--panel)}
.card.wide{grid-column:1 / -1}
.card-h{font-size:14px;font-weight:600;margin-bottom:16px;display:flex;gap:9px;align-items:baseline}
.score-card{grid-column:1 / -1;display:flex;align-items:center;gap:34px;flex-wrap:wrap}
.score-side{flex:1;min-width:220px}
.delta{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:15px;font-family:'IBM Plex Mono'}
.delta.up{color:var(--good)} .delta span{color:var(--txt-dim);font-weight:400;font-size:12px;font-family:'Inter'}
.score-copy{color:var(--txt-mid);font-size:13px;line-height:1.55;margin:12px 0 10px;max-width:560px}
.rubric{font-size:10.5px;color:var(--txt-dim)}

/* competitor mini */
.comp-mini{display:flex;flex-direction:column;gap:10px}
.comp-mini.big-gap{gap:16px}
.comp-row{display:grid;grid-template-columns:180px 1fr 34px;align-items:center;gap:12px}
.comp-name{font-size:12.5px;color:var(--txt-mid);display:flex;align-items:center;gap:7px;font-family:'IBM Plex Mono'}
.comp-row.you .comp-name{color:var(--txt)}
.you-tag{font-size:9px;background:var(--accent-dim);color:var(--accent);padding:1px 6px;border-radius:4px;font-family:'Inter';font-weight:600}
.comp-track{height:7px;background:var(--line);border-radius:4px;overflow:hidden}
.comp-fill{height:100%;border-radius:4px;transition:width .8s}
.comp-s{font-size:12px;text-align:right}

/* table */
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;color:var(--txt-dim);font-weight:500;font-size:11px;letter-spacing:.05em;
  padding:8px 10px;border-bottom:1px solid var(--line)}
.tbl td{padding:11px 10px;border-bottom:1px solid var(--line)}
.pill{font-size:11px;background:var(--panel-2);border:1px solid var(--line-2);padding:2px 9px;border-radius:20px;color:var(--txt-mid)}
.pill.accent{background:var(--accent-dim);color:var(--accent);border-color:transparent}
.pill.dim{color:var(--txt-dim)}
.dim{color:var(--txt-dim)}
.link-btn{display:inline-flex;align-items:center;gap:5px;background:transparent;border:none;color:var(--accent);cursor:pointer;font-size:12px}

/* answers */
.paid-banner{display:flex;align-items:center;gap:10px;color:var(--txt-mid);font-size:12.5px;background:var(--accent-dim);border-color:transparent}
.paid-banner svg{color:var(--accent);flex:none}
.prompt-h{font-family:'IBM Plex Mono';font-size:13px;color:var(--txt)}
.prompt-score{display:flex;align-items:baseline;gap:9px;margin-bottom:14px}
.prompt-score .big{font-size:28px;font-weight:600}
.engine-dots{display:flex;gap:6px}
.edot{font-size:9.5px;font-weight:600;color:#04222a;padding:3px 7px;border-radius:5px;font-family:'IBM Plex Mono'}

/* fixes */
.fix-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.fix-card{border:1px solid var(--line);border-radius:11px;padding:15px;background:var(--panel-2)}
.fix-t{font-size:14px;font-weight:600;font-family:'Hanken Grotesk'}
.fix-d{color:var(--txt-mid);font-size:12px;margin:5px 0 13px;line-height:1.4}
.fix-btn{display:inline-flex;align-items:center;gap:6px;background:var(--panel);border:1px solid var(--line-2);
  color:var(--txt);padding:7px 12px;border-radius:7px;font-size:12.5px;cursor:pointer;font-weight:500}

/* empty + plan */
.empty{text-align:center;padding:44px 20px;color:var(--txt-mid)}
.empty svg{color:var(--txt-dim);margin-bottom:12px}
.empty-t{font-size:15px;font-weight:600;color:var(--txt)}
.empty-s{font-size:13px;margin:6px 0 18px}
.plan-row{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid var(--line);font-size:13px;color:var(--txt-mid)}
.plan-row:last-child{border-bottom:none}

@media(max-width:820px){
  .app{grid-template-columns:1fr}
  .side{flex-direction:row;flex-wrap:wrap;align-items:center;border-right:none;border-bottom:1px solid var(--line)}
  .nav{flex-direction:row;flex-wrap:wrap}.exit{margin:0}
  .grid-main,.report-grid,.fix-grid{grid-template-columns:1fr}
  .mkt-hero h1{font-size:38px}.mkt-strip{grid-template-columns:1fr}
}
`;
