/* Scan Details (Phase 4): full report for one stored scan — overall score, signal
   cards with issues/recommendations/evidence, and pass/warn/fail summary. */
import React, { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronDown, AlertTriangle, Wrench, Check, RefreshCw, FileText, Radar, ArrowUp, ArrowDown, Lock } from "lucide-react";
import { ScoreRing, StatusBadge, fmtDate, fmtDuration, statusColor } from "./ui.jsx";
import { getScanStatus } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { ContentInsightsCard } from "./ContentInsights.jsx";

function fmtEvidence(v) {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (v == null) return "-";
  if (Array.isArray(v)) return v.length ? `${v.length}: ${v.slice(0, 3).join(", ")}${v.length > 3 ? "…" : ""}` : "none";
  if (typeof v === "object") return Object.entries(v).map(([k, val]) => `${k}=${val}`).join(", ");
  return String(v);
}

export default function ScanDetails({ scan, onBack, onRerun, busy, canRun = true, onReport,
                                      onRefresh, onRetryBulk }) {
  const [open, setOpen] = useState({});
  // A bulk scan runs in the background: show live progress while it is
  // pending/running, and an error + retry if it failed. Single-page scans are
  // always "completed" and fall straight through to the report below.
  const status = scan.status || "completed";
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
  const bulk = scan.bulk || null;
  const headScore = bulk ? bulk.avg_score : scan.overall_score;

  return (
    <div>
      <div className="d-toolbar" style={{ justifyContent: "space-between" }}>
        <button className="d-iconbtn" onClick={onBack}><ChevronLeft size={14} /> Back to scans</button>
        <div style={{ display: "flex", gap: 8 }}>
          {onReport && (
            <button className="d-iconbtn" onClick={onReport}>
              <FileText size={13} /> View full report
            </button>
          )}
          {canRun && (
            <button className="d-iconbtn" disabled={busy} onClick={() => onRerun(scan.scan_id)}>
              <RefreshCw size={13} className={busy ? "spin-slow" : ""} /> Re-run scan
            </button>
          )}
        </div>
      </div>

      <div className="d-panel" style={{ display: "flex", alignItems: "center", gap: 22, flexWrap: "wrap" }}>
        <ScoreRing value={headScore} size={96} stroke={8} />
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ fontSize: 20, fontWeight: 700, fontFamily: "'Hanken Grotesk'" }}>{scan.domain || scan.url}</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "8px 0", flexWrap: "wrap" }}>
            <StatusBadge status={headScore >= 75 ? "pass" : headScore >= 45 ? "warn" : "fail"} />
            {bulk && <span className="d-dim" style={{ fontSize: 12 }}>average across {bulk.page_count} page{bulk.page_count === 1 ? "" : "s"}</span>}
          </div>
          <div className="d-dim d-mono" style={{ fontSize: 11.5 }}>
            scanner {scan.scanner_version || "-"} · rubric {scan.rubric_version} · {fmtDate(scan.scanned_at)} · {fmtDuration(scan.duration_ms)}
          </div>
        </div>
        {!bulk && (
          <div style={{ display: "flex", gap: 18 }}>
            <Tally n={counts.pass || 0} label="Passed" color="var(--good)" />
            <Tally n={counts.warn || 0} label="Warnings" color="var(--warn)" />
            <Tally n={counts.fail || 0} label="Failures" color="var(--bad)" />
          </div>
        )}
      </div>

      {bulk && <BulkPages bulk={bulk} scanId={scan.scan_id} />}

      {/* AI Content Insights (Pro-only) for a single-page scan. */}
      {!bulk && <ContentInsightsCard scanId={scan.scan_id} />}

      {!bulk && (
      <div className="d-panel" style={{ marginTop: 16 }}>
        <div className="d-panel-h">Signal analysis <span className="sub">10 checks</span></div>
        <div className="d-list">
          {sections.map((s) => {
            const isOpen = !!open[s.id];
            const col = statusColor(s.status);
            const clean = !(s.issues?.length) && !(s.recommendations?.length);
            return (
              <div key={s.id} className="d-sig">
                <button className="d-sig-head" onClick={() => toggle(s.id)} aria-expanded={isOpen}>
                  <span className="d-sig-dot" style={{ background: col }} />
                  <span className="d-sig-name">{s.label}</span>
                  <span className="d-badge" style={{ color: col, borderColor: col }}>{s.status.toUpperCase()}</span>
                  <span className="d-score" style={{ color: col, width: 28, textAlign: "right" }}>{s.score}</span>
                  <ChevronDown size={15} style={{ color: "var(--txt-dim)", transition: "transform .2s", transform: isOpen ? "rotate(180deg)" : "none" }} />
                </button>
                {isOpen && (
                  <div className="d-sig-body">
                    {s.issues?.length > 0 && (
                      <div><div className="d-sig-bh">Issues</div>
                        <ul className="d-sig-ul">{s.issues.map((it, i) => <li key={i}><AlertTriangle size={11} style={{ color: "var(--warn)" }} /> <span>{it}</span></li>)}</ul></div>
                    )}
                    {s.recommendations?.length > 0 && (
                      <div><div className="d-sig-bh">Recommendations</div>
                        <ul className="d-sig-ul">{s.recommendations.map((r, i) => <li key={i}><Wrench size={11} style={{ color: "var(--accent)" }} /> <span>{r}</span></li>)}</ul></div>
                    )}
                    {clean && <div style={{ display: "flex", alignItems: "center", gap: 7, color: "var(--good)", fontSize: 12.5 }}><Check size={12} /> No issues found.</div>}
                    {s.evidence?.detected_types?.length > 0 && (
                      <div><div className="d-sig-bh">Found</div>
                        <div className="d-found">{s.evidence.detected_types.map((t) => (
                          <span key={t} className="d-found-chip">{t}</span>
                        ))}</div></div>
                    )}
                    {s.evidence && Object.keys(s.evidence).length > 0 && (
                      <div><div className="d-sig-bh">Evidence</div>
                        <div className="d-ev">{Object.entries(s.evidence).filter(([k]) => k !== "detected_types").map(([k, v]) => (
                          <div key={k} className="d-ev-row"><span className="d-ev-k">{k}</span><span className="d-ev-v">{fmtEvidence(v)}</span></div>
                        ))}</div></div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
      )}
    </div>
  );
}

function Tally({ n, label, color }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 24, fontWeight: 800, color, fontFamily: "'Hanken Grotesk'" }}>{n}</div>
      <div className="d-dim" style={{ fontSize: 11 }}>{label}</div>
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
    <div>
      <div className="d-toolbar">
        <button className="d-iconbtn" onClick={onBack}><ChevronLeft size={14} /> Back to scans</button>
      </div>
      <div className="d-panel" style={{ textAlign: "center", padding: "40px 24px" }}>
        <div className="d-mini-empty-ill" style={{ margin: "0 auto 16px" }}>
          <Radar size={26} className="spin-slow" />
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, fontFamily: "'Hanken Grotesk'" }}>
          Scanning your URLs…
        </div>
        <div className="d-dim" style={{ fontSize: 13, margin: "6px 0 18px" }}>
          Bulk scan in progress
        </div>

        <div className="site-prog" role="progressbar" aria-valuenow={pct ?? undefined}>
          <div className="site-prog-bar">
            {pct == null
              ? <div className="site-prog-fill indeterminate" />
              : <div className="site-prog-fill" style={{ width: `${pct}%` }} />}
          </div>
          <div className="site-prog-label d-mono">
            {total ? `Scanning ${done} of ${total} pages…` : `Scanning ${done} page${done === 1 ? "" : "s"}…`}
            {failed > 0 && <span className="d-dim"> · {failed} unreachable</span>}
          </div>
          {current && <div className="site-prog-url d-dim d-mono">{pagePath(current)}</div>}
        </div>
      </div>
    </div>
  );
}

/* Terminal error state for a bulk scan that exhausted its retries. */
function BulkScanFailed({ scan, onBack, onRetry }) {
  const msg = scan.error || "The bulk scan could not be completed.";
  return (
    <div>
      <div className="d-toolbar">
        <button className="d-iconbtn" onClick={onBack}><ChevronLeft size={14} /> Back to scans</button>
      </div>
      <div className="d-panel" style={{ textAlign: "center", padding: "40px 24px" }}>
        <div className="d-mini-empty-ill" style={{ margin: "0 auto 16px", background: "rgba(229,97,91,.12)", color: "var(--bad)" }}>
          <AlertTriangle size={26} />
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, fontFamily: "'Hanken Grotesk'" }}>Scan failed</div>
        <div className="d-dim" style={{ fontSize: 13, marginBottom: 18, marginTop: 6 }}>{msg}</div>
        {onRetry && <button className="dash-newscan" style={{ display: "inline-flex" }} onClick={onRetry}>
          <RefreshCw size={15} /> Retry bulk scan
        </button>}
      </div>
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
    <div className="d-panel" style={{ marginTop: 16 }}>
      <div className="d-panel-h">
        Pages scanned
        <span className="sub">
          {bulk.page_count} of {bulk.requested ?? pages.length} URL{(bulk.requested ?? pages.length) === 1 ? "" : "s"} scored · avg {bulk.avg_score ?? "—"}
          {bulk.truncated && <span className="d-dim"> · partial (time budget reached)</span>}
        </span>
      </div>

      <div className="bulk-chips">
        {bulk.best && <span className="bulk-chip good"><ArrowUp size={12} /> Best {bulk.best.score} · {urlLabel(bulk.best.url)}</span>}
        {bulk.worst && <span className="bulk-chip bad"><ArrowDown size={12} /> Worst {bulk.worst.score} · {urlLabel(bulk.worst.url)}</span>}
      </div>

      {locked && (
        <div className="bulk-lock-banner">
          <Lock size={13} />
          <span>Scores shown for all pages — detailed breakdowns are a Pro feature.</span>
          <button className="bulk-lock-unlock" onClick={unlock}>Unlock</button>
        </div>
      )}

      <div className="d-table-wrap">
        <table className="d-table">
          <thead><tr>
            <th>URL</th>
            <th style={{ cursor: "pointer" }} onClick={() => setSort(sort === "score-asc" ? "score-desc" : "score-asc")}>
              Score {sort.startsWith("score") ? (sort === "score-asc" ? "▲" : "▼") : ""}
            </th>
            <th>Status</th><th>Top issue</th><th style={{ textAlign: "right" }}>Details</th>
          </tr></thead>
          <tbody>
            {rows.map((p) => {
              if (p.error) {
                return (
                  <tr key={p.url}>
                    <td><span className="d-url">{urlLabel(p.url)}</span></td>
                    <td className="d-dim">—</td>
                    <td><span className="d-badge" style={{ color: "var(--bad)", borderColor: "var(--bad)" }}>ERROR</span></td>
                    <td className="d-dim d-mono" style={{ fontSize: 11 }} colSpan={2}>{p.error}</td>
                  </tr>
                );
              }
              const isOpen = openUrl === p.url;
              return (
                <tr key={p.url}>
                  <td><span className="d-url">{urlLabel(p.url)}</span></td>
                  <td><ScoreRing value={p.overall_score} size={30} /></td>
                  <td><StatusBadge status={p.status_label || (p.overall_score >= 75 ? "pass" : p.overall_score >= 45 ? "warn" : "fail")} /></td>
                  <td className="d-dim" style={{ fontSize: 12, maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.top_issue || "—"}</td>
                  <td style={{ textAlign: "right" }}>
                    {locked
                      ? <button className="d-iconbtn" onClick={unlock} title="Detailed reports are a Pro feature"><Lock size={12} className="lock-i" /> Details</button>
                      : <button className="d-iconbtn" onClick={() => setOpenUrl(isOpen ? null : p.url)}>{isOpen ? "Hide" : "View details"}</button>}
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
          <div className="d-panel-h" style={{ fontSize: 12.5 }}>{urlLabel(openPage.url)} · signals</div>
          <div className="d-list">
            {(openPage.sections_summary || []).map((s) => {
              const col = statusColor(s.status);
              return (
                <div key={s.id} className="d-sig" style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "9px 12px", flexWrap: "wrap" }}>
                  <span className="d-sig-dot" style={{ background: col, marginTop: 4 }} />
                  <span className="d-sig-name" style={{ flex: 1, minWidth: 140 }}>{s.label}</span>
                  <span className="d-badge" style={{ color: col, borderColor: col }}>{String(s.status).toUpperCase()}</span>
                  <span className="d-score" style={{ color: col, width: 28, textAlign: "right" }}>{s.score}</span>
                  {s.issues?.length > 0 && (
                    <ul className="d-sig-ul" style={{ flexBasis: "100%", marginTop: 4 }}>
                      {s.issues.map((it, i) => <li key={i}><AlertTriangle size={11} style={{ color: "var(--warn)" }} /> <span>{it}</span></li>)}
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
    </div>
  );
}

/* Blurred skeleton teaser for locked per-page details (reuses the report blur+overlay
   pattern). Purely placeholder content — the real locked data is never sent to the
   client, so there is nothing to un-blur in the DOM. */
function LockedSectionsTeaser({ onUnlock }) {
  return (
    <div className="rep-lockwrap" style={{ marginTop: 12 }}>
      <div className="rep-blur d-list" aria-hidden="true">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="d-sig" style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px" }}>
            <span className="d-sig-dot" style={{ background: "var(--line-2)" }} />
            <span className="d-sig-name" style={{ flex: 1 }}>Full signal breakdown</span>
            <span className="d-badge" style={{ color: "var(--txt-dim)", borderColor: "var(--line-2)" }}>••••</span>
            <span className="d-score" style={{ color: "var(--txt-dim)", width: 28, textAlign: "right" }}>••</span>
          </div>
        ))}
      </div>
      <div className="rep-lock-overlay">
        <div className="rep-lock-t">Detailed page reports are a Pro feature</div>
        <div className="rep-lock-s">See the full signal breakdown for every page — issues, fixes and evidence.</div>
        <button className="btn btn-primary" onClick={onUnlock}><Lock size={14} /> Unlock detailed reports</button>
      </div>
    </div>
  );
}
