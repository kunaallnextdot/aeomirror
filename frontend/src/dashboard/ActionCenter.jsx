/* Action Center (/app/action-center) — "What should I fix next?", ONE operational view
   over intelligence that already exists elsewhere (Report/Scan Details/Answer Tracking).
   This is a presentation layer only: it never scores, never invents an issue/priority/
   evidence, never calls an LLM, and never persists anything.

   Data sources (see actionSources.js for the normalization/dedup/priority logic):
   - GET /reports/{scan_id}          — recommendations, technical_seo, content_intelligence,
                                        phase4 (schema/entity/links), crawl_graph (all
                                        already embedded in ONE cached payload)
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
import { RecommendationCard, AU_PRIORITY } from "./ReportView.jsx";
import { buildActionItems, computeLockedActionCount } from "./actionSources.js";
import VerificationBadge from "./VerificationBadge.jsx";
import { Shell, Cell, Button, Skeleton } from "./aurora.jsx";
import "./ActionCenter.aurora.css";
import "./ReportView.aurora.css";   // reuses .au-rep-card-b/.au-rep-fx-* via RecommendationCard
import "./ScanDetails.aurora.css";  // reuses .au-sd-lockbanner/.au-sd-bh/.au-sd-ul verbatim

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
    scanId,
  }), [report, answerSim, scanId]);

  // Phase K: a finding whose backing signal has a real, persisted "verified" record
  // (the actual verification state machine's own status — never inferred from a score
  // change) no longer behaves like an active unresolved action. It's demoted out of the
  // primary priority-ordered list into a separate, collapsed "Verified fixed" section —
  // nothing is deleted, the finding/evidence stays fully visible on demand, it's just no
  // longer counted/sorted as if it still needed action. An item with no verifySignalId
  // (nothing real to verify against) is always active — verified status is never guessed.
  const activeItems = useMemo(
    () => items.filter((it) => !(it.verifySignalId && verificationBySignal[it.verifySignalId]?.verification_status === "verified")),
    [items, verificationBySignal],
  );
  const verifiedItems = useMemo(
    () => items.filter((it) => it.verifySignalId && verificationBySignal[it.verifySignalId]?.verification_status === "verified"),
    [items, verificationBySignal],
  );

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
  if (error) return <div className="aurora-screen"><Shell><Cell solid><div className="au-card-center" role="alert"><div className="au-card-s">{error}</div></div></Cell></Shell></div>;
  if (!scanId) return <NoReportYetAC />;

  return (
    <div className="aurora-screen">
      <Shell>
        <Cell solid>
          <div className="au-ac-head">
            <div className="au-ac-eyebrow"><ListChecks size={13} /> Action Center</div>
            {total > 0 ? (
              <div className="au-ac-headline">
                Your latest scan found {total} problem{total === 1 ? "" : "s"} to fix.
              </div>
            ) : (
              <div className="au-ac-headline">You&apos;re in good shape.</div>
            )}
          </div>
          {total === 0 && (
            <div className="au-ac-clean">
              <CheckCircle2 size={16} />
              <span>No high-priority issues were detected in your latest scan.</span>
            </div>
          )}
          {total > 0 && (
            <div className="au-ac-counts">
              {PRIORITY_ORDER.filter((p) => counts[p] > 0).map((p) => (
                <div key={p} className="au-ac-count">
                  <div className="au-ac-count-n" style={{ color: AU_PRIORITY[p] }}>{counts[p]}</div>
                  <div className="au-ac-count-l">{p}</div>
                </div>
              ))}
            </div>
          )}
          <Link className="au-btn au-ghost" style={{ marginTop: 10 }} to={`/app/scans/${encodeURIComponent(scanId)}`}>
            View report
          </Link>
        </Cell>

        {total > 0 && (
          <Cell solid style={{ marginTop: 14 }}>
            <div className="au-panel-h">Needs attention</div>
            {lockedCount > 0 && (
              <div className="au-sd-lockbanner" style={{ marginBottom: 10 }}>
                {lockedCount} more problem{lockedCount === 1 ? "" : "s"} found — full diagnosis is a Pro feature.
              </div>
            )}
            <div className="au-ac-list">
              {visibleItems.map((item) => (
                <ActionCard key={item.id} item={item} scanId={scanId}
                            expanded={expandedId === item.id}
                            onToggle={() => setExpandedId((id) => (id === item.id ? null : item.id))}
                            ins={aiById[item.id]}
                            verification={item.verifySignalId ? verificationBySignal[item.verifySignalId] : null} />
              ))}
            </div>
            {!showAll && activeItems.length > PREVIEW_LIMIT && (
              <button className="au-ac-viewall" onClick={() => setShowAll(true)}>
                View all {activeItems.length} actions
              </button>
            )}
          </Cell>
        )}

        {verifiedItems.length > 0 && (
          <Cell solid style={{ marginTop: 14 }}>
            <button type="button" className="au-ac-verified-toggle"
                    onClick={() => setShowVerified((s) => !s)} aria-expanded={showVerified}>
              <CheckCircle2 size={14} style={{ color: "var(--au-mint-d)" }} />
              <span>{verifiedItems.length} verified fixed</span>
              <span className="au-ac-verified-sub">— confirmed by a later scan, kept here for reference</span>
              <ChevronDown size={14} className="au-ac-chev" style={{ transform: showVerified ? "rotate(180deg)" : "none", marginLeft: "auto" }} />
            </button>
            {showVerified && (
              <div className="au-ac-list" style={{ marginTop: 10 }}>
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
    <div className={verified ? "au-ac-card au-ac-card-verified" : "au-ac-card"}>
      <button className="au-ac-card-h" onClick={onToggle} aria-expanded={expanded}>
        <span className="au-rep-pri" style={{ background: AU_PRIORITY[item.priority] }}>{item.priority}</span>
        <span className="au-ac-card-t">{item.title}</span>
        <VerificationBadge verification={verification} />
        <span className="au-ac-card-src">{item.source}</span>
        <ChevronDown size={15} className="au-ac-chev" style={{ transform: expanded ? "rotate(180deg)" : "none" }} />
      </button>
      {!expanded ? (
        <div className="au-ac-preview">
          {item.problem && <p className="au-ac-problem">{item.problem}</p>}
          {item.evidencePreview?.length > 0 && (
            <div className="au-ac-ev-preview">Evidence: {item.evidencePreview.join(" · ")}</div>
          )}
          {item.fixText && <div className="au-ac-fix-preview">Fix: {item.fixText}</div>}
          <div className="au-ac-actions">
            <Button variant="accent" onClick={onToggle}>See fix</Button>
            <Link className="au-ac-openlink" to={item.destination.to}>
              {item.destination.label} <ExternalLink size={11} />
            </Link>
          </div>
        </div>
      ) : item.kind === "recommendation" ? (
        <>
          <RecommendationCard r={item.raw} ins={ins} />
          <div className="au-ac-actions" style={{ padding: "0 16px 14px" }}>
            <Link className="au-ac-openlink" to={item.destination.to}>
              Open in Scan Details <ExternalLink size={11} />
            </Link>
          </div>
        </>
      ) : (
        <div className="au-ac-preview">
          {item.problem && <p className="au-ac-problem">{item.problem}</p>}
          {item.why && (
            <div className="au-rep-ai-why" style={{ marginTop: 8 }}>
              <div className="au-rep-ai-why-h"><Sparkles size={12} /> Why it matters</div>
              <p>{item.why}</p>
            </div>
          )}
          {(item.fullEvidence || item.evidencePreview || []).length > 0 && (
            <div className="au-ac-ev-full">
              <div className="au-sd-bh">Evidence</div>
              <ul className="au-sd-ul">
                {(item.fullEvidence || item.evidencePreview).map((e, i) => <li key={i}><span>{e}</span></li>)}
              </ul>
            </div>
          )}
          {item.fixText && (
            <div className="au-ac-ev-full">
              <div className="au-sd-bh">Fix</div>
              <p style={{ margin: 0 }}>{item.fixText}</p>
            </div>
          )}
          <div className="au-ac-actions">
            <Link className="au-ac-openlink" to={item.destination.to}>
              {item.destination.label} <ExternalLink size={11} />
            </Link>
            {/* Only offered when the source object carries a real scanner signal id
                (verifySignalId) — never guessed for a source with no such link back
                to a re-scannable signal (e.g. content intelligence, crawl graph). */}
            {item.verifySignalId && (
              <Link className="au-ac-openlink" to={`/app/scans/${encodeURIComponent(scanId)}`}>
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
    <div className="aurora-screen"><Shell>
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
    <div className="aurora-screen"><Shell>
      <Cell solid><div className="au-card-center">
        <div className="au-card-t">No report yet</div>
        <div className="au-card-s">Run your first scan to see what needs attention.</div>
      </div></Cell>
    </Shell></div>
  );
}
