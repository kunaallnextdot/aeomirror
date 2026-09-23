/* DiagnosisCard — the shared Problem -> Evidence -> Why it matters -> How to fix ->
   Implementation -> Verify presentation shell, reused by ReportView's Technical SEO,
   Schema, Entity, Content Intelligence, and Crawl Graph sections (Phase C) so a single
   real finding reads the same way everywhere in the product, mirroring the language
   already established by RecommendationCard (ReportView.jsx) and Action Center.

   PRESENTATION ONLY: this component never fetches data, never scores, never classifies
   a finding, never calls an LLM, and never invents a step. Every prop is optional and
   independently rendered — a caller that has no real "why" text for a given finding
   simply doesn't pass `whyItMatters`, and that step is omitted rather than repeating
   another step's text under a new heading (see each ReportView section's own comments
   for why a given step is/isn't populated for that section's real backend data).

   Reuses the EXACT CSS classes RecommendationCard/the report's existing cards already
   use (`au-rep-card`, `au-rep-fx-h`, `au-rep-fx-ul`, `au-rep-ai-why`, `au-tseo-badge`,
   `au-rep-verify-link`, `au-ci-toggle`) — one visual language, not a new one. */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, History, Sparkles } from "lucide-react";

const SEVERITY_COLOR = {
  Critical: "var(--au-peach-d)", High: "var(--au-peach-d)",
  Medium: "var(--au-lemon-d)", Low: "var(--au-muted)",
};

const EVIDENCE_PREVIEW_LIMIT = 3;

/**
 * @param {string} title - short "what's wrong" headline (bold).
 * @param {string} [badge] - short badge text (defaults to `severity` when omitted).
 * @param {string} [severity] - Critical|High|Medium|Low, colors the badge.
 * @param {string} [problem] - 1-2 sentence problem statement. Omitted -> no paragraph.
 * @param {string[]} [evidence] - real evidence lines (URLs, counts, statuses). Shown
 *   as a short preview with a "Show N more" expand when there are more than 3.
 * @param {string} [whyItMatters] - real, section-provided explanation text.
 * @param {string} [fix] - real "how to fix" text.
 * @param {string} [implementation] - real code/markup example, rendered as <pre>.
 * @param {string} [verifyHref] - when set, renders "Verify after re-scan" linking
 *   there. Callers must only pass this when a real related recommendation/signal
 *   exists (see each section's own dedup logic) — never guessed at here.
 * @param {React.ReactNode} [affectedPages] - the real, full list of affected pages/
 *   URLs a caller needs ALWAYS visible (never truncated behind the Evidence preview's
 *   "Show N more" — e.g. Content Intelligence's cluster page list). Rendered right
 *   after the Evidence step.
 * @param {React.ReactNode} [children] - extra section-specific content appended after
 *   the standard steps (e.g. Content Intelligence's per-page detail expand).
 */
export default function DiagnosisCard({
  title, badge, severity, problem, evidence, whyItMatters, fix, implementation,
  verifyHref, affectedPages, children,
}) {
  const [showAllEvidence, setShowAllEvidence] = useState(false);
  const evList = evidence || [];
  const preview = evList.slice(0, EVIDENCE_PREVIEW_LIMIT);
  const rest = evList.slice(EVIDENCE_PREVIEW_LIMIT);

  return (
    <div className="au-rep-card">
      <div className="au-rep-card-b" style={{ padding: "12px 14px" }}>
        {(title || badge || severity) && (
          <div className="au-tseo-row" style={{ marginBottom: problem ? 4 : 0 }}>
            {(badge || severity) && (
              <span className="au-tseo-badge" style={{ background: SEVERITY_COLOR[severity] || "var(--au-muted)" }}>
                {badge || severity}
              </span>
            )}
            {title && <span className="au-rep-card-t">{title}</span>}
          </div>
        )}

        {problem && <p className="au-rep-desc">{problem}</p>}

        {/* Fix-first: the actionable steps (what to fix / how) come immediately after
            the problem statement — visually dominant, ahead of supporting explanation
            and evidence, which stay real and available but secondary (see PART 7/8 of
            the ticket this reordering implements: WHAT'S WRONG -> WHAT TO FIX -> HOW TO
            FIX IT -> WHY IT MATTERS -> EVIDENCE). No step's content changed, only order. */}
        {fix && (
          <>
            <div className="au-rep-fx-h au-rep-fx-h-prominent">How to fix</div>
            <p className="au-rep-desc au-rep-fix-prominent">{fix}</p>
          </>
        )}

        {implementation && (
          <>
            <div className="au-rep-fx-h">Implementation</div>
            <pre className="au-code">{implementation}</pre>
          </>
        )}

        {whyItMatters && (
          <div className="au-rep-ai-why">
            <div className="au-rep-ai-why-h"><Sparkles size={12} /> Why it matters</div>
            <p>{whyItMatters}</p>
          </div>
        )}

        {preview.length > 0 && (
          <>
            <div className="au-rep-fx-h">Evidence</div>
            <ul className="au-rep-fx-ul">
              {preview.map((e, i) => <li key={i}><AlertTriangle size={11} /> <span>{e}</span></li>)}
            </ul>
            {rest.length > 0 && !showAllEvidence && (
              <button type="button" className="au-ci-toggle" onClick={() => setShowAllEvidence(true)}>
                Show {rest.length} more
              </button>
            )}
            {showAllEvidence && rest.length > 0 && (
              <ul className="au-rep-fx-ul" style={{ marginTop: 6 }}>
                {rest.map((e, i) => <li key={i}><AlertTriangle size={11} /> <span>{e}</span></li>)}
              </ul>
            )}
          </>
        )}

        {affectedPages}

        {children}
      </div>

      {verifyHref && (
        <div className="au-rep-verify-link">
          <Link to={verifyHref}><History size={12} /> Verify after re-scan</Link>
        </div>
      )}
    </div>
  );
}

/* A compact, non-actionable note for findings that are ALREADY represented by a
   fuller recommendation elsewhere on this same report (see each section's own
   `related_recommendation_id` check) — real evidence stays visible, but it's framed
   as supporting detail for that recommendation rather than a second, duplicate
   diagnosis card for the same root cause. Verification lives on the recommendation
   card itself (already wired — see RecCard's own "Verify after re-scan" link); this
   note only jumps there, it never adds a second Verify affordance. */
export function SupportingEvidenceNote({ recommendationTitle, items, onJump }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="au-rep-card">
      <div className="au-rep-card-b" style={{ padding: "12px 14px" }}>
        <p className="au-rep-desc" style={{ marginBottom: 6 }}>
          Already covered by the <button type="button" className="au-ci-toggle" style={{ display: "inline" }} onClick={onJump}>
            &quot;{recommendationTitle}&quot; recommendation
          </button> above — shown here as supporting evidence.
        </p>
        <ul className="au-rep-fx-ul">
          {items.map((e, i) => <li key={i}><AlertTriangle size={11} /> <span>{e}</span></li>)}
        </ul>
      </div>
    </div>
  );
}
