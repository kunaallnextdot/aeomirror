/* Action Center (/app/action-center) — "What should I fix next?", ONE operational view
   over intelligence that already exists elsewhere (Report/Scan Details/Answer Tracking).
   This is a presentation layer only: it never scores, never invents an issue/priority/
   evidence, never calls an LLM, and never persists anything.

   Data sources (see actionSources.js for the normalization/dedup/priority logic):
   - GET /reports/{scan_id}          — recommendations, technical_seo, content_intelligence,
                                        phase4 (schema/entity/links), crawl_graph, and
                                        url_intelligence (the URL Intelligence Engine's own
                                        crawl findings, live-merged in by
                                        reports/service.py — see its own docstring for why
                                        that one key is never part of the cached payload)
                                        — all in ONE request
   - GET /reports/{scan_id}/answer-simulation — the Answer Simulator's most recently
                                        PERSISTED run summary (read-only, never triggers a
                                        new simulation, never touches individual question
                                        text — see actionSources.js's normalizeAnswerSimulation)
   - GET /api/verifications           — persisted Fix Verification records for this scan
                                        (only ever shown when a real record exists — see
                                        VerificationBadge; never a fake "not verified" status)
   Exactly 3 requests total, fired once on mount, never per-card. */
import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ListChecks, CheckCircle2, ExternalLink, Sparkles, History } from "lucide-react";
import { getReport, getReportAnswerSimulation, getVerifications, ScanError } from "../api.js";
import { RecommendationCard, SP_PRIORITY } from "./ReportView.jsx";
import { buildActionItems, computeLockedActionCount } from "./actionSources.js";
import VerificationBadge from "./VerificationBadge.jsx";
import { Shell, Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import "./ActionCenter.spectrum.css";

const PRIORITY_ORDER = ["Critical", "High", "Medium", "Low"];
const PREVIEW_LIMIT = 6;   // no existing product limit for this list; 5-7 is the ticket's
                            // own suggested range — 6 is the deterministic middle choice.
                            // Applied AFTER normalize -> dedup -> priority sort (see below),
                            // so a Critical/High item is never hidden behind lower-priority ones.

export default function ActionCenter({ scanId }) {
  const [report, setReport] = useState(null);
  const [answerSim, setAnswerSim] = useState(null);
  const [verifications, setVerifications] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [showVerified, setShowVerified] = useState(false);

  useEffect(() => {
    if (!scanId) { setLoading(false); return; }
    let alive = true;
    setLoading(true); setError(null);
    Promise.all([
      getReport(scanId),
      getReportAnswerSimulation(scanId).catch(() => null),
      getVerifications(scanId).catch(() => ({ verifications: [] })),
    ])
      .then(([r, sim, v]) => {
        if (alive) { setReport(r); setAnswerSim(sim); setVerifications(v.verifications || []); setLoading(false); }
      })
      .catch((e) => { if (alive) { setError(e instanceof ScanError ? e.message : "Could not load your action items."); setLoading(false); } });
    return () => { alive = false; };
  }, [scanId]);

  // Latest persisted verification per signal_id (server returns newest-first),
  // looked up per item by its own `verifySignalId` (set only where the source object
  // carries a real scanner signal id — see actionSources.js) rather than assuming
  // `item.id` happens to equal one.
  const verificationBySignal = useMemo(() => {
    const map = {};
    for (const v of verifications) if (!map[v.signal_id]) map[v.signal_id] = v;
    return map;
  }, [verifications]);

  const aiById = useMemo(() =>
    Object.fromEntries((report?.ai?.issue_insights || []).map((i) => [i.id, i])), [report]);

  const items = useMemo(() => buildActionItems({
    recommendations: report?.recommendations,
    technicalSeo: report?.technical_seo,
    contentIntelligence: report?.content_intelligence,
    answerSimulation: answerSim,
    phase4: report?.phase4,
    crawlGraph: report?.crawl_graph,
    urlIntelligence: report?.url_intelligence,
    scanId,
  }), [report, answerSim, scanId]);

  // Phase K: a finding whose backing signal has a real, persisted "verified" record
  // (the actual verification state machine's own status — never inferred from a score
  // change) no longer behaves like an active unresolved action. It's demoted out of the
  // primary priority-ordered list into a separate, collapsed "Verified fixed" section —
  // nothing is deleted, the finding/evidence stays fully visible on demand, it's just no
  // longer counted/sorted as if it still needed action. An item with no verifySignalId
  // (nothing real to verify against) is always active — verified status is never guessed.
  // A source can also carry ITS OWN self-contained verification (today: URL
  // Intelligence's `item.verified`, set from UrlFinding.verification_status — a
  // different, per-URL evidence diff than the scan-signal Verification table
  // verifySignalId keys into) — either path demotes an item the same way.
  const isVerified = (it) =>
    it.verified || (it.verifySignalId && verificationBySignal[it.verifySignalId]?.verification_status === "verified");
  const activeItems = useMemo(() => items.filter((it) => !isVerified(it)), [items, verificationBySignal]);
  const verifiedItems = useMemo(() => items.filter(isVerified), [items, verificationBySignal]);

  const counts = useMemo(() => {
    const c = { Critical: 0, High: 0, Medium: 0, Low: 0 };
    for (const it of activeItems) if (c[it.priority] != null) c[it.priority] += 1;
    return c;
  }, [activeItems]);
  const total = activeItems.length;   // unique ACTIVE action items — verified-fixed ones don't count as still-open
  const visibleItems = showAll ? activeItems : activeItems.slice(0, PREVIEW_LIMIT);
  // Sums locked counts across EVERY source Action Center itself renders (recommendations,
  // technical_seo, content_intelligence, phase4 schema/entity/links, crawl_graph) — not
  // just locked recommendations — so this banner never understates what's actually hidden.
  const lockedCount = computeLockedActionCount(report);

  if (loading) return <ActionCenterSkeleton />;
  if (error) return <div className="spectrum-screen"><Shell><Cell solid><div className="sp-card-center" role="alert"><div className="sp-card-s">{error}</div></div></Cell></Shell></div>;
  if (!scanId) return <NoReportYetAC />;

  return (
    <div className="spectrum-screen">
      <Shell>
        <Cell solid>
          <div className="sp-ac-head">
            <div className="sp-ac-eyebrow"><ListChecks size={13} /> Action Center</div>
            {total > 0 ? (
              <div className="sp-ac-headline">
                Your latest scan found {total} problem{total === 1 ? "" : "s"} to fix.
              </div>
            ) : (
              <div className="sp-ac-headline">You&apos;re in good shape.</div>
            )}
          </div>
          {total === 0 && (
            <div className="sp-ac-clean">
              <CheckCircle2 size={16} />
              <span>No high-priority issues were detected in your latest scan.</span>
            </div>
          )}
          {total > 0 && (
            <div className="sp-ac-counts">
              {PRIORITY_ORDER.filter((p) => counts[p] > 0).map((p) => (
                <div key={p} className="sp-ac-count">
                  <div className="sp-ac-count-n" style={{ color: SP_PRIORITY[p] }}>{counts[p]}</div>
                  <div className="sp-ac-count-l">{p}</div>
                </div>
              ))}
            </div>
          )}
          <Link className="sp-btn sp-ghost" style={{ marginTop: 10 }} to={`/app/scans/${encodeURIComponent(scanId)}`}>
            View report
          </Link>
        </Cell>

        {total > 0 && (
          <Cell solid style={{ marginTop: 14 }}>
            <div className="sp-panel-h">Needs attention</div>
            {lockedCount > 0 && (
              <div className="sp-sd-lockbanner" style={{ marginBottom: 10 }}>
                {lockedCount} more problem{lockedCount === 1 ? "" : "s"} found — full diagnosis is a Pro feature.
              </div>
            )}
            <div className="sp-ac-list">
              {visibleItems.map((item) => (
                <ActionCard key={item.id} item={item} scanId={scanId}
                            expanded={expandedId === item.id}
                            onToggle={() => setExpandedId((id) => (id === item.id ? null : item.id))}
                            ins={aiById[item.id]}
                            verification={item.verifySignalId ? verificationBySignal[item.verifySignalId] : null} />
              ))}
            </div>
            {!showAll && activeItems.length > PREVIEW_LIMIT && (
              <button className="sp-ac-viewall" onClick={() => setShowAll(true)}>
                View all {activeItems.length} actions
              </button>
            )}
          </Cell>
        )}

        {verifiedItems.length > 0 && (
          <Cell solid style={{ marginTop: 14 }}>
            <button type="button" className="sp-ac-verified-toggle"
                    onClick={() => setShowVerified((s) => !s)} aria-expanded={showVerified}>
              <CheckCircle2 size={14} style={{ color: "var(--sp-blue-d)" }} />
              <span>{verifiedItems.length} verified fixed</span>
              <span className="sp-ac-verified-sub">— confirmed by a later scan, kept here for reference</span>
              <ChevronDown size={14} className="sp-ac-chev" style={{ transform: showVerified ? "rotate(180deg)" : "none", marginLeft: "auto" }} />
            </button>
            {showVerified && (
              <div className="sp-ac-list" style={{ marginTop: 10 }}>
                {verifiedItems.map((item) => (
                  <ActionCard key={item.id} item={item} scanId={scanId}
                              expanded={expandedId === item.id}
                              onToggle={() => setExpandedId((id) => (id === item.id ? null : item.id))}
                              ins={aiById[item.id]}
                              verification={verificationBySignal[item.verifySignalId]}
                              verified />
                ))}
              </div>
            )}
          </Cell>
        )}
      </Shell>
    </div>
  );
}

function ActionCard({ item, scanId, expanded, onToggle, ins, verification, verified }) {
  return (
    <div className={verified ? "sp-ac-card sp-ac-card-verified" : "sp-ac-card"}>
      <button className="sp-ac-card-h" onClick={onToggle} aria-expanded={expanded}>
        <span className="sp-rep-pri" style={{ background: SP_PRIORITY[item.priority] }}>{item.priority}</span>
        <span className="sp-ac-card-t">{item.title}</span>
        <VerificationBadge verification={verification} />
        <span className="sp-ac-card-src">{item.source}</span>
        <ChevronDown size={15} className="sp-ac-chev" style={{ transform: expanded ? "rotate(180deg)" : "none" }} />
      </button>
      {!expanded ? (
        <div className="sp-ac-preview">
          {item.problem && <p className="sp-ac-problem">{item.problem}</p>}
          {item.evidencePreview?.length > 0 && (
            <div className="sp-ac-ev-preview">Evidence: {item.evidencePreview.join(" · ")}</div>
          )}
          {item.fixText && <div className="sp-ac-fix-preview">Fix: {item.fixText}</div>}
          {item.overlapsWith?.length > 0 && (
            <div className="sp-ac-overlap" title="Different sources flagged the same page — review together, they may share a root cause.">
              Also flagged on this page by: {item.overlapsWith.map((o) => o.source).join(", ")}
            </div>
          )}
          <div className="sp-ac-actions">
            <Button variant="gradient" onClick={onToggle}>See fix</Button>
            <Link className="sp-ac-openlink" to={item.destination.to}>
              {item.destination.label} <ExternalLink size={11} />
            </Link>
          </div>
        </div>
      ) : item.kind === "recommendation" ? (
        <>
          <RecommendationCard r={item.raw} ins={ins} />
          <div className="sp-ac-actions" style={{ padding: "0 16px 14px" }}>
            <Link className="sp-ac-openlink" to={item.destination.to}>
              Open in Scan Details <ExternalLink size={11} />
            </Link>
          </div>
        </>
      ) : (
        <div className="sp-ac-preview">
          {item.problem && <p className="sp-ac-problem">{item.problem}</p>}
          {item.why && (
            <div className="sp-rep-ai-why" style={{ marginTop: 8 }}>
              <div className="sp-rep-ai-why-h"><Sparkles size={12} /> Why it matters</div>
              <p>{item.why}</p>
            </div>
          )}
          {(item.fullEvidence || item.evidencePreview || []).length > 0 && (
            <div className="sp-ac-ev-full">
              <div className="sp-sd-bh">Evidence</div>
              <ul className="sp-sd-ul">
                {(item.fullEvidence || item.evidencePreview).map((e, i) => <li key={i}><span>{e}</span></li>)}
              </ul>
            </div>
          )}
          {item.fixText && (
            <div className="sp-ac-ev-full">
              <div className="sp-sd-bh">Fix</div>
              <p style={{ margin: 0 }}>{item.fixText}</p>
            </div>
          )}
          {item.overlapsWith?.length > 0 && (
            <div className="sp-ac-overlap">
              Also flagged on this page by: {item.overlapsWith.map((o) => o.source).join(", ")}
            </div>
          )}
          <div className="sp-ac-actions">
            <Link className="sp-ac-openlink" to={item.destination.to}>
              {item.destination.label} <ExternalLink size={11} />
            </Link>
            {/* Only offered when the source object carries a real scanner signal id
                (verifySignalId) — never guessed for a source with no such link back
                to a re-scannable signal (e.g. content intelligence, crawl graph). */}
            {item.verifySignalId && (
              <Link className="sp-ac-openlink" to={`/app/scans/${encodeURIComponent(scanId)}`}>
                <History size={11} /> Verify after re-scan
              </Link>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ActionCenterSkeleton() {
  return (
    <div className="spectrum-screen"><Shell>
      <Cell solid>
        <Skeleton w="55%" h={22} /><Skeleton w="80%" h={14} style={{ marginTop: 10 }} />
      </Cell>
      <Cell solid style={{ marginTop: 14 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} h={54} />)}
        </div>
      </Cell>
    </Shell></div>
  );
}

function NoReportYetAC() {
  return (
    <div className="spectrum-screen"><Shell>
      <Cell solid><div className="sp-card-center">
        <div className="sp-card-t">No report yet</div>
        <div className="sp-card-s">Run your first scan to see what needs attention.</div>
      </div></Cell>
    </Shell></div>
  );
}
