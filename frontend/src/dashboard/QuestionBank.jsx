/* Question Bank — a consolidated, grounded view of every REAL question AEOMirror
   already knows about for this site: scan-derived questions (FAQ schema + question-
   shaped headings, from Phase 4 Question Mining) merged with Answer Tracking's
   actually-tracked prompts, cross-linked to existing opportunities. Server-gated (see
   backend `gate_question_bank`) — a locked caller's `questions` array already IS the
   free preview; nothing here is blurred real content.

   This is an AGGREGATION/NAVIGATION layer, not a second Answer Tracking dashboard —
   it shows only what a question IS (found where, tracked or not, gap or not, linked to
   which opportunity), never the per-provider/per-sample verdict detail that
   AnswerTracking.jsx already owns. */
import React, { useEffect, useState } from "react";
import { Eye, EyeOff, HelpCircle, Link2, Lock } from "lucide-react";
import { getQuestionBank, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { Cell, Button, Tag } from "./aurora.jsx";

const SOURCE_LABEL = {
  scan_faq: "FAQ schema", scan_heading: "Page heading", answer_tracking: "Tracked prompt",
};
const CATEGORY_LABEL = {
  pricing: "Pricing", comparison: "Comparison", local: "Local", how_to: "How-to",
  problem_solution: "Problem/solution", service: "Service", informational: "Informational",
};

export function ReportQuestionBank({ scanId }) {
  const { openUpgrade } = useUpgrade();
  const [state, setState] = useState(null);   // null = loading

  useEffect(() => {
    if (!scanId) { setState(null); return undefined; }
    let alive = true;
    setState(null);
    getQuestionBank(scanId)
      .then((d) => { if (alive) setState(d); })
      .catch((e) => { if (alive && !(e instanceof ScanError)) setState({ available: false, reason: "error" }); });
    return () => { alive = false; };
  }, [scanId]);

  if (!state) return null;                                  // loading — avoid a flash

  if (state.available === false && state.reason === "scan_incomplete") {
    return (
      <Cell solid id="rep-question-bank" style={{ scrollMarginTop: 120 }}>
        <div className="au-panel-h">Question Bank</div>
        <div className="au-dim" style={{ fontSize: 13 }}>
          Scan still in progress — insufficient evidence to build the Question Bank yet.
        </div>
      </Cell>
    );
  }
  if (!state.available) return null;                        // error/unknown — fail quiet, never fabricate
  const questions = state.questions || [];
  if (questions.length === 0) return null;                   // clean empty state: nothing found, say nothing

  const locked = state.locked_question_count || 0;

  return (
    <Cell solid id="rep-question-bank" style={{ scrollMarginTop: 120 }}>
      <div className="au-panel-h">
        <HelpCircle size={14} style={{ color: "var(--au-primary)" }} /> Question Bank
        <span className="au-sub">real questions found on your site and in AI answers</span>
      </div>
      <div className="au-rep-list">
        {questions.map((q, i) => <QuestionRow key={i} q={q} />)}
      </div>
      {state.preview && locked > 0 && (
        <div className="au-sim-lock">
          <Lock size={13} />
          <span>{locked} more question{locked === 1 ? "" : "s"}</span>
          <Button variant="accent"
                  onClick={() => openUpgrade("report", { scanId, message: "Unlock the complete Question Bank." })}>
            Unlock
          </Button>
        </div>
      )}
    </Cell>
  );
}

function QuestionRow({ q }) {
  const at = q.answer_tracking || {};
  const relatedCount = (q.related_opportunity_ids || []).length;
  return (
    <div className="au-rep-card">
      <div className="au-rep-card-b" style={{ padding: "12px 14px" }}>
        <div className="au-rep-card-t" style={{ marginBottom: 6 }}>{q.question}</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
          {(q.sources || []).map((s) => <Tag key={s} variant="info">{SOURCE_LABEL[s] || s}</Tag>)}
          {q.category && <Tag variant="info">{CATEGORY_LABEL[q.category] || q.category}</Tag>}
        </div>
        {at.tracked ? (
          <div className="au-dim" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 5 }}>
            {at.is_gap ? (
              <span style={{ color: "var(--au-peach-d)", display: "flex", alignItems: "center", gap: 5 }}>
                <EyeOff size={12} /> Not mentioned in tracked AI answers
              </span>
            ) : (
              <span style={{ color: "var(--au-mint-d)", display: "flex", alignItems: "center", gap: 5 }}>
                <Eye size={12} /> Mentioned in {at.mention_rate}% of tracked AI answers
              </span>
            )}
          </div>
        ) : (
          <div className="au-dim" style={{ fontSize: 12 }}>Not yet tracked in Answer Tracking</div>
        )}
        {relatedCount > 0 && (
          <div className="au-dim" style={{ fontSize: 11, marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
            <Link2 size={11} /> {relatedCount} related opportunit{relatedCount === 1 ? "y" : "ies"}
          </div>
        )}
        {(q.source_urls || []).length > 0 && (
          <div className="au-dim" style={{ fontSize: 11, marginTop: 4 }}>{q.source_urls.join(" · ")}</div>
        )}
      </div>
    </div>
  );
}
