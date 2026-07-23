import React, { useState, useEffect, useRef } from "react";
import {
  Search, Lock, ArrowRight, Radar, LayoutDashboard, ScanLine, MessageSquareText,
  Users, Wrench, FileText, Settings, RefreshCw, X, AlertTriangle,
  ExternalLink, Sparkles, Globe
} from "lucide-react";
import {
  AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid
} from "recharts";
import { scanUrl, getScanById, captureLead, ScanError } from "./api";

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
function Gauge({ value, size = 200, label = "AI Readiness" }) {
  const cx = size / 2, cy = size / 2, r = size / 2 - 16;
  const va = 180 * (value / 100);
  const col = bandColor(value);
  return (
    <div className="gauge" style={{ width: size }}>
      <svg width={size} height={size / 2 + 30} viewBox={`0 0 ${size} ${size / 2 + 30}`}>
        <path d={arc(cx, cy, r, 0, 180)} className="gauge-track" />
        <path d={arc(cx, cy, r, 0, Math.max(0.1, va))} style={{ stroke: col }} className="gauge-val" />
        {[0, 45, 75, 100].map((t) => {
          const [x, y] = polar(cx, cy, r + 12, 180 * (t / 100));
          return <text key={t} x={x} y={y} className="gauge-tick">{t}</text>;
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

/* ---------- family bars ---------- */
function FamilyBars({ families }) {
  return (
    <div className="fam">
      {families.map((f) => {
        const pct = Math.round((f.earned / f.weight) * 100);
        return (
          <div key={f.id} className="fam-row">
            <div className="fam-label">{f.label}</div>
            <div className="fam-track">
              <div className="fam-fill" style={{ width: `${pct}%`, background: bandColor(pct) }} />
            </div>
            <div className="fam-num mono">{f.earned}<span className="fam-den">/{f.weight}</span></div>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- issue list ---------- */
function IssueList({ issues, locked, max = 5 }) {
  const list = issues.slice(0, max);
  return (
    <div className="issues">
      {list.map((c, i) => (
        <div key={i} className="issue">
          <span className="issue-chip" style={{ color: STATUS_COLOR[c.status], borderColor: STATUS_COLOR[c.status] }}>
            {STATUS_LABEL[c.status]}
          </span>
          <div className="issue-body">
            <div className="issue-label">{c.label} <span className="issue-fam">{c.family}</span></div>
            <div className="issue-fix">
              {locked ? <><Lock size={11} /> {c.fix || c.fix_hint}</> : <><Wrench size={11} /> {c.fix || c.fix_hint}</>}
            </div>
          </div>
          {locked
            ? <button className="issue-btn locked"><Lock size={12} /> Fix</button>
            : <button className="issue-btn"><ArrowRight size={12} /> Generate</button>}
        </div>
      ))}
    </div>
  );
}

/* ============================= FREE SCANNER LAYER ============================= */
function FreeScanner({ compact, onFull, onScanComplete }) {
  const [url, setUrl] = useState("");
  const [state, setState] = useState("idle"); // idle | scanning | done
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [emailGate, setEmailGate] = useState(false);
  const [scanCount, setScanCount] = useState(0);
  const [email, setEmail] = useState("");
  const inFlight = useRef(false); // synchronous guard against duplicate submits

  const run = async () => {
    if (!url.trim()) return;
    if (inFlight.current) return; // guard against duplicate submits
    if (scanCount >= 1) { setEmailGate(true); return; }
    inFlight.current = true;
    setState("scanning");
    setError(null);
    try {
      // real backend call — no silent mock fallback; a failure surfaces below
      const [result] = await Promise.all([
        scanUrl(url),
        new Promise((r) => setTimeout(r, 1200)), // keep the scan animation legible
      ]);
      const adapted = adaptReport(result);
      setReport(adapted);
      setState("done");
      setScanCount((c) => c + 1);
      onScanComplete?.(adapted); // lift the real report to the app root
    } catch (err) {
      if (err && err.code === 429) { setEmailGate(true); setState("idle"); return; }
      // Show a clear, safe message. Never fall back to fake scores.
      setError(err instanceof ScanError ? err.message : "Scan failed. Please try again.");
      setState("idle");
    } finally {
      inFlight.current = false;
    }
  };

  return (
    <div className={compact ? "scanner compact" : "scanner"}>
      <div className="scan-input">
        <Globe size={16} className="scan-globe" />
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="Paste any website URL"
          spellCheck={false}
        />
        <button className="scan-go" onClick={run} disabled={state === "scanning"}>
          {state === "scanning" ? <><ScanLine size={15} className="spin-slow" /> Scanning</> : <>Scan free <ArrowRight size={15} /></>}
        </button>
      </div>
      <div className="scan-hint">Checks crawler access, render parity, schema, structure, extractability, freshness. No login for your first scan.</div>

      {error && (
        <div className="scan-error" role="alert">
          <AlertTriangle size={14} /> <span>{error}</span>
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
              <span className="report-badge" style={{ color: bandColor(report.ars), borderColor: bandColor(report.ars) }}>
                {band(report.ars) === "good" ? "AI ready" : band(report.ars) === "warn" ? "Needs work" : "At risk"}
              </span>
            </div>
            <Gauge value={report.ars} size={168} />
          </div>

          <div className="report-grid">
            <div className="panel">
              <div className="panel-h">Score by signal family</div>
              <FamilyBars families={report.families} />
            </div>
            <CrawlerStrip crawlers={report.crawlers} />
          </div>

          <div className="panel">
            <div className="panel-h">Top issues <span className="panel-sub">fixes unlock with a free account</span></div>
            <IssueList issues={report.issues} locked max={5} />
          </div>

          <div className="report-cta">
            <div>
              <div className="cta-title">Unlock the full report and the fixes</div>
              <div className="cta-sub">Generated schema, llms.txt, an FAQ block, and a re-scan that proves the score moved.</div>
            </div>
            <button className="cta-btn" onClick={onFull}>Create free account <ArrowRight size={15} /></button>
          </div>
        </div>
      )}

      {emailGate && (
        <div className="modal-wrap" onClick={() => setEmailGate(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-x" onClick={() => setEmailGate(false)}><X size={16} /></button>
            <div className="modal-title">One scan on us. For the next, tell us where to send it.</div>
            <div className="modal-sub">Your first scan was free. Add an email to keep scanning and save your reports.</div>
            <input className="modal-input" placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            <button className="modal-btn" onClick={async () => { await captureLead(email, url); setEmailGate(false); setScanCount(0); }}>Continue scanning <ArrowRight size={15} /></button>
            <div className="modal-fine">This is the lead-capture gate on scan #2. Mocked in the prototype.</div>
          </div>
        </div>
      )}
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
        <div className="mkt-stat"><div className="mono big">6</div><div>signal families scored, zero AI cost</div></div>
        <div className="mkt-stat"><div className="mono big">4</div><div>AI crawlers checked per scan</div></div>
        <div className="mkt-stat"><div className="mono big">0-100</div><div>AI Readiness Score, one number</div></div>
      </div>
    </div>
  );
}

/* ============================= APP SHELL ============================= */
const NAV = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "scans", label: "Scans", icon: ScanLine },
  { id: "answers", label: "Answer visibility", icon: MessageSquareText, paid: true },
  { id: "competitors", label: "Competitors", icon: Users, paid: true },
  { id: "fixes", label: "Fixes", icon: Wrench },
  { id: "reports", label: "Reports", icon: FileText },
  { id: "settings", label: "Settings", icon: Settings },
];

function AppShell({ report, notice, onReport, history = [], onSelectScan, onExit, onGoScan }) {
  const [active, setActive] = useState("overview");
  const [rescanning, setRescanning] = useState(false);
  const [rescanError, setRescanError] = useState(null);
  const [historyError, setHistoryError] = useState(null);
  const inFlight = useRef(false); // synchronous guard: blocks same-tick double clicks

  const site = report ? report.domain : "no site yet";

  // Open a past scan from the history list: fetch the stored report, then show it.
  const openScan = async (id) => {
    setHistoryError(null);
    try {
      await onSelectScan(id);
      setActive("overview");
    } catch (err) {
      setHistoryError(err instanceof ScanError ? err.message
        : "Could not load that scan. It may have expired.");
    }
  };

  // Re-scan the current report's URL against the real backend.
  const doRescan = async () => {
    if (inFlight.current || !report?.url) return; // prevent duplicate clicks
    inFlight.current = true;
    setRescanning(true);
    setRescanError(null);
    try {
      const fresh = adaptReport(await scanUrl(report.url));
      onReport(fresh); // replaces dashboard data and updates the saved scan id
    } catch (err) {
      setRescanError(err instanceof ScanError ? err.message : "Re-scan failed. Please try again.");
    } finally {
      inFlight.current = false;
      setRescanning(false);
    }
  };

  return (
    <div className="app">
      <aside className="side">
        <div className="brand"><Radar size={18} /> <span>AEOMirror</span></div>
        <div className="site-switch">
          <div className="site-btn" style={{ cursor: "default" }}>
            <Globe size={14} /> <span>{site}</span>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <button key={n.id} className={`nav-item ${active === n.id ? "on" : ""}`} onClick={() => setActive(n.id)}>
              <n.icon size={16} /> <span>{n.label}</span>
              {n.paid && <span className="nav-tag">PRO</span>}
            </button>
          ))}
        </nav>
        <button className="exit" onClick={onExit}><ArrowRight size={14} style={{ transform: "rotate(180deg)" }} /> Homepage scanner</button>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <div className="crumb mono">{site.toUpperCase()}</div>
            <div className="topbar-title">{NAV.find((n) => n.id === active).label}</div>
          </div>
          <button className="rescan" onClick={doRescan} disabled={rescanning || !report}>
            <RefreshCw size={14} className={rescanning ? "spin-slow" : ""} /> {rescanning ? "Re-scanning" : "Re-scan"}
          </button>
        </header>

        <div className="content">
          {(rescanError || historyError) && (
            <div className="scan-error" role="alert" style={{ marginBottom: 16 }}>
              <AlertTriangle size={14} /> <span>{rescanError || historyError}</span>
            </div>
          )}

          {!report ? (
            <DashboardEmpty notice={notice} onGoScan={onGoScan} />
          ) : (
            <>
              {active === "overview" && <Overview report={report} />}
              {active === "scans" && <Scans history={history} current={report} onSelect={openScan} />}
              {active === "answers" && <Answers />}
              {active === "competitors" && <Competitors report={report} />}
              {active === "fixes" && <Fixes report={report} />}
              {active === "reports" && <Reports />}
              {active === "settings" && <SettingsScreen />}
            </>
          )}
        </div>
      </main>
    </div>
  );
}

/* Shown when the dashboard has no real scan to display yet. */
function DashboardEmpty({ notice, onGoScan }) {
  return (
    <div className="card wide">
      <div className="empty">
        <ScanLine size={26} />
        <div className="empty-t">No scan loaded yet</div>
        <div className="empty-s">
          {notice || "Run a scan from the homepage scanner to populate this dashboard with a live report."}
        </div>
        <button className="cta-btn small" onClick={onGoScan}>Go to scanner <ArrowRight size={14} /></button>
      </div>
    </div>
  );
}

/* ---------- OVERVIEW (full fidelity) ---------- */
const TREND = [
  { w: "May 26", s: 41 }, { w: "Jun 02", s: 44 }, { w: "Jun 09", s: 43 },
  { w: "Jun 16", s: 52 }, { w: "Jun 23", s: 58 }, { w: "Jun 30", s: 57 },
  { w: "Jul 07", s: 64 }, { w: "Jul 14", s: 68 },
];
function Overview({ report }) {
  return (
    <div className="grid-main">
      <section className="card score-card">
        <Gauge value={report.ars} size={210} />
        <div className="score-side">
          <div className="score-band" style={{ color: bandColor(report.ars) }}>
            {band(report.ars) === "good" ? "AI ready" : band(report.ars) === "warn" ? "Needs work" : "At risk"}
          </div>
          <div className="score-copy">This is structural readiness: whether AI crawlers can reach, render, parse and extract this site. Answer visibility (whether engines actually cite you) is tracked separately under the PRO tab.</div>
          <div className="rubric mono">rubric {report.rubric_version}</div>
        </div>
      </section>

      <section className="card">
        <div className="card-h">Readiness trend <span className="card-sub">illustrative — history builds after repeat scans</span></div>
        <div style={{ height: 150 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={TREND} margin={{ top: 6, right: 6, bottom: 0, left: -18 }}>
              <defs>
                <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="w" tick={{ fill: "var(--txt-dim)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 100]} tick={{ fill: "var(--txt-dim)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 8, color: "var(--txt)", fontSize: 12 }} />
              <Area type="monotone" dataKey="s" stroke="var(--accent)" strokeWidth={2} fill="url(#g)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="card">
        <div className="card-h">Score by signal family</div>
        <FamilyBars families={report.families} />
      </section>

      <section className="card">
        <CrawlerStrip crawlers={report.crawlers} />
      </section>

      <section className="card wide">
        <div className="card-h">This week's backlog <span className="card-sub">ranked by impact, ready to assign</span></div>
        <IssueList issues={report.issues} locked={false} max={5} />
      </section>

      <section className="card">
        <div className="card-h">Competitor snapshot <span className="card-sub">PRO</span></div>
        <div className="comp-mini">
          {[{ n: report.domain, s: report.ars, you: true }, { n: "competitor-a.com", s: report.ars + 9 }, { n: "competitor-b.com", s: report.ars - 6 }].map((c) => (
            <div key={c.n} className={`comp-row ${c.you ? "you" : ""}`}>
              <span className="comp-name">{c.n}{c.you && <span className="you-tag">you</span>}</span>
              <div className="comp-track"><div className="comp-fill" style={{ width: `${c.s}%`, background: c.you ? "var(--accent)" : "var(--line-2)" }} /></div>
              <span className="mono comp-s">{c.s}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

/* ---------- lighter secondary screens (walking skeleton) ---------- */
function Scans({ history = [], current, onSelect }) {
  const fmtDate = (ms) => {
    try { return new Date(ms).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); }
    catch { return "-"; }
  };
  return (
    <div className="card wide">
      <div className="card-h">Scan history <span className="card-sub">stored scans in this browser</span></div>
      {history.length === 0 ? (
        <div className="empty">
          <ScanLine size={22} />
          <div className="empty-t">No scans yet</div>
          <div className="empty-s">Run a scan and it will appear here.</div>
        </div>
      ) : (
        <table className="tbl">
          <thead><tr><th>Date</th><th>URL</th><th>ARS</th><th>Rubric</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.id}>
                <td className="mono">{fmtDate(h.date)}</td>
                <td>{h.domain || h.url}{current && current.scan_id === h.id && <span className="you-tag" style={{ marginLeft: 7 }}>current</span>}</td>
                <td><span className="mono" style={{ color: bandColor(h.ars) }}>{h.ars}</span></td>
                <td className="mono dim">{h.rubric_version || "-"}</td>
                <td><span className="pill">{h.status || "complete"}</span></td>
                <td><button className="link-btn" onClick={() => onSelect(h.id)}>View <ExternalLink size={11} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
function Answers() {
  const prompts = [
    { p: "best cold brew coffee maker 2026", ct: 2, tot: 5 },
    { p: "affordable ethical skincare india", ct: 3, tot: 5 },
    { p: "shopify vs woocommerce for small brands", ct: 0, tot: 5 },
  ];
  return (
    <div className="grid-main">
      <div className="card wide paid-banner">
        <Sparkles size={15} /> <span>Answer visibility runs live prompts against ChatGPT, Claude, Gemini and Perplexity. This is a metered PRO feature. Results below are illustrative.</span>
      </div>
      {prompts.map((pr, i) => (
        <div key={i} className="card">
          <div className="card-h prompt-h">"{pr.p}"</div>
          <div className="prompt-score">
            <span className="mono big" style={{ color: pr.ct === 0 ? "var(--bad)" : "var(--txt)" }}>{pr.ct}/{pr.tot}</span>
            <span className="dim">engines mentioned you</span>
          </div>
          <div className="engine-dots">
            {["GPT", "Claude", "Gemini", "Pplx", "AIO"].map((e, j) => (
              <span key={e} className="edot" style={{ background: j < pr.ct ? "var(--good)" : "var(--line-2)" }}>{e}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
function Competitors({ report }) {
  const comps = [
    { n: report.domain, s: report.ars, you: true },
    { n: "competitor-a.com", s: report.ars + 9 },
    { n: "competitor-b.com", s: report.ars - 6 },
    { n: "competitor-c.com", s: report.ars + 2 },
  ].sort((a, b) => b.s - a.s);
  return (
    <div className="card wide">
      <div className="card-h">Readiness vs tracked competitors <span className="card-sub">PRO</span></div>
      <div className="comp-mini big-gap">
        {comps.map((c) => (
          <div key={c.n} className={`comp-row ${c.you ? "you" : ""}`}>
            <span className="comp-name">{c.n}{c.you && <span className="you-tag">you</span>}</span>
            <div className="comp-track"><div className="comp-fill" style={{ width: `${c.s}%`, background: c.you ? "var(--accent)" : "var(--line-2)" }} /></div>
            <span className="mono comp-s" style={{ color: bandColor(c.s) }}>{c.s}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
function Fixes({ report }) {
  const assets = [
    { t: "Organization JSON-LD", d: "Generated from your page content", ready: true },
    { t: "FAQPage schema", d: "From detected question-answer gaps", ready: true },
    { t: "robots.txt corrector", d: "Unblocks GPTBot and ClaudeBot", ready: true },
    { t: "llms.txt", d: "Emerging convention, adoption unconfirmed", ready: true },
  ];
  return (
    <div className="grid-main">
      <div className="card wide">
        <div className="card-h">Generated fixes <span className="card-sub">each ships as a copy-paste asset, then re-scan to prove the delta</span></div>
        <div className="fix-grid">
          {assets.map((a) => (
            <div key={a.t} className="fix-card">
              <div className="fix-t">{a.t}</div>
              <div className="fix-d">{a.d}</div>
              <button className="fix-btn"><Wrench size={12} /> Generate</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
function Reports() {
  return (
    <div className="card wide">
      <div className="card-h">Client reports <span className="card-sub">white-label export, Agency tier</span></div>
      <div className="empty">
        <FileText size={26} />
        <div className="empty-t">No reports generated yet</div>
        <div className="empty-s">Build a white-label PDF from any site's latest scan and schedule it monthly.</div>
        <button className="cta-btn small">New report <ArrowRight size={14} /></button>
      </div>
    </div>
  );
}
function SettingsScreen() {
  return (
    <div className="grid-main">
      <div className="card">
        <div className="card-h">Plan</div>
        <div className="plan-row"><span>Current plan</span><span className="pill accent">Track / $69</span></div>
        <div className="plan-row"><span>Sites</span><span className="mono">1 of 5</span></div>
        <div className="plan-row"><span>Tracked prompts</span><span className="mono">18 of 25</span></div>
        <div className="plan-row"><span>Engines</span><span className="mono">4</span></div>
      </div>
      <div className="card">
        <div className="card-h">Team</div>
        <div className="plan-row"><span>Ayush Prashar</span><span className="pill">Owner</span></div>
        <div className="plan-row"><span>seat 2</span><span className="pill dim">invite</span></div>
        <div className="plan-row"><span>seat 3</span><span className="pill dim">invite</span></div>
      </div>
    </div>
  );
}

/* ============================= ROOT ============================= */
export default function App() {
  const [view, setView] = useState("marketing"); // marketing | app
  const [report, setReport] = useState(null);     // the real backend report (source of truth)
  const [notice, setNotice] = useState(null);     // e.g. a saved scan that expired
  const [history, setHistory] = useState(loadHistory);

  // Store a fresh real report, remember its id for restore, and record history.
  const commitReport = (rep) => {
    setReport(rep);
    setNotice(null);
    try {
      if (rep?.scan_id && !String(rep.scan_id).startsWith("mock-")) {
        localStorage.setItem(LAST_SCAN_KEY, rep.scan_id);
      }
    } catch { /* localStorage unavailable — non-fatal */ }
    setHistory(pushHistory(rep));
  };

  // Open a past scan by id: always fetch the stored report from the backend
  // (never regenerate) and show it in the dashboard.
  const selectScan = async (id) => {
    const rep = adaptReport(await getScanById(id));
    commitReport(rep);
    return rep;
  };

  // On load, restore the last scan from the backend using the saved id.
  useEffect(() => {
    let saved = null;
    try { saved = localStorage.getItem(LAST_SCAN_KEY); } catch { /* ignore */ }
    if (!saved) return;
    let cancelled = false;
    (async () => {
      try {
        const rep = adaptReport(await getScanById(saved));
        if (!cancelled) setReport(rep);
      } catch (err) {
        // Expired / missing / unreachable: drop the invalid id and stay graceful.
        if (err instanceof ScanError && err.code === 404) {
          try { localStorage.removeItem(LAST_SCAN_KEY); } catch { /* ignore */ }
          if (!cancelled) setNotice("Your last saved scan has expired. Run a new scan to continue.");
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="root">
      <style>{CSS}</style>
      <div className="switcher">
        <button className={view === "marketing" ? "on" : ""} onClick={() => setView("marketing")}>Homepage scanner</button>
        <button className={view === "app" ? "on" : ""} onClick={() => setView("app")}>Product dashboard</button>
      </div>
      {view === "marketing"
        ? <Marketing onFull={() => setView("app")} onScanComplete={commitReport} />
        : <AppShell report={report} notice={notice} onReport={commitReport}
                    history={history} onSelectScan={selectScan}
                    onExit={() => setView("marketing")} onGoScan={() => setView("marketing")} />}
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
  padding:8px;background:rgba(11,15,20,.85);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.switcher button{background:transparent;border:1px solid transparent;color:var(--txt-mid);
  padding:6px 14px;border-radius:7px;font-size:12.5px;cursor:pointer;font-weight:500}
.switcher button.on{background:var(--panel-2);color:var(--txt);border-color:var(--line)}

/* marketing */
.mkt{max-width:1080px;margin:0 auto;padding:56px 24px 80px}
.eyebrow{display:inline-flex;align-items:center;gap:7px;color:var(--accent);font-size:11px;
  letter-spacing:.12em;margin-bottom:22px;border:1px solid var(--line);padding:5px 11px;border-radius:20px}
.mkt-hero h1{font-size:52px;line-height:1.03;font-weight:800;letter-spacing:-.02em;margin:0 0 20px}
.mkt-lede{color:var(--txt-mid);font-size:16.5px;line-height:1.55;max-width:640px;margin:0 0 34px}
.mkt-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:52px;
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
.scan-error{display:flex;align-items:center;gap:8px;margin-top:14px;padding:10px 12px;border:1px solid var(--bad);
  border-radius:9px;background:rgba(229,97,91,.10);color:var(--bad);font-size:12.5px}
.scan-error svg{flex:none}
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

/* gauge */
.gauge{position:relative;text-align:center;flex:none}
.gauge-track{fill:none;stroke:var(--line);stroke-width:12;stroke-linecap:round}
.gauge-val{fill:none;stroke-width:12;stroke-linecap:round;transition:all .9s cubic-bezier(.2,.7,.2,1)}
.gauge-tick{fill:var(--txt-dim);font-size:9px;font-family:'IBM Plex Mono';text-anchor:middle}
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
