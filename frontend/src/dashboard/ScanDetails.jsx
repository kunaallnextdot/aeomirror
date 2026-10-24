/* Scan Details (/app/scans/:scanId) — MIGRATED to the Spectrum design system.
   Data flow, hooks, effects and handlers are byte-for-byte unchanged from the pre-Aurora
   version; only JSX + class names changed (Aurora au-* classes -> Spectrum primitives +
   sp-sd-*). Route-level loading + error states are exported here (ScanDetailLoading /
   ScanDetailNotFound) so the route can render them in Spectrum too, without touching the
   shared ui.jsx primitives.
   Out of scope (still dark, flagged): <ContentInsightsCard> is a separate component.
   <RecommendationCard> and <VerificationBadge> (from ReportView.jsx/VerificationBadge.jsx)
   are both fully Spectrum-classed now, so no Aurora CSS import is needed for them here. */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronLeft, ChevronDown, AlertTriangle, Wrench, Check, RefreshCw, FileText,
  ArrowUp, ArrowDown, Lock, History, CheckCircle2, XCircle, MinusCircle,
} from "lucide-react";
import Logo from "../shared/Logo.jsx";
import { fmtDate, fmtDuration } from "./ui.jsx";
import { getScanStatus, getReport, createVerification, getVerifications, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { ContentInsightsCard } from "./ContentInsights.jsx";
import { RecommendationCard } from "./ReportView.jsx";
import VerificationBadge from "./VerificationBadge.jsx";
import { Shell, Cell, Button, Tag, Ring, ProgressBar, Skeleton } from "../shared/spectrum.jsx";
import "./ScanDetails.spectrum.css";

const STATUS_VARIANT = { pass: "ok", warn: "warning", fail: "critical" };
const spStatusColor = (s) => s === "pass" ? "var(--sp-blue-d)" : s === "warn" ? "var(--sp-lav-d)"
  : s === "fail" ? "var(--sp-violet-d)" : "var(--sp-muted)";
const scoreStatus = (v) => (v >= 75 ? "pass" : v >= 45 ? "warn" : "fail");

function fmtEvidence(v) {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (v == null) return "-";
  if (Array.isArray(v)) return v.length ? `${v.length}: ${v.slice(0, 3).join(", ")}${v.length > 3 ? "…" : ""}` : "none";
  if (typeof v === "object") return Object.entries(v).map(([k, val]) => `${k}=${val}`).join(", ");
  return String(v);
}

export default function ScanDetails({ scan, onBack, onRerun, busy, canRun = true, onReport,
                                      onRefresh, onRetryBulk, scans = [] }) {
  const [open, setOpen] = useState({});
  const { openUpgrade } = useUpgrade();

  // Problem-first landing: a signal with a real issue or recommendation is expanded
  // by default, so the diagnosis is visible immediately after a scan completes
  // instead of requiring a click per signal. A clean/passing signal stays collapsed —
  // "clean" uses the exact same definition as the render below (issues/recommendations
  // present), never `s.status` alone. Recomputed whenever a different scan is viewed.
  useEffect(() => {
    const initial = {};
    for (const s of scan.sections || []) {
      if ((s.issues?.length > 0) || (s.recommendations?.length > 0)) initial[s.id] = true;
    }
    setOpen(initial);
  }, [scan.scan_id]);
  // A bulk scan runs in the background: show live progress while it is
  // pending/running, and an error + retry if it failed. Single-page scans are
  // always "completed" and fall straight through to the report below.
  const status = scan.status || "completed";
  const bulk = scan.bulk || null;

  // Reuses the EXISTING report endpoint/cache (GET /reports/{scan_id} -> get_or_build_report)
  // for the rich Problem/Why/Fix/Implementation detail per signal — the SAME
  // build_recommendations()/fix_template data ReportView renders, never a second
  // recommendation engine or a new endpoint. Fetched ONCE per scan (not per accordion
  // click); only for single-page scans, since bulk scans don't render the signal-analysis
  // panel this enriches. Non-fatal if it fails — Scan Details' own native issues/evidence/
  // recommendations already render fully without it.
  const [report, setReport] = useState(null);
  const [reportLoaded, setReportLoaded] = useState(false);
  useEffect(() => {
    if (bulk || status !== "completed") return;
    let alive = true;
    setReport(null); setReportLoaded(false);
    getReport(scan.scan_id)
      .then((r) => { if (alive) { setReport(r); setReportLoaded(true); } })
      .catch(() => { if (alive) setReportLoaded(true); });
    return () => { alive = false; };
  }, [scan.scan_id, bulk, status]);

  const recsById = useMemo(() =>
    Object.fromEntries((report?.recommendations || []).map((r) => [r.id, r])), [report]);
  const aiById = useMemo(() =>
    Object.fromEntries((report?.ai?.issue_insights || []).map((i) => [i.id, i])), [report]);
  const lockedFixCount = report?.locked_recommendation_count || 0;

  // Persisted Fix Verification history for THIS scan as baseline — fetched ONCE
  // (not per signal, not per accordion click), so the latest verification survives a
  // page reload instead of living only in local component state. Newest first (server
  // order preserved) -> grouping by signal_id keeps each signal's full history while
  // its FIRST entry is that signal's latest result.
  const [verifications, setVerifications] = useState(null);
  const loadVerifications = useCallback(() => {
    if (bulk || status !== "completed") return;
    getVerifications(scan.scan_id)
      .then((r) => setVerifications(r.verifications || []))
      .catch(() => setVerifications([]));
  }, [scan.scan_id, bulk, status]);
  useEffect(() => { setVerifications(null); loadVerifications(); }, [loadVerifications]);

  const verificationsBySignal = useMemo(() => {
    const map = {};
    for (const v of verifications || []) (map[v.signal_id] || (map[v.signal_id] = [])).push(v);
    return map;
  }, [verifications]);

  // Fix Verification candidates: completed scans of the SAME site, strictly AFTER
  // this baseline scan — never assumed to be "the fix", just offered as options the
  // user explicitly picks (see verification.js docstring). Reuses the already-loaded
  // Recent Scans list (no new fetch); newest first. Compares `scan_time` (row.created_at)
  // on BOTH sides via the baseline's OWN list entry — never mixed with the detail
  // response's separate `scanned_at` field, which can differ slightly.
  const baselineListEntry = useMemo(() =>
    (scans || []).find((s) => s.id === scan.scan_id), [scans, scan.scan_id]);
  const verifyCandidates = useMemo(() => {
    if (!baselineListEntry) return [];
    const baselineTime = new Date(baselineListEntry.scan_time).getTime();
    return (scans || [])
      .filter((s) => s.id !== scan.scan_id && s.domain === scan.domain
        && (!s.status || s.status === "completed")
        && new Date(s.scan_time).getTime() > baselineTime)
      .sort((a, b) => new Date(b.scan_time) - new Date(a.scan_time));
  }, [scans, baselineListEntry, scan.scan_id, scan.domain]);

  if (status === "pending" || status === "running") {
    return <BulkScanProgress scan={scan} onBack={onBack} onDone={() => onRefresh?.(scan.scan_id)} />;
  }
  if (status === "failed") {
    return <BulkScanFailed scan={scan} onBack={onBack}
                           onRetry={onRetryBulk ? () => onRetryBulk(scan) : null} />;
  }
  const sections = scan.sections || [];
  const counts = sections.reduce((a, s) => { a[s.status] = (a[s.status] || 0) + 1; return a; }, {});
  const toggle = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));
  // A bulk scan reports the average as the headline and its own per-page table; a
  // single-page scan shows the full signal breakdown below.
  const headScore = bulk ? bulk.avg_score : scan.overall_score;
  const headStatus = scoreStatus(headScore);

  return (
    <div className="spectrum-screen">
      <Shell>
        <div className="sp-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to scans</Button>
          <div className="sp-sd-toolbar-r">
            {onReport && (
              <Button variant="ghost" onClick={onReport}><FileText size={13} /> View full report</Button>
            )}
            {canRun && (
              <Button variant="ghost" loading={busy} onClick={() => onRerun(scan.scan_id)}>
                {!busy && <RefreshCw size={13} />} Re-run scan
              </Button>
            )}
          </div>
        </div>

        <div className="sp-sd-stack">
          <Cell solid className="sp-sd-headcell">
            <span role="img" aria-label={`AI readiness score ${headScore} of 100 — ${headStatus}`}>
              <Ring value={headScore} size={96} stroke={8} />
            </span>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div className="sp-sd-domain">{scan.domain || scan.url}</div>
              <div className="sp-sd-headmeta">
                <Tag variant={STATUS_VARIANT[headStatus]}>{headStatus.toUpperCase()}</Tag>
                {bulk && <span className="sp-sd-dim">average across {bulk.page_count} page{bulk.page_count === 1 ? "" : "s"}</span>}
              </div>
              <div className="sp-sd-meta">
                scanner {scan.scanner_version || "-"} · rubric {scan.rubric_version} · {fmtDate(scan.scanned_at)} · {fmtDuration(scan.duration_ms)}
              </div>
            </div>
            {!bulk && (
              <div className="sp-sd-tallies">
                <SpTally n={counts.pass || 0} label="Passed" status="pass" />
                <SpTally n={counts.warn || 0} label="Warnings" status="warn" />
                <SpTally n={counts.fail || 0} label="Failures" status="fail" />
              </div>
            )}
          </Cell>

          {bulk && <BulkPages bulk={bulk} scanId={scan.scan_id} />}

          {/* AI Content Insights (Pro-only) for a single-page scan. */}
          {!bulk && <ContentInsightsCard scanId={scan.scan_id} />}

          {!bulk && (
            <Cell solid>
              <div className="sp-sd-panel-h">Diagnosis <span className="sp-sd-sub">10 checks</span></div>
              {/* Same existing Free/Pro report gate as ReportView's Recommendations section
                  (server-trimmed via gate_recommendations — never bypassed, never a blurred
                  fake). Only appears when the ALREADY-FETCHED report says some fixes were
                  left out for this tier; the basic issue/evidence/recommendation each signal
                  card shows below is never affected by this gate. */}
              {lockedFixCount > 0 && (
                <div className="sp-sd-lockbanner">
                  <Lock size={13} />
                  <span>{lockedFixCount} more detailed fix{lockedFixCount === 1 ? "" : "es"} available in the full report.</span>
                  <Button variant="accent" onClick={() => openUpgrade("report", { scanId: scan.scan_id })}>Unlock</Button>
                </div>
              )}
              <div className="sp-sd-list">
                {sections.map((s) => {
                  const isOpen = !!open[s.id];
                  const col = spStatusColor(s.status);
                  const clean = !(s.issues?.length) && !(s.recommendations?.length);
                  const evEntries = Object.entries(s.evidence || {}).filter(([k]) => k !== "detected_types");
                  const richRec = recsById[s.id];
                  return (
                    <div key={s.id} className="sp-sd-sig">
                      <button className="sp-sd-sig-head" onClick={() => toggle(s.id)} aria-expanded={isOpen}>
                        <span className="sp-sd-dot" style={{ background: col }} aria-hidden="true" />
                        <span className="sp-sd-sig-name">{s.label}</span>
                        <Tag variant={STATUS_VARIANT[s.status] || "info"}>{String(s.status).toUpperCase()}</Tag>
                        <span className="sp-sd-score" style={{ color: col }}>{s.score}</span>
                        <VerificationBadge verification={verificationsBySignal[s.id]?.[0]} />
                        <ChevronDown size={15} className="sp-sd-chev" style={{ transform: isOpen ? "rotate(180deg)" : "none" }} />
                      </button>
                      {isOpen && (
                        <div className="sp-sd-sig-body">
                          {/* Problem -> Fix -> Implementation -> Why it matters -> Evidence —
                              the SAME product-wide fix-first hierarchy DiagnosisCard/
                              RecommendationCard use everywhere else (ReportView.jsx). Problem
                              stays native to Scan Details (the scanner's own issue strings —
                              real, per-signal, never duplicated elsewhere) and is never
                              repeated by RecommendationCard (showProblem=false). Fix/
                              Implementation/Outcome/Why, when available, render via
                              <RecommendationCard>, the SAME component + SAME fix_template data
                              ReportView's Recommendations section uses — one source of truth,
                              never a second recommendation engine. "Found" (detected schema/
                              content types) is real evidence, not a problem statement, so it's
                              grouped with the native Evidence block at the end, not ahead of
                              the fix. */}
                          {s.issues?.length > 0 && (
                            <div><div className="sp-sd-bh">What&apos;s wrong</div>
                              <ul className="sp-sd-ul">{s.issues.map((it, i) => <li key={i}><AlertTriangle size={11} style={{ color: "var(--sp-lav-d)" }} /> <span>{it}</span></li>)}</ul></div>
                          )}
                          {clean && <div className="sp-sd-clean"><Check size={12} /> No issues found.</div>}

                          {richRec ? (
                            <div className="sp-sd-fix">
                              <RecommendationCard r={richRec} ins={aiById[richRec.id]} showProblem={false} />
                            </div>
                          ) : s.recommendations?.length > 0 ? (
                            <div><div className="sp-sd-bh">How to fix it</div>
                              <ul className="sp-sd-ul">{s.recommendations.map((r, i) => <li key={i}><Wrench size={11} style={{ color: "var(--sp-primary)" }} /> <span>{r}</span></li>)}</ul></div>
                          ) : !clean && reportLoaded && (
                            <div className="sp-sd-dim" style={{ fontSize: 12.5 }}>This check has no implementation example — see the issue above for what to address.</div>
                          )}

                          {s.evidence?.detected_types?.length > 0 && (
                            <div><div className="sp-sd-bh">Found</div>
                              <div className="sp-sd-found">{s.evidence.detected_types.map((t) => (
                                <span key={t} className="sp-sd-found-chip">{t}</span>
                              ))}</div></div>
                          )}
                          {evEntries.length > 0 ? (
                            <div><div className="sp-sd-bh">Evidence</div>
                              <div className="sp-sd-ev">{evEntries.map(([k, v]) => (
                                <div key={k} className="sp-sd-ev-row"><span className="sp-sd-ev-k">{k}</span><span className="sp-sd-ev-v">{fmtEvidence(v)}</span></div>
                              ))}</div></div>
                          ) : !clean && (
                            <div><div className="sp-sd-bh">Evidence</div>
                              <div className="sp-sd-dim" style={{ fontSize: 12.5 }}>Evidence unavailable for this check.</div></div>
                          )}

                          {/* Fix -> Implement -> Re-scan -> Verify. Only offered where there is
                              an actual problem to verify (never on a clean/passing signal). Never
                              claims anything until a real later scan is compared — see
                              verification.js and FixVerificationPanel below. */}
                          {!clean && (
                            <FixVerificationPanel signalId={s.id} label={s.label}
                              baselineScanId={scan.scan_id} candidates={verifyCandidates}
                              recommendation={richRec} history={verificationsBySignal[s.id] || []}
                              onRerun={canRun ? () => onRerun(scan.scan_id) : null} rerunBusy={busy}
                              onVerified={loadVerifications} />
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </Cell>
          )}
        </div>
      </Shell>
    </div>
  );
}

const VERIFY_COPY = {
  verified: { title: "Verified after re-scan", icon: CheckCircle2, tone: "ok" },
  partially_improved: { title: "Partially improved", icon: MinusCircle, tone: "warn" },
  unchanged: { title: "No meaningful change detected", icon: MinusCircle, tone: "info" },
  regressed: { title: "Regression detected", icon: XCircle, tone: "bad" },
};

/* Fix Verification — SCAN -> FIX -> RE-SCAN -> VERIFY -> PERSIST, scoped to one
   signal. Never claims anything (no "verified"/"fixed" text anywhere) until the user
   explicitly picks a later scan and POST /api/verifications computes + persists a
   real result server-side (app/services/verification.py — a 1:1 port of this same
   engine, never a second/divergent one). Same org-isolation, same metered quota as
   Compare (no new pricing rule). The LATEST persisted record (`history[0]`, passed
   from the parent's one-time GET /api/verifications) is shown by default, so the
   result survives a page reload instead of living only in local state. */
function FixVerificationPanel({ signalId, label, baselineScanId, candidates, recommendation, history, onRerun, rerunBusy, onVerified }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState(candidates[0]?.id || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notComparable, setNotComparable] = useState(false);
  const { handleGated } = useUpgrade();

  useEffect(() => { setSelectedId(candidates[0]?.id || ""); setNotComparable(false); }, [candidates]);

  // The LATEST persisted record (if any) is the default display — this is what makes
  // verification survive a reload instead of living only in local state. Running
  // "Verify" again adds a NEW record (see Phase 8: history), it never edits this one.
  const latest = history?.[0] || null;
  const showHistory = (history?.length || 0) > 1;

  const runVerify = async () => {
    if (!selectedId) return;
    setBusy(true); setError(null); setNotComparable(false);
    try {
      await createVerification({ baselineScanId, verificationScanId: selectedId, signalId });
      // The parent owns `verifications` (fetched once for the whole scan, not per
      // signal) as the single source of truth; ask it to reload rather than keeping
      // a second, locally-duplicated copy of the same persisted record here.
      onVerified?.();
    } catch (e) {
      if (e instanceof ScanError && e.code === 422) setNotComparable(true);
      else if (!handleGated(e, "compares")) setError(e instanceof ScanError ? e.message : "Could not verify.");
    } finally { setBusy(false); }
  };

  return (
    <div className="sp-sd-verify">
      <button className="sp-sd-verify-toggle" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
        <History size={12} /> {expanded ? "Hide verification" : latest ? "Verification" : "Verify after re-scan"}
      </button>
      {expanded && (
        <div className="sp-sd-verify-body">
          {latest && <VerificationResult result={latest} recommendation={recommendation} />}
          {showHistory && <VerificationHistory records={history} />}
          {candidates.length === 0 ? (
            <div className="sp-sd-verify-cta">
              <div className="sp-sd-dim" style={{ fontSize: 12.5 }}>
                Re-scan your website after implementing this fix. AEOMirror will compare the
                new scan with the previous evidence — implement the fix, then run a new scan
                to verify the change.
              </div>
              {onRerun && (
                <Button variant="ghost" loading={rerunBusy} onClick={onRerun}>
                  {!rerunBusy && <RefreshCw size={12} />} Run re-scan
                </Button>
              )}
            </div>
          ) : (
            <>
              <div className="sp-sd-verify-pick">
                <select className="sp-select" aria-label={`Pick a scan to verify ${label} against`}
                        value={selectedId} onChange={(e) => { setSelectedId(e.target.value); setNotComparable(false); }}>
                  {candidates.map((c) => (
                    <option key={c.id} value={c.id}>{fmtDate(c.scan_time)} · score {c.overall_score ?? "—"}</option>
                  ))}
                </select>
                <Button variant="accent" loading={busy} disabled={!selectedId} onClick={runVerify}>
                  {!busy && <History size={13} />} {latest ? "Verify again" : "Verify"}
                </Button>
              </div>
              {error && <div className="sp-sd-err">{error}</div>}
              {notComparable && (
                <div className="sp-sd-verify-nc"><AlertTriangle size={12} /> These scans cannot be compared reliably.</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function VerificationHistory({ records }) {
  return (
    <div className="sp-sd-verify-history">
      <div className="sp-sd-bh">Verification history</div>
      {records.map((r) => {
        const copy = VERIFY_COPY[r.verification_status];
        return (
          <div key={r.id} className="sp-sd-verify-hrow">
            <span className="sp-sd-dim">{fmtDate(r.created_at)}</span>
            <span>{r.score_before} → {r.score_after}</span>
            <span style={{ color: copy?.tone ? `var(--sp-${copy.tone === "ok" ? "blue" : copy.tone === "bad" ? "violet" : copy.tone === "warn" ? "lav" : "muted"}-d)` : undefined }}>
              {copy?.title || r.verification_status}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function VerificationResult({ result, recommendation }) {
  const copy = VERIFY_COPY[result.verification_status];
  const Icon = copy?.icon || MinusCircle;
  return (
    <div className={`sp-sd-verify-result sp-sd-verify-${copy?.tone || "info"}`}>
      <div className="sp-sd-verify-h"><Icon size={14} /> {copy?.title || result.verification_status}</div>
      <div className="sp-sd-verify-ba">
        <span>Before <b>{String(result.status_before).toUpperCase()} · {result.score_before}</b></span>
        <span className="sp-sd-dim">→</span>
        <span>After <b>{String(result.status_after).toUpperCase()} · {result.score_after}</b></span>
        <span className="sp-sd-dim">
          Score change: {result.score_delta > 0 ? "+" : ""}{result.score_delta}
        </span>
      </div>

      {result.resolved_issues.length > 0 && (
        <div className="sp-sd-verify-list">
          <div className="sp-sd-bh">Resolved</div>
          <ul className="sp-sd-ul">{result.resolved_issues.map((it, i) => (
            <li key={i}><Check size={11} style={{ color: "var(--sp-blue-d)" }} /> <span>{it}</span></li>
          ))}</ul>
        </div>
      )}
      {result.remaining_issues.length > 0 && (
        <div className="sp-sd-verify-list">
          <div className="sp-sd-bh">Still needs attention</div>
          <ul className="sp-sd-ul">{result.remaining_issues.map((it, i) => (
            <li key={i}><AlertTriangle size={11} style={{ color: "var(--sp-lav-d)" }} /> <span>{it}</span></li>
          ))}</ul>
        </div>
      )}
      {result.new_issues.length > 0 && (
        <div className="sp-sd-verify-list">
          <div className="sp-sd-bh">New issue detected in the verification scan</div>
          <ul className="sp-sd-ul">{result.new_issues.map((it, i) => (
            <li key={i}><AlertTriangle size={11} style={{ color: "var(--sp-violet-d)" }} /> <span>{it}</span></li>
          ))}</ul>
        </div>
      )}
      {result.evidence_changes.length > 0 && (
        <div className="sp-sd-verify-list">
          <div className="sp-sd-bh">Evidence</div>
          <div className="sp-sd-ev">{result.evidence_changes.map((c) => (
            <div key={c.key} className="sp-sd-ev-row">
              <span className="sp-sd-ev-k">{c.key}</span>
              <span className="sp-sd-ev-v">{fmtEvidence(c.before)} → {fmtEvidence(c.after)}</span>
            </div>
          ))}</div>
        </div>
      )}
      {result.verification_status === "regressed" && recommendation && (
        <div className="sp-sd-dim" style={{ fontSize: 12.5 }}>See the fix above for the recommended action.</div>
      )}
    </div>
  );
}

function SpTally({ n, label, status }) {
  return (
    <div className="sp-sd-tally">
      <div className="sp-sd-tally-n" style={{ color: spStatusColor(status) }}>{n}</div>
      <div className="sp-sd-tally-l">{label}</div>
    </div>
  );
}

function pagePath(u) {
  try { const x = new URL(u); return (x.pathname === "/" ? "/ (home)" : x.pathname) + (x.search || ""); }
  catch { return u; }
}

/* Live progress for a running bulk scan. Polls GET /api/scans/{id}/status every ~2.5s;
   when it finishes it calls onDone() so the parent reloads the full report (or the
   failed state). */
function BulkScanProgress({ scan, onBack, onDone }) {
  const [prog, setProg] = useState(scan.progress || null);
  const doneRef = useRef(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await getScanStatus(scan.scan_id);
        if (!alive) return;
        setProg(s.progress || null);
        if ((s.status === "completed" || s.status === "failed") && !doneRef.current) {
          doneRef.current = true;
          onDone?.();
          return;   // stop polling; parent will re-render with the terminal state
        }
      } catch { /* transient — keep polling */ }
      if (alive && !doneRef.current) timer = setTimeout(tick, 2500);
    };
    let timer = setTimeout(tick, 2500);
    return () => { alive = false; clearTimeout(timer); };
  }, [scan.scan_id, onDone]);

  const total = prog?.total ?? null;
  const done = prog?.done ?? 0;
  const failed = prog?.failed ?? 0;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : null;
  const current = prog?.current_url;

  return (
    <div className="spectrum-screen">
      <Shell>
        <div className="sp-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to scans</Button>
        </div>
        <Cell solid>
          <div className="sp-sd-center">
            <div className="sp-sd-ill"><Logo variant="icon" size={26} className="spin-slow" /></div>
            <div className="sp-sd-ct">Scanning your URLs…</div>
            <div className="sp-sd-cs">Bulk scan in progress</div>

            <div className="sp-sd-prog">
              {pct == null
                ? <div className="sp-pg" role="progressbar" aria-label="Bulk scan in progress"><div className="sp-pgf sp-sd-indet" /></div>
                : <ProgressBar value={pct} />}
              <div className="sp-sd-prog-label">
                {total ? `Scanning ${done} of ${total} pages…` : `Scanning ${done} page${done === 1 ? "" : "s"}…`}
                {failed > 0 && <span className="sp-sd-dim"> · {failed} unreachable</span>}
              </div>
              {current && <div className="sp-sd-prog-url">{pagePath(current)}</div>}
            </div>
          </div>
        </Cell>
      </Shell>
    </div>
  );
}

/* Terminal error state for a bulk scan that exhausted its retries. */
function BulkScanFailed({ scan, onBack, onRetry }) {
  const msg = scan.error || "The bulk scan could not be completed.";
  return (
    <div className="spectrum-screen">
      <Shell>
        <div className="sp-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to scans</Button>
        </div>
        <Cell solid>
          <div className="sp-sd-center">
            <div className="sp-sd-ill sp-sd-ill-bad"><AlertTriangle size={26} /></div>
            <div className="sp-sd-ct">Scan failed</div>
            <div className="sp-sd-cs">{msg}</div>
            {onRetry && <Button variant="accent" onClick={onRetry}><RefreshCw size={15} /> Retry bulk scan</Button>}
          </div>
        </Cell>
      </Shell>
    </div>
  );
}

function urlLabel(u) {
  try { const x = new URL(u); return x.hostname.replace(/^www\./, "") + (x.pathname === "/" ? "" : x.pathname); }
  catch { return u; }
}

/* Bulk scan results: average headline + best/worst + a sortable per-URL table. Scores
   and top-issue one-liners show for everyone; the FULL per-page signal breakdown is a
   Pro feature (details_locked, set + stripped by the server) — Free orgs see a lock
   and a blurred teaser that opens the upgrade modal. */
function BulkPages({ bulk, scanId }) {
  const { openUpgrade } = useUpgrade();
  const [openUrl, setOpenUrl] = useState(null);
  const [sort, setSort] = useState("score-asc");   // score-asc | score-desc | url
  const locked = !!bulk.details_locked;
  const pages = bulk.pages || [];

  const rows = [...pages].sort((a, b) => {
    if (sort === "url") return urlLabel(a.url).localeCompare(urlLabel(b.url));
    const av = a.error ? -1 : a.overall_score, bv = b.error ? -1 : b.overall_score;
    return sort === "score-desc" ? bv - av : av - bv;   // errors sort to the bottom of asc
  });
  const openPage = !locked && pages.find((p) => p.url === openUrl && !p.error);
  const unlock = () => openUpgrade("page_details");

  return (
    <Cell solid>
      <div className="sp-sd-panel-h">
        Pages scanned
        <span className="sp-sd-sub">
          {bulk.page_count} of {bulk.requested ?? pages.length} URL{(bulk.requested ?? pages.length) === 1 ? "" : "s"} scored · avg {bulk.avg_score ?? "—"}
          {bulk.truncated && <span className="sp-sd-dim"> · partial (time budget reached)</span>}
        </span>
      </div>

      <div className="sp-sd-best">
        {bulk.best && <span className="sp-sd-bestchip good"><ArrowUp size={12} /> Best {bulk.best.score} · {urlLabel(bulk.best.url)}</span>}
        {bulk.worst && <span className="sp-sd-bestchip bad"><ArrowDown size={12} /> Worst {bulk.worst.score} · {urlLabel(bulk.worst.url)}</span>}
      </div>

      {locked && (
        <div className="sp-sd-lockbanner">
          <Lock size={13} />
          <span>Scores shown for all pages — detailed breakdowns are a Pro feature.</span>
          <Button variant="accent" onClick={unlock}>Unlock</Button>
        </div>
      )}

      <div className="sp-sd-tablewrap">
        <table className="sp-sd-table">
          <thead><tr>
            <th>URL</th>
            <th className="sp-sd-th-sort" onClick={() => setSort(sort === "score-asc" ? "score-desc" : "score-asc")}>
              Score {sort.startsWith("score") ? (sort === "score-asc" ? "▲" : "▼") : ""}
            </th>
            <th>Status</th><th>Top issue</th><th className="sp-sd-td-right">Details</th>
          </tr></thead>
          <tbody>
            {rows.map((p) => {
              if (p.error) {
                return (
                  <tr key={p.url}>
                    <td><span className="sp-sd-url">{urlLabel(p.url)}</span></td>
                    <td className="sp-sd-dim">—</td>
                    <td><Tag variant="critical">ERROR</Tag></td>
                    <td className="sp-sd-meta" colSpan={2}>{p.error}</td>
                  </tr>
                );
              }
              const isOpen = openUrl === p.url;
              const st = p.status_label || scoreStatus(p.overall_score);
              return (
                <tr key={p.url}>
                  <td><span className="sp-sd-url">{urlLabel(p.url)}</span></td>
                  <td><span role="img" aria-label={`Score ${p.overall_score} of 100`}><Ring value={p.overall_score} size={30} /></span></td>
                  <td><Tag variant={STATUS_VARIANT[st] || "info"}>{String(st).toUpperCase()}</Tag></td>
                  <td className="sp-sd-topissue">{p.top_issue || "—"}</td>
                  <td className="sp-sd-td-right">
                    {locked
                      ? <Button variant="ghost" onClick={unlock} title="Detailed reports are a Pro feature"><Lock size={12} /> Details</Button>
                      : <Button variant="ghost" onClick={() => setOpenUrl(isOpen ? null : p.url)}>{isOpen ? "Hide" : "View details"}</Button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {locked && <LockedSectionsTeaser onUnlock={unlock} />}

      {openPage && (
        <div style={{ marginTop: 12 }}>
          <div className="sp-sd-panel-h" style={{ fontSize: 13 }}>{urlLabel(openPage.url)} · signals</div>
          <div className="sp-sd-list">
            {(openPage.sections_summary || []).map((s) => {
              const col = spStatusColor(s.status);
              return (
                <div key={s.id} className="sp-sd-sig" style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "9px 12px", flexWrap: "wrap" }}>
                  <span className="sp-sd-dot" style={{ background: col, marginTop: 4 }} aria-hidden="true" />
                  <span className="sp-sd-sig-name" style={{ flex: 1, minWidth: 140 }}>{s.label}</span>
                  <Tag variant={STATUS_VARIANT[s.status] || "info"}>{String(s.status).toUpperCase()}</Tag>
                  <span className="sp-sd-score" style={{ color: col }}>{s.score}</span>
                  {s.issues?.length > 0 && (
                    <ul className="sp-sd-ul" style={{ flexBasis: "100%", marginTop: 4 }}>
                      {s.issues.map((it, i) => <li key={i}><AlertTriangle size={11} style={{ color: "var(--sp-lav-d)" }} /> <span>{it}</span></li>)}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
          {/* AI Content Insights for the opened bulk page (Pro-only). */}
          <ContentInsightsCard scanId={scanId} pageUrl={openPage.url} />
        </div>
      )}
    </Cell>
  );
}

/* Blurred skeleton teaser for locked per-page details (reuses the report blur+overlay
   pattern). Purely placeholder content — the real locked data is never sent to the
   client, so there is nothing to un-blur in the DOM. */
function LockedSectionsTeaser({ onUnlock }) {
  return (
    <div className="sp-sd-lockwrap">
      <div className="sp-sd-blur sp-sd-list" aria-hidden="true">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="sp-sd-sig" style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px" }}>
            <span className="sp-sd-dot" style={{ background: "var(--sp-line-2)" }} />
            <span className="sp-sd-sig-name" style={{ flex: 1 }}>Full signal breakdown</span>
            <Tag variant="info">••••</Tag>
            <span className="sp-sd-score" style={{ color: "var(--sp-muted)" }}>••</span>
          </div>
        ))}
      </div>
      <div className="sp-sd-lock-overlay">
        <div className="sp-sd-lock-t">Detailed page reports are a Pro feature</div>
        <div className="sp-sd-lock-s">See the full signal breakdown for every page — issues, fixes and evidence.</div>
        <Button variant="accent" onClick={onUnlock}><Lock size={14} /> Unlock detailed reports</Button>
      </div>
    </div>
  );
}

/* Route-level LOADING state (Spectrum) — replaces the shared <TableSkeleton> for this route
   only, so the shared primitive stays untouched for other screens. */
export function ScanDetailLoading() {
  return (
    <div className="spectrum-screen">
      <Shell>
        <div className="sp-sd-toolbar"><Skeleton w={130} h={30} /></div>
        <div className="sp-sd-stack">
          <Cell solid className="sp-sd-headcell">
            <Skeleton w={96} h={96} style={{ borderRadius: "50%" }} />
            <div style={{ flex: 1, minWidth: 220, display: "grid", gap: 10 }}>
              <Skeleton w="45%" h={22} /><Skeleton w="70%" h={12} />
            </div>
          </Cell>
          <Cell solid>
            <div className="sp-sd-skrows">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} h={46} />)}</div>
          </Cell>
        </div>
      </Shell>
    </div>
  );
}

/* Route-level ERROR / not-found state (Spectrum) — copy matches the shared NotFoundState
   verbatim (title + label + back link); only the skin changes. */
export function ScanDetailNotFound({ label }) {
  return (
    <div className="spectrum-screen">
      <Shell>
        <Cell solid>
          <div className="sp-sd-center">
            <div className="sp-sd-ill sp-sd-ill-bad"><AlertTriangle size={26} /></div>
            <div className="sp-sd-ct">Not found or no access</div>
            <div className="sp-sd-cs">{label || "This item doesn’t exist, or it belongs to another workspace."}</div>
            <Link className="sp-btn sp-gradient" to="/app/dashboard">Back to dashboard</Link>
          </div>
        </Cell>
      </Shell>
    </div>
  );
}
