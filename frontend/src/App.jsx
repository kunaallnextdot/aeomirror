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
import { AuAvatar } from "./dashboard/aurora.jsx";
import "./App.aurora.css";
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
// DEV-only Aurora UI kit review surface. Gating the lazy import on import.meta.env.DEV (a
// build-time constant) puts the dynamic import in a dead branch for production, so Rollup emits
// NO aurora-uikit / aurora chunk in prod at all. See aurora-uikit.jsx for removal steps.
const UiKit = import.meta.env.DEV ? React.lazy(() => import("./dashboard/aurora-uikit.jsx")) : null;
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
/* Aurora equivalents (Phase 13a) — same thresholds/mapping, `--au-*` tokens. */
function auBandColor(v) { return v >= 75 ? "var(--au-mint-d)" : v >= 45 ? "var(--au-lemon-d)" : "var(--au-peach-d)"; }
const AU_STATUS_COLOR = { pass: "var(--au-mint-d)", warn: "var(--au-lemon-d)", fail: "var(--au-peach-d)" };

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
  const col = auBandColor(value);
  // Horizontal padding in the viewBox so the end tick labels (0 and 100) never
  // clip against the SVG edge. Only 0 and 100 are shown to keep the dial clean.
  const PAD = 12;
  return (
    <div className="au-gauge" style={{ width: size }}>
      <svg width={size} height={size / 2 + 30}
           viewBox={`${-PAD} 0 ${size + PAD * 2} ${size / 2 + 30}`}>
        <path d={arc(cx, cy, r, 0, 180)} className="au-gauge-track" />
        <path d={arc(cx, cy, r, 0, Math.max(0.1, va))} style={{ stroke: col }} className="au-gauge-val" />
        {[0, 100].map((t) => {
          const [x, y] = polar(cx, cy, r, 180 * (t / 100));
          // Anchor the ends inward (0 = start, 100 = end) and drop them just below
          // the arc so both stay fully inside the padded viewBox.
          return (
            <text key={t} x={x} y={y + 15} textAnchor={t === 0 ? "start" : "end"}
                  className="au-gauge-tick">{t}</text>
          );
        })}
      </svg>
      <div className="au-gauge-center">
        <div className="au-gauge-num" style={{ color: col }}>{value}</div>
        <div className="au-gauge-den">/ 100</div>
      </div>
      <div className="au-gauge-label">{label}</div>
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
    <div className="au-strip">
      <div className="au-strip-head">
        <span>AI crawler visibility</span>
        <span className="au-strip-sub">can each engine reach this site</span>
      </div>
      <div className="au-strip-rows">
        {crawlers.map((c) => {
          const m = CRAWLER_META[c.id];
          const ok = c.status === "pass";
          const warn = c.status === "warn";
          return (
            <div key={c.id} className="au-strip-row">
              <div className="au-strip-bars">
                {[0, 1, 2].map((i) => (
                  <span key={i} className="au-bar" style={{
                    height: 8 + i * 5,
                    background: ok ? "var(--au-mint-d)" : warn && i < 2 ? "var(--au-lemon-d)" : (!ok && !warn && i < 1) ? "var(--au-peach-d)" : "var(--au-line)"
                  }} />
                ))}
              </div>
              <div className="au-strip-name">
                <div className="au-strip-n">{m.name}</div>
                <div className="au-strip-s">{m.sub}</div>
              </div>
              <div className="au-strip-status" style={{ color: AU_STATUS_COLOR[c.status] }}>
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
    <div className="au-fam">
      {sections.map((s) => {
        const v = Math.max(0, Math.min(100, s.score ?? 0));
        return (
          <div key={s.id} className="au-fam-row">
            <div className="au-fam-label">{s.label}</div>
            <div className="au-fam-track">
              <div className="au-fam-fill" style={{ width: `${v}%`, background: auBandColor(v) }} />
            </div>
            <div className="au-fam-num" style={{ color: auBandColor(v) }}>{s.score}<span className="au-fam-den">/100</span></div>
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
      <div className="au-issues">
        {list.map((c, i) => (
          <div key={i} className="au-issue">
            <span className="au-issue-chip" style={{ color: AU_STATUS_COLOR[c.status], borderColor: AU_STATUS_COLOR[c.status] }}>
              {STATUS_LABEL[c.status]}
            </span>
            <div className="au-issue-body">
              <div className="au-issue-label">{c.label} <span className="au-issue-fam">{c.family}</span></div>
              <div className="au-issue-fix"><Lock size={11} /> {c.fix || c.fix_hint}</div>
            </div>
            <button className="au-issue-btn locked"><Lock size={12} /> Fix</button>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="au-issues">
      {list.map((c, i) => {
        const isOpen = !!open[i];
        const fix = c.fix || c.fix_hint;
        return (
          <div key={i} className={`au-issue expandable${isOpen ? " open" : ""}`}>
            <button className="au-issue-main" onClick={() => toggle(i)} aria-expanded={isOpen}>
              <span className="au-issue-chip" style={{ color: AU_STATUS_COLOR[c.status], borderColor: AU_STATUS_COLOR[c.status] }}>
                {STATUS_LABEL[c.status]}
              </span>
              <div className="au-issue-body">
                <div className="au-issue-label">{c.label} <span className="au-issue-fam">{c.family}</span></div>
                <div className="au-issue-fix"><Wrench size={11} /> {isOpen ? "How to fix ↓" : "See the fix"}</div>
              </div>
              <ChevronDown size={15} className="au-issue-chev" style={{ transform: isOpen ? "rotate(180deg)" : "none" }} />
            </button>
            {isOpen && (
              <div className="au-issue-detail">
                {c.detail && <p className="au-issue-detail-finding">{c.detail}</p>}
                {fix && (
                  <div className="au-issue-detail-fix">
                    <div className="au-issue-detail-h"><Wrench size={11} /> Recommended fix</div>
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
    <div className={compact ? "au-scanner compact" : "au-scanner"}>
      {isAuthenticated && (
        <div className="au-scan-mode" role="group" aria-label="Scan type">
          <button type="button" className={tab === "single" ? "on" : ""} onClick={() => setTab("single")}>Single page</button>
          <button type="button" className={tab === "bulk" ? "on" : ""} onClick={() => setTab("bulk")}>Bulk — up to 50 URLs</button>
        </div>
      )}

      {tab === "single" ? (
        <>
          <div className="au-scan-input">
            <Globe size={16} className="au-scan-globe" />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="Paste any website URL"
              spellCheck={false}
            />
            <button className="au-scan-go" onClick={() => run()} disabled={state === "scanning"}>
              {state === "scanning" ? <><ScanLine size={15} className="au-spin" /> Scanning</> : <>Scan free <ArrowRight size={15} /></>}
            </button>
          </div>
          <div className="au-scan-hint">Checks crawler access, indexability, schema, content structure, internal linking, performance and freshness across 10 signals. Free account — 1 scan a month, plus a one-time 50-URL bulk trial.</div>
        </>
      ) : (
        <BulkScanPanel onGate={setGate402} />
      )}

      {error && (
        <div className="au-scan-error" role="alert">
          <AlertTriangle size={14} /> <span>{error}</span>
        </div>
      )}

      {gate402 && (
        <div className="au-scan-error au-scan-gate" role="alert">
          <AlertTriangle size={14} /> <span>{gate402}</span>
          <button className="au-scan-gate-up" onClick={() => navigate("/app")}>Upgrade to Pro <ArrowRight size={13} /></button>
        </div>
      )}

      {state === "scanning" && (
        <div className="au-scan-progress">
          <div className="au-scan-line" />
          <div className="au-scan-steps">
            <span>fetch</span><span>render</span><span>parse robots.txt</span><span>read schema</span><span>score</span>
          </div>
        </div>
      )}

      {state === "done" && report && (
        <div className="au-report">
          <div className="au-report-top">
            <div className="au-report-domain">
              <div className="au-report-label">RESULT</div>
              <div className="au-report-url">{report.domain}</div>
              <span className="au-report-badge" style={{ color: auBandColor(headline), borderColor: auBandColor(headline) }}>
                {band(headline) === "good" ? "AI ready" : band(headline) === "warn" ? "Needs work" : "At risk"}
              </span>
            </div>
            <Gauge value={headline} size={168} />
          </div>

          <div className="au-report-grid">
            <div className="au-mkt-panel">
              <div className="au-mkt-panel-h">Score by signal <span className="au-mkt-panel-sub">10 checks</span></div>
              <SignalBars sections={report.sections} />
            </div>
            <CrawlerStrip crawlers={report.crawlers} />
          </div>

          <div className="au-mkt-panel">
            <div className="au-mkt-panel-h">Top issues
              {!isAuthenticated && <span className="au-mkt-panel-sub">fixes unlock with a free account</span>}
            </div>
            <IssueList issues={report.issues} locked={!isAuthenticated} max={5} />
          </div>

          {!isAuthenticated ? (
            <div className="au-report-cta">
              <div>
                <div className="au-cta-title">Unlock the full report and the fixes</div>
                <div className="au-cta-sub">Generated schema, llms.txt, an FAQ block, and a re-scan that proves the score moved.</div>
              </div>
              <button className="au-cta-btn" onClick={onFull}>Create free account <ArrowRight size={15} /></button>
            </div>
          ) : (
            <div className="au-report-cta">
              <div>
                <div className="au-cta-title">Your full report is ready</div>
                <div className="au-cta-sub">
                  {isPro
                    ? "Open the full breakdown in your dashboard, or download the PDF to share."
                    : "Open the full breakdown in your dashboard — every signal, fix and evidence detail."}
                </div>
              </div>
              <div className="au-report-cta-actions">
                <button className="au-cta-btn" onClick={viewFullReport}>View full report <ArrowRight size={15} /></button>
                {isPro ? (
                  <button className="au-cta-link" onClick={downloadPdf} disabled={pdfBusy}>
                    <Download size={14} /> {pdfBusy ? "Preparing…" : "Download PDF"}
                  </button>
                ) : (
                  <button className="au-cta-link" onClick={() => openUpgrade("report", { scanId: report.scan_id })}>
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
    <div className="au-bulk">
      {!trial.isPro && trial.available && (
        <div className="au-bulk-badge"><Sparkles size={12} /> 1 free bulk trial</div>
      )}
      <textarea className="au-bulk-text" rows={5} spellCheck={false}
                placeholder={"One URL per line…\nhttps://example.com/\nhttps://example.com/pricing"}
                value={text} onChange={(e) => setText(e.target.value)} />
      <div
        className={`au-bulk-drop${dragOver ? " over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
        role="button" tabIndex={0}
      >
        <FileText size={15} />
        <span>{file ? file.name : "Drop a .csv, .xlsx, .txt or .json here, or click to browse"}</span>
        {file && <button className="au-bulk-clear" onClick={(e) => { e.stopPropagation(); setFile(null); if (fileRef.current) fileRef.current.value = ""; }}>Clear</button>}
        <input ref={fileRef} type="file" accept={BULK_ACCEPT} hidden
               onChange={(e) => chooseFile(e.target.files?.[0] || null)} />
      </div>

      <div className="au-bulk-foot">
        <span className={`au-bulk-count${over ? " over" : ""}`}>
          {file ? "URLs read from file" : `${count} / ${BULK_MAX} URLs`}{over ? " · extras skipped" : ""}
        </span>
        <button className="au-scan-go" onClick={submit} disabled={busy}>
          {busy ? <><ScanLine size={15} className="au-spin" /> Starting…</>
            : <>Scan {file ? "file" : `${Math.min(count, BULK_MAX)} URL${count === 1 ? "" : "s"}`} <ArrowRight size={15} /></>}
        </button>
      </div>

      {err && <div className="au-scan-error" role="alert"><AlertTriangle size={14} /> <span>{err}</span></div>}
      {skipped?.length > 0 && <SkippedList skipped={skipped} />}
      <div className="au-scan-hint">Bulk scans run in the background — we'll take you to a live progress view.</div>
    </div>
  );
}

function SkippedList({ skipped }) {
  return (
    <div className="au-bulk-skipped">
      <div className="au-bulk-skipped-h">{skipped.length} URL{skipped.length === 1 ? "" : "s"} skipped</div>
      {skipped.slice(0, 8).map((s, i) => (
        <div key={i} className="au-bulk-skipped-row">
          <span className="au-bulk-skipped-url">{s.url}</span>
          <span className="au-bulk-skipped-reason">{SKIP_LABEL[s.reason] || s.reason}</span>
        </div>
      ))}
      {skipped.length > 8 && <div style={{ fontSize: 11.5, marginTop: 4, color: "var(--au-muted)" }}>+{skipped.length - 8} more</div>}
    </div>
  );
}

/* ============================= MARKETING VIEW ============================= */
function Marketing({ onFull, onScanComplete }) {
  return (
    <div className="au-mkt">
      <div className="au-mkt-hero">
        <div className="au-eyebrow"><Radar size={13} /> AEOMIRROR / AI VISIBILITY SCANNER</div>
        <h1>Is AI recommending you,<br />or your competitor?</h1>
        <p className="au-mkt-lede">
          Search rankings no longer predict AI answers. AEOMirror scans any site and shows exactly what
          ChatGPT, Claude, Gemini and Perplexity can reach, read, and cite. Then it hands you the fixes.
        </p>
        <FreeScanner onFull={onFull} onScanComplete={onScanComplete} />
      </div>

      <div className="au-mkt-strip">
        <div className="au-mkt-stat"><div className="au-big">10</div><div>signals scored — free scan, no AI cost</div></div>
        <div className="au-mkt-stat"><div className="au-big">4</div><div>AI crawlers checked per scan</div></div>
        <div className="au-mkt-stat"><div className="au-big">AI</div><div>written reports & content insights on Pro</div></div>
      </div>
    </div>
  );
}

/* Legacy AppShell + mock screens removed in Phase 4 (replaced by ./dashboard/Dashboard.jsx). */

/* ============================= AUTH-AWARE MARKETING HEADER ============================= */
function TopBar() {
  const { ready, isAuthenticated, user } = useAuth();
  return (
    <div className="au-topbar">
      <div className="au-topbar-brand" onClick={() => navigate("/")} style={{ cursor: "pointer" }}><Radar size={17} /> AEOMirror</div>
      <div className="au-topbar-actions">
        <button className="au-tb-btn au-tb-link" onClick={() => navigate("/contact")}>Contact</button>
        {!ready ? null : isAuthenticated ? (
          <>
            <button className="au-tb-btn au-tb-primary" onClick={() => navigate("/app")}>
              <LayoutDashboard size={15} /> Dashboard
            </button>
            <button className="au-tb-avatar" onClick={() => navigate("/app")} title={user?.name}>
              <AuAvatar user={user} size={30} />
            </button>
          </>
        ) : (
          <>
            <button className="au-tb-btn" onClick={() => navigate("/login")}>Log in</button>
            <button className="au-tb-btn au-tb-primary" onClick={() => navigate("/register")}>Sign up free</button>
          </>
        )}
      </div>
    </div>
  );
}

function MarketingRoot() {
  const { isAuthenticated } = useAuth();
  const body = (
    <div className="au-site">
      <TopBar />
      <Marketing onFull={() => navigate(isAuthenticated ? "/app" : "/register")} onScanComplete={() => {}} />
      <SiteFooter />
    </div>
  );
  // Signed-in visitors get the shared subscription context (plan + usage) and the
  // UpgradeModal, so the result card can render its plan-aware CTA and open the
  // paywall. Signed-out visitors skip it (no authed billing call, no modal needed).
  return isAuthenticated ? <UpgradeProvider>{body}</UpgradeProvider> : body;
}

/* Public /contact page shell: same marketing chrome (top bar + footer). */
function ContactRoot() {
  // 13a: chrome (TopBar/SiteFooter) is Aurora on the light au-site ground; the Contact
  // page body itself is migrated in 13b (renders dark until then — a known, bounded gap).
  return (
    <div className="au-site">
      <TopBar />
      <Suspense fallback={<FullScreenLoader />}><Contact /></Suspense>
      <SiteFooter />
    </div>
  );
}

/* Website footer with a Contact/Support section. */
function SiteFooter() {
  return (
    <footer className="au-site-footer">
      <div className="au-footer-inner">
        <div className="au-footer-brand">
          <div className="au-footer-logo"><Radar size={16} /> AEOMirror</div>
          <p className="au-footer-tag">See what AI can reach, read and cite on any site — then fix it.</p>
        </div>
        <div className="au-footer-help">
          <div className="au-footer-help-h"><LifeBuoy size={15} /> Need help?</div>
          <a className="au-footer-mail" href={`mailto:${SUPPORT_EMAIL}`}>
            <Mail size={13} /> {SUPPORT_EMAIL}
          </a>
          <button className="au-footer-cta" onClick={() => navigate("/contact")}>
            Contact us <ArrowRight size={14} />
          </button>
        </div>
      </div>
      <div className="au-footer-bottom">© {new Date().getFullYear()} AEOMirror. All rights reserved.</div>
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

      {/* DEV-only Aurora primitive review surface (absent from production builds) */}
      {import.meta.env.DEV && (
        <Route path="/ui-kit" element={
          <Suspense fallback={<FullScreenLoader />}><UiKit /></Suspense>
        } />
      )}

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
  /* --- Aurora design system (Phase 1, ADDITIVE) ---------------------------------
     Namespaced au- so nothing above is touched and no name collides (the prototype's
     bare line/line-2/primary would clash with the dark tokens). NOTHING consumes these
     yet — screens are repointed one phase at a time under a per-screen .aurora-screen
     wrapper; see design/token-map.md. Dark tokens + the injected .root base rules are
     removed only in the final cleanup phase. */
  /* surfaces */
  /* Warm "atmospheric" theme (v2): dark-brown→burnt-orange ground behind warm CREAM cards.
     Cards stay light, so all dark-on-light text/contrast is preserved. */
  --au-app:#EFE6D6; --au-panel:#FBF6EE; --au-solid:#FCF8F1; --au-glass:rgba(252,248,241,.74);
  /* text (dark charcoal heading + readable blue-gray secondary) */
  --au-ink:#231A12; --au-ink-2:#4A5265; --au-muted:#616876;
  /* lines (warm) */
  --au-line:rgba(60,40,22,.12); --au-line-2:rgba(60,40,22,.07);
  /* brand — burnt orange/brown CTA + brighter orange/gold accent */
  --au-primary:#A34A15; --au-pop:#E8823C;
  /* pastel tint + deep pair per hue (semantic; deeps tuned for AA on cream cards) */
  --au-mint:#DCF1E4; --au-mint-d:#157A4E;
  --au-lav:#EAE6FE; --au-lav-d:#6C4BF0;
  --au-peach:#FBE6D8; --au-peach-d:#BA501D;
  --au-sky:#E2F1FD; --au-sky-d:#1C76B4;
  --au-lemon:#FBEFD3; --au-lemon-d:#956808;
  /* semantic aliases (map onto the hue pairs — usage convention) */
  --au-success:var(--au-mint); --au-success-d:var(--au-mint-d);
  --au-warning:var(--au-lemon); --au-warning-d:var(--au-lemon-d);
  --au-danger:var(--au-peach);  --au-danger-d:var(--au-peach-d);
  --au-neutral:var(--au-sky);   --au-neutral-d:var(--au-sky-d);
  /* atmospheric ground — LIGHT + minimal warm: soft cream with one gentle orange glow
     (was a heavy dark→burnt-orange; lightened so it reads airy, not dull). */
  --au-atmos:radial-gradient(1000px 540px at 78% -8%,rgba(232,140,70,.14),transparent 58%),linear-gradient(168deg,#FCF7F0 0%,#F7EEE2 55%,#F3E7D8 100%);
  /* radius (incl. pill) */
  --au-r-s:14px; --au-r-m:22px; --au-r-l:30px; --au-r-pill:999px;
  /* shadow (warm-tinted, a touch deeper for lift on the dark ground) */
  --au-sh-s:0 2px 8px rgba(40,24,10,.07);
  --au-sh:0 4px 14px rgba(40,24,10,.08), 0 20px 50px rgba(40,24,10,.12);
  --au-sh-l:0 10px 30px rgba(40,24,10,.14), 0 40px 90px rgba(40,24,10,.20);
  /* type — faces only; applied per screen, never globally in this phase */
  --au-font-heading:'Outfit',system-ui,sans-serif;
  --au-font-body:'Plus Jakarta Sans',system-ui,sans-serif;
  --au-font-numeric:'DM Mono',ui-monospace,monospace;
  /* motion */
  --au-ease:cubic-bezier(.2,.9,.28,1);
  background:var(--bg); color:var(--txt);
  font-family:'Inter',system-ui,sans-serif; min-height:100vh; font-size:14px;
}
/* Aurora reveal keyframe (unused until a screen opts in via animation on .aurora-screen
   elements; stagger with per-element animation-delay of ~60ms). */
@keyframes au-reveal{from{opacity:0;transform:translateY(20px) scale(.985)}to{opacity:1;transform:none}}
/* Accessibility guard — copied verbatim from the prototype. Global by design (universal
   selector); only affects viewers who request reduced motion, collapsing animation to ~0. */
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;
  animation-iteration-count:1!important;transition-duration:.01ms!important}}
.root *{box-sizing:border-box}
.mono{font-family:'IBM Plex Mono',monospace}
h1,h2,h3,.brand,.topbar-title,.gauge-num,.cta-title{font-family:'Hanken Grotesk',sans-serif}


/* app-wide spinner (used by <Loader2 className="spin-slow"> in the loaders, guards,
   admin, and the migrated Aurora screens). The rest of the old injected marketing +
   walking-skeleton CSS was dead after the Aurora migration and removed in #14 cleanup. */
.spin-slow{animation:spin 1.4s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
`;
