/* Scan Details (/app/scans/:scanId) — MIGRATED to the Aurora design system.
   Data flow, hooks, effects and handlers are byte-for-byte unchanged from the pre-Aurora
   version; only JSX + class names changed (dark d-* classes -> Aurora primitives + au-sd-*).
   Route-level loading + error states are exported here (ScanDetailLoading / ScanDetailNotFound)
   so the route can render them in Aurora too, without touching the shared ui.jsx primitives.
   Out of scope (still dark, flagged): <ContentInsightsCard> is a separate component. */
import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronDown, AlertTriangle, Wrench, Check, RefreshCw, FileText, Radar, ArrowUp, ArrowDown, Lock } from "lucide-react";
import { fmtDate, fmtDuration } from "./ui.jsx";
import { getScanStatus } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { ContentInsightsCard } from "./ContentInsights.jsx";
import { Shell, Cell, Button, Tag, Ring, ProgressBar, Skeleton } from "./aurora.jsx";
import "./ScanDetails.aurora.css";

const STATUS_VARIANT = { pass: "ok", warn: "warning", fail: "critical" };
const auStatusColor = (s) => s === "pass" ? "var(--au-mint-d)" : s === "warn" ? "var(--au-lemon-d)"
  : s === "fail" ? "var(--au-peach-d)" : "var(--au-muted)";
const scoreStatus = (v) => (v >= 75 ? "pass" : v >= 45 ? "warn" : "fail");

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
  const headStatus = scoreStatus(headScore);

  return (
    <div className="aurora-screen">
      <Shell>
        <div className="au-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to scans</Button>
          <div className="au-sd-toolbar-r">
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

        <div className="au-sd-stack">
          <Cell solid className="au-sd-headcell">
            <span role="img" aria-label={`AI readiness score ${headScore} of 100 — ${headStatus}`}>
              <Ring value={headScore} size={96} stroke={8} />
            </span>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div className="au-sd-domain">{scan.domain || scan.url}</div>
              <div className="au-sd-headmeta">
                <Tag variant={STATUS_VARIANT[headStatus]}>{headStatus.toUpperCase()}</Tag>
                {bulk && <span className="au-sd-dim">average across {bulk.page_count} page{bulk.page_count === 1 ? "" : "s"}</span>}
              </div>
              <div className="au-sd-meta">
                scanner {scan.scanner_version || "-"} · rubric {scan.rubric_version} · {fmtDate(scan.scanned_at)} · {fmtDuration(scan.duration_ms)}
              </div>
            </div>
            {!bulk && (
              <div className="au-sd-tallies">
                <AuTally n={counts.pass || 0} label="Passed" status="pass" />
                <AuTally n={counts.warn || 0} label="Warnings" status="warn" />
                <AuTally n={counts.fail || 0} label="Failures" status="fail" />
              </div>
            )}
          </Cell>

          {bulk && <BulkPages bulk={bulk} scanId={scan.scan_id} />}

          {/* AI Content Insights (Pro-only) for a single-page scan. */}
          {!bulk && <ContentInsightsCard scanId={scan.scan_id} />}

          {!bulk && (
            <Cell solid>
              <div className="au-sd-panel-h">Signal analysis <span className="au-sd-sub">10 checks</span></div>
              <div className="au-sd-list">
                {sections.map((s) => {
                  const isOpen = !!open[s.id];
                  const col = auStatusColor(s.status);
                  const clean = !(s.issues?.length) && !(s.recommendations?.length);
                  return (
                    <div key={s.id} className="au-sd-sig">
                      <button className="au-sd-sig-head" onClick={() => toggle(s.id)} aria-expanded={isOpen}>
                        <span className="au-sd-dot" style={{ background: col }} aria-hidden="true" />
                        <span className="au-sd-sig-name">{s.label}</span>
                        <Tag variant={STATUS_VARIANT[s.status] || "info"}>{String(s.status).toUpperCase()}</Tag>
                        <span className="au-sd-score" style={{ color: col }}>{s.score}</span>
                        <ChevronDown size={15} className="au-sd-chev" style={{ transform: isOpen ? "rotate(180deg)" : "none" }} />
                      </button>
                      {isOpen && (
                        <div className="au-sd-sig-body">
                          {s.issues?.length > 0 && (
                            <div><div className="au-sd-bh">Issues</div>
                              <ul className="au-sd-ul">{s.issues.map((it, i) => <li key={i}><AlertTriangle size={11} style={{ color: "var(--au-lemon-d)" }} /> <span>{it}</span></li>)}</ul></div>
                          )}
                          {s.recommendations?.length > 0 && (
                            <div><div className="au-sd-bh">Recommendations</div>
                              <ul className="au-sd-ul">{s.recommendations.map((r, i) => <li key={i}><Wrench size={11} style={{ color: "var(--au-primary)" }} /> <span>{r}</span></li>)}</ul></div>
                          )}
                          {clean && <div className="au-sd-clean"><Check size={12} /> No issues found.</div>}
                          {s.evidence?.detected_types?.length > 0 && (
                            <div><div className="au-sd-bh">Found</div>
                              <div className="au-sd-found">{s.evidence.detected_types.map((t) => (
                                <span key={t} className="au-sd-found-chip">{t}</span>
                              ))}</div></div>
                          )}
                          {s.evidence && Object.keys(s.evidence).length > 0 && (
                            <div><div className="au-sd-bh">Evidence</div>
                              <div className="au-sd-ev">{Object.entries(s.evidence).filter(([k]) => k !== "detected_types").map(([k, v]) => (
                                <div key={k} className="au-sd-ev-row"><span className="au-sd-ev-k">{k}</span><span className="au-sd-ev-v">{fmtEvidence(v)}</span></div>
                              ))}</div></div>
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

function AuTally({ n, label, status }) {
  return (
    <div className="au-sd-tally">
      <div className="au-sd-tally-n" style={{ color: auStatusColor(status) }}>{n}</div>
      <div className="au-sd-tally-l">{label}</div>
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
    <div className="aurora-screen">
      <Shell>
        <div className="au-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to scans</Button>
        </div>
        <Cell solid>
          <div className="au-sd-center">
            <div className="au-sd-ill"><Radar size={26} className="spin-slow" /></div>
            <div className="au-sd-ct">Scanning your URLs…</div>
            <div className="au-sd-cs">Bulk scan in progress</div>

            <div className="au-sd-prog">
              {pct == null
                ? <div className="au-pg" role="progressbar" aria-label="Bulk scan in progress"><div className="au-pgf au-sd-indet" /></div>
                : <ProgressBar value={pct} />}
              <div className="au-sd-prog-label">
                {total ? `Scanning ${done} of ${total} pages…` : `Scanning ${done} page${done === 1 ? "" : "s"}…`}
                {failed > 0 && <span className="au-sd-dim"> · {failed} unreachable</span>}
              </div>
              {current && <div className="au-sd-prog-url">{pagePath(current)}</div>}
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
    <div className="aurora-screen">
      <Shell>
        <div className="au-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to scans</Button>
        </div>
        <Cell solid>
          <div className="au-sd-center">
            <div className="au-sd-ill au-sd-ill-bad"><AlertTriangle size={26} /></div>
            <div className="au-sd-ct">Scan failed</div>
            <div className="au-sd-cs">{msg}</div>
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
      <div className="au-sd-panel-h">
        Pages scanned
        <span className="au-sd-sub">
          {bulk.page_count} of {bulk.requested ?? pages.length} URL{(bulk.requested ?? pages.length) === 1 ? "" : "s"} scored · avg {bulk.avg_score ?? "—"}
          {bulk.truncated && <span className="au-sd-dim"> · partial (time budget reached)</span>}
        </span>
      </div>

      <div className="au-sd-best">
        {bulk.best && <span className="au-sd-bestchip good"><ArrowUp size={12} /> Best {bulk.best.score} · {urlLabel(bulk.best.url)}</span>}
        {bulk.worst && <span className="au-sd-bestchip bad"><ArrowDown size={12} /> Worst {bulk.worst.score} · {urlLabel(bulk.worst.url)}</span>}
      </div>

      {locked && (
        <div className="au-sd-lockbanner">
          <Lock size={13} />
          <span>Scores shown for all pages — detailed breakdowns are a Pro feature.</span>
          <Button variant="accent" onClick={unlock}>Unlock</Button>
        </div>
      )}

      <div className="au-sd-tablewrap">
        <table className="au-sd-table">
          <thead><tr>
            <th>URL</th>
            <th className="au-sd-th-sort" onClick={() => setSort(sort === "score-asc" ? "score-desc" : "score-asc")}>
              Score {sort.startsWith("score") ? (sort === "score-asc" ? "▲" : "▼") : ""}
            </th>
            <th>Status</th><th>Top issue</th><th className="au-sd-td-right">Details</th>
          </tr></thead>
          <tbody>
            {rows.map((p) => {
              if (p.error) {
                return (
                  <tr key={p.url}>
                    <td><span className="au-sd-url">{urlLabel(p.url)}</span></td>
                    <td className="au-sd-dim">—</td>
                    <td><Tag variant="critical">ERROR</Tag></td>
                    <td className="au-sd-meta" colSpan={2}>{p.error}</td>
                  </tr>
                );
              }
              const isOpen = openUrl === p.url;
              const st = p.status_label || scoreStatus(p.overall_score);
              return (
                <tr key={p.url}>
                  <td><span className="au-sd-url">{urlLabel(p.url)}</span></td>
                  <td><span role="img" aria-label={`Score ${p.overall_score} of 100`}><Ring value={p.overall_score} size={30} /></span></td>
                  <td><Tag variant={STATUS_VARIANT[st] || "info"}>{String(st).toUpperCase()}</Tag></td>
                  <td className="au-sd-topissue">{p.top_issue || "—"}</td>
                  <td className="au-sd-td-right">
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
          <div className="au-sd-panel-h" style={{ fontSize: 13 }}>{urlLabel(openPage.url)} · signals</div>
          <div className="au-sd-list">
            {(openPage.sections_summary || []).map((s) => {
              const col = auStatusColor(s.status);
              return (
                <div key={s.id} className="au-sd-sig" style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "9px 12px", flexWrap: "wrap" }}>
                  <span className="au-sd-dot" style={{ background: col, marginTop: 4 }} aria-hidden="true" />
                  <span className="au-sd-sig-name" style={{ flex: 1, minWidth: 140 }}>{s.label}</span>
                  <Tag variant={STATUS_VARIANT[s.status] || "info"}>{String(s.status).toUpperCase()}</Tag>
                  <span className="au-sd-score" style={{ color: col }}>{s.score}</span>
                  {s.issues?.length > 0 && (
                    <ul className="au-sd-ul" style={{ flexBasis: "100%", marginTop: 4 }}>
                      {s.issues.map((it, i) => <li key={i}><AlertTriangle size={11} style={{ color: "var(--au-lemon-d)" }} /> <span>{it}</span></li>)}
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
    <div className="au-sd-lockwrap">
      <div className="au-sd-blur au-sd-list" aria-hidden="true">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="au-sd-sig" style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px" }}>
            <span className="au-sd-dot" style={{ background: "var(--au-line-2)" }} />
            <span className="au-sd-sig-name" style={{ flex: 1 }}>Full signal breakdown</span>
            <Tag variant="info">••••</Tag>
            <span className="au-sd-score" style={{ color: "var(--au-muted)" }}>••</span>
          </div>
        ))}
      </div>
      <div className="au-sd-lock-overlay">
        <div className="au-sd-lock-t">Detailed page reports are a Pro feature</div>
        <div className="au-sd-lock-s">See the full signal breakdown for every page — issues, fixes and evidence.</div>
        <Button variant="accent" onClick={onUnlock}><Lock size={14} /> Unlock detailed reports</Button>
      </div>
    </div>
  );
}

/* Route-level LOADING state (Aurora) — replaces the shared <TableSkeleton> for this route
   only, so the shared primitive stays untouched for other screens. */
export function ScanDetailLoading() {
  return (
    <div className="aurora-screen">
      <Shell>
        <div className="au-sd-toolbar"><Skeleton w={130} h={30} /></div>
        <div className="au-sd-stack">
          <Cell solid className="au-sd-headcell">
            <Skeleton w={96} h={96} style={{ borderRadius: "50%" }} />
            <div style={{ flex: 1, minWidth: 220, display: "grid", gap: 10 }}>
              <Skeleton w="45%" h={22} /><Skeleton w="70%" h={12} />
            </div>
          </Cell>
          <Cell solid>
            <div className="au-sd-skrows">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} h={46} />)}</div>
          </Cell>
        </div>
      </Shell>
    </div>
  );
}

/* Route-level ERROR / not-found state (Aurora) — copy matches the shared NotFoundState
   verbatim (title + label + back link); only the skin changes. */
export function ScanDetailNotFound({ label }) {
  return (
    <div className="aurora-screen">
      <Shell>
        <Cell solid>
          <div className="au-sd-center">
            <div className="au-sd-ill au-sd-ill-bad"><AlertTriangle size={26} /></div>
            <div className="au-sd-ct">Not found or no access</div>
            <div className="au-sd-cs">{label || "This item doesn’t exist, or it belongs to another workspace."}</div>
            <Link className="au-btn au-accent" to="/app/dashboard">Back to dashboard</Link>
          </div>
        </Cell>
      </Shell>
    </div>
  );
}
