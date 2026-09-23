/* AEO Answer Simulator — the primary Answer Tracking UX: deterministic evidence
   retrieval + answerability scoring from the site's own scanned content, with an
   optional AI explanation step (Pro). Zero external AI-provider calls by default —
   see backend/app/services/answer_simulator/. Provider Tracking (the live OpenAI/
   Anthropic/Perplexity/Gemini flow) is a separate, secondary panel — see
   AnswerTracking.jsx. */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Play, ChevronDown, ChevronRight, Sparkles, AlertTriangle, ExternalLink } from "lucide-react";
import {
  getSimulatorQuestions, runAnswerSimulation, explainSimulatorQuestion, ScanError,
} from "../api.js";
import { Cell, Button, Skeleton } from "./aurora.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import "./AnswerTracking.aurora.css";

// Human-readable status — answers "is this question supported?" directly, rather
// than the abstract High/Medium/Low the underlying `answerability` enum reads as.
// Same real backend value, friendlier words — nothing invented.
const ANSWERABILITY_LABEL = {
  HIGH: "Supported", MEDIUM: "Partially supported", LOW: "Not supported",
  INSUFFICIENT_EVIDENCE: "Not enough evidence",
};
const ANSWERABILITY_COLOR = {
  HIGH: "var(--au-mint-d)", MEDIUM: "var(--au-lemon-d)", LOW: "var(--au-peach-d)",
  INSUFFICIENT_EVIDENCE: "var(--au-muted)",
};
const BATCH_PRESETS = [10, 25, 50];

const FIELD_LABEL = {
  title: "Title", meta_description: "Meta description", h1: "Heading",
  heading_question: "Heading", faq: "FAQ", entity: "Entity", body_chunk: "Body content",
};
const fieldLabel = (f) => FIELD_LABEL[f] || (f ? f.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "Evidence");

export default function AnswerSimulator({ monitorId, canRun }) {
  const { handleGated } = useUpgrade();
  const [questions, setQuestions] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [customText, setCustomText] = useState("");
  const [customDrafts, setCustomDrafts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState(null);
  const [requestLlmStep, setRequestLlmStep] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setQuestions(await getSimulatorQuestions(monitorId));
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load questions.");
    }
  }, [monitorId]);
  useEffect(() => { load(); setSelected(new Set()); setRun(null); }, [load]);

  const bankQuestions = questions?.bank_questions || [];
  const trackedPrompts = questions?.tracked_prompts || [];
  const allChoices = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const t of trackedPrompts) {
      if (!seen.has(t.text)) { seen.add(t.text); out.push({ text: t.text, source: t.source, tracked: true }); }
    }
    for (const q of bankQuestions) {
      if (!seen.has(q.question)) { seen.add(q.question); out.push({ text: q.question, source: "scan", tracked: false }); }
    }
    for (const t of customDrafts) {
      if (!seen.has(t)) { seen.add(t); out.push({ text: t, source: "manual", tracked: false }); }
    }
    return out;
  }, [bankQuestions, trackedPrompts, customDrafts]);

  const toggle = (text) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(text)) next.delete(text); else next.add(text);
      return next;
    });
  };

  const selectPreset = (n) => {
    setSelected(new Set(allChoices.slice(0, n).map((c) => c.text)));
  };

  const addCustom = () => {
    const t = customText.trim();
    if (!t) return;
    if (!customDrafts.includes(t)) setCustomDrafts((d) => [...d, t]);
    setSelected((prev) => new Set(prev).add(t));
    setCustomText("");
  };

  const runSimulation = async () => {
    if (selected.size === 0) return;
    setBusy(true); setError(null);
    try {
      const questionIds = [];
      const questionBankKeys = [];
      const customQuestions = [];
      const trackedByText = new Map(trackedPrompts.map((p) => [p.text, p]));
      const bankTexts = new Set(bankQuestions.map((q) => q.question));
      for (const text of selected) {
        const tracked = trackedByText.get(text);
        if (tracked) { questionIds.push(tracked.id); continue; }
        if (bankTexts.has(text)) { questionBankKeys.push(text); continue; }
        customQuestions.push(text);
      }
      const res = await runAnswerSimulation(monitorId,
        { questionIds, customQuestions, questionBankKeys, requestLlmStep });
      setRun(res);
      await load();
    } catch (e) {
      if (handleGated(e, "answer_simulator")) { /* upgrade modal shown */ }
      else setError(e instanceof ScanError ? e.message : "Could not run the simulation.");
    } finally { setBusy(false); }
  };

  const explain = async (promptId) => {
    if (!run) return;
    setBusy(true); setError(null);
    try {
      const updated = await explainSimulatorQuestion(monitorId, run.run_id, promptId);
      setRun((prev) => ({
        ...prev,
        results: prev.results.map((r) => (r.prompt_id === promptId ? updated : r)),
      }));
    } catch (e) {
      if (handleGated(e, "answer_simulator")) { /* upgrade modal shown */ }
      else setError(e instanceof ScanError ? e.message : "Could not get an explanation.");
    } finally { setBusy(false); }
  };

  if (!questions && !error) {
    return (
      <Cell solid className="au-as-panel">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} h={36} />)}
        </div>
      </Cell>
    );
  }

  return (
    <Cell solid className="au-as-panel">
      <div className="au-panel-h">AEO Answer Simulator</div>
      <div className="au-as-explainer">
        Simulation uses your website&apos;s current content to estimate how well an AI
        answer can be supported. This is not a live measurement of ChatGPT, Claude,
        Gemini, or Perplexity responses.
      </div>

      {error && <div className="au-at-err">{error}</div>}

      {allChoices.length === 0 ? (
        <div className="au-dim" style={{ fontSize: 13, padding: "6px 2px" }}>
          No scan-derived questions yet — run a scan for this site, or add a custom
          question below.
        </div>
      ) : (
        <>
          <ul className="au-as-questions">
            {allChoices.map((c) => (
              <li key={c.text} className="au-as-question">
                <label>
                  <input type="checkbox" checked={selected.has(c.text)}
                        onChange={() => toggle(c.text)} disabled={!canRun} />
                  <span className="au-as-question-text">{c.text}</span>
                  <span className="au-as-question-badge">{c.tracked ? "tracked" : c.source}</span>
                </label>
              </li>
            ))}
          </ul>
          {canRun && (
            <div className="au-as-presets">
              {BATCH_PRESETS.map((n) => (
                <button key={n} type="button" className="au-at-suggest-chip"
                       onClick={() => selectPreset(n)} disabled={busy}>
                  Select {n}
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {canRun && (
        <>
          <div className="au-at-add">
            <input className="au-input" placeholder="Add a custom question…" value={customText}
                  maxLength={2000} onChange={(e) => setCustomText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addCustom()} />
            <Button variant="accent" disabled={busy || !customText.trim()} onClick={addCustom}>Add</Button>
          </div>

          <label className="au-as-llm-toggle">
            <input type="checkbox" checked={requestLlmStep}
                  onChange={(e) => setRequestLlmStep(e.target.checked)} />
            Use the optional AI explanation step (Pro)
          </label>

          <div className="au-at-runbar">
            <Button variant="primary" loading={busy} disabled={busy || selected.size === 0} onClick={runSimulation}>
              {!busy && <Play size={15} />} Run Simulation
            </Button>
            <div className="au-at-est">{selected.size} question{selected.size === 1 ? "" : "s"} selected</div>
          </div>
        </>
      )}

      {run && (
        <div className="au-as-results">
          {run.locked_count > 0 && (
            <div className="au-at-note"><AlertTriangle size={13} /> {run.locked_count} question{run.locked_count === 1 ? "" : "s"} skipped
              — Free plan batch limit is {run.batch_limit}. Upgrade to Pro for larger batches.</div>
          )}
          {run.results.map((r) => (
            <SimulationResultCard key={r.prompt_id} result={r} onExplain={() => explain(r.prompt_id)} busy={busy} />
          ))}
        </div>
      )}
    </Cell>
  );
}

/* Fix-first order: Question/Status (header) -> Support -> What to fix/Fix ->
   Evidence (collapsed sub-section) -> secondary metrics. Support is built from the
   site's OWN real evidence snippet(s) — never a synthesized/invented sentence — and
   falls back to the backend's own deterministic explanation for LOW/INSUFFICIENT_
   EVIDENCE, where that text is already a complete, honest sentence on its own. */
function supportText(result, level, summaryLine) {
  const first = result.evidence?.[0]?.snippet;
  if (level === "LOW" || level === "INSUFFICIENT_EVIDENCE" || !first) return summaryLine;
  const n = result.evidence.length;
  return `Your scanned content addresses this${n > 1 ? ` with ${n} supporting facts` : ""}: "${first}"`;
}

function SimulationResultCard({ result, onExplain, busy }) {
  const [expanded, setExpanded] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  const level = result.answerability;
  // The deterministic answer_text is "<summary line>\n- <evidence line>..." — the
  // bullet lines duplicate exactly what the evidence cards below already show, so
  // only the summary line is surfaced here (see Answer Tracking redesign: no raw
  // evidence-text dump, structured evidence instead).
  const summaryLine = (result.answer_text || "").split("\n")[0];
  const support = supportText(result, level, summaryLine);
  const hasGap = result.missing_information.length > 0;
  const evidenceCount = result.evidence.length;

  return (
    <div className="au-as-card">
      <button className="au-as-card-head" onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="au-as-card-question">{result.question}</span>
        <span className="au-at-tag" style={{ color: ANSWERABILITY_COLOR[level] }}>
          {ANSWERABILITY_LABEL[level] || level}
        </span>
      </button>
      {expanded && (
        <div className="au-as-card-body">
          {support && <div className="au-as-summary au-as-support">{support}</div>}

          {hasGap ? (
            <div className="au-as-missing">
              <div className="au-at-block-h">What to fix</div>
              <ul>{result.missing_information.map((m, i) => <li key={i}>{m}</li>)}</ul>
              {/* Generic, non-topic-specific guidance only — never a fabricated,
                  topic-specific "opportunity" (e.g. never invents a page recommendation
                  the site's own content doesn't support). */}
              <div className="au-as-fix">
                <span className="au-at-block-h" style={{ marginBottom: 4 }}>Fix</span>
                Add a section that directly addresses this question, with a clear
                heading and a concise answer near the top of the page.
              </div>
            </div>
          ) : level === "HIGH" && (
            <div className="au-as-nofix"><span>Fix</span> No fix needed — this question is well supported.</div>
          )}

          {evidenceCount > 0 && (
            <div className="au-as-evidence">
              <button type="button" className="au-ci-toggle" onClick={() => setShowEvidence((v) => !v)}>
                {showEvidence ? "Hide" : "View"} evidence ({evidenceCount} source{evidenceCount === 1 ? "" : "s"})
              </button>
              {showEvidence && (
                <div className="au-as-evidence-grid" style={{ marginTop: 8 }}>
                  {result.evidence.map((e, i) => (
                    <div key={i} className="au-as-ev-card">
                      <div className="au-as-ev-head">
                        <span className="au-as-ev-src">{fieldLabel(e.field)}</span>
                        <a className="au-as-ev-link" href={e.url} target="_blank" rel="noopener noreferrer">
                          View source <ExternalLink size={10} />
                        </a>
                      </div>
                      <div className="au-as-ev-url">{e.url}</div>
                      <div className="au-as-ev-snip">“{e.snippet}”</div>
                    </div>
                  ))}
                </div>
              )}
              {result.locked_evidence_count > 0 && (
                <div className="au-as-evidence-locked">
                  {result.locked_evidence_count} more source{result.locked_evidence_count === 1 ? "" : "s"} — Pro
                </div>
              )}
            </div>
          )}

          {level === "INSUFFICIENT_EVIDENCE" && !result.llm_step_used && (
            <Button variant="accent" disabled={busy} onClick={onExplain}>
              <Sparkles size={13} /> Explain why
            </Button>
          )}

          {/* Secondary detail — real, still available, never dominant. */}
          <div className="au-as-secondary">
            <div className="au-as-metrics">
              <div className="au-as-metric">
                Evidence relevance
                <b>{result.evidence_relevance_pct != null ? `${result.evidence_relevance_pct}%` : "—"}</b>
              </div>
              <div className="au-as-metric">
                Topic alignment
                <b>{result.topic_alignment_score != null ? `${result.topic_alignment_score}%` : "—"}</b>
              </div>
              <div className="au-as-metric">
                Evidence coverage
                <b>{result.evidence_coverage_pct != null ? `${result.evidence_coverage_pct}%` : "—"}</b>
              </div>
              <div className="au-as-metric">
                Supported by
                <b>{result.supported_url_count || 0} page{result.supported_url_count === 1 ? "" : "s"}</b>
              </div>
            </div>
            <div className="au-as-brand">
              Brand Mention: <b>{result.brand_mentioned === true ? "YES" : result.brand_mentioned === false ? "NO" : "—"}</b>
              {result.llm_step_used && <span className="au-at-tag"><Sparkles size={11} /> AI-explained</span>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
