/* Page Intelligence — the per-URL view inside Page Opportunities' detail drawer
   (GET /monitors/:id/page-intelligence/:crawlUrlId). The most important problem and
   its next action come first; everything else is behind a disclosure. Every check,
   pattern and recommendation shows the stored evidence it came from. AI suggestions
   are made only on request, labelled as AI-generated, and shown next to the page's
   current text — nothing is ever changed on the website. */
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Sparkles } from "lucide-react";
import { analyzePageNow, generatePageSuggestions, getPageIntelligence, ScanError } from "../api.js";
import { fmtDate } from "./ui.jsx";
import { Button, Skeleton } from "../shared/spectrum.jsx";

const PRIORITY = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
const STATUS = { pass: "OK", fail: "Problem", warn: "Review", not_assessable: "Can't tell", not_applicable: "Not needed", info: "Observed" };
const EFFORT = { low: "Low effort", medium: "Medium effort", high: "High effort" };
const BENEFIT = {
  search_visibility: "Search visibility", ai_answer_readiness: "AI answer readiness", conversion: "Conversions",
  technical_health: "Technical health", content_quality: "Content quality",
};
const FIELD = { title: "SEO title", meta_description: "Meta description", h1: "H1 heading", answer_passage: "Answer passage" };
const msgOf = (e, fallback) => (e instanceof ScanError ? e.message : fallback);

export default function PageIntelligence({ monitorId, crawlUrlId, canRun, fallbackAction, children }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);
  const [actionErr, setActionErr] = useState(null);

  useEffect(() => {
    let alive = true;
    getPageIntelligence(monitorId, crawlUrlId)
      .then((r) => { if (alive) setD(r); })
      .catch((e) => { if (alive) setErr(msgOf(e, "Could not load the page analysis.")); });
    return () => { alive = false; };
  }, [monitorId, crawlUrlId]);

  const analyze = async () => {
    setBusy("analyze"); setActionErr(null);
    try { setD(await analyzePageNow(monitorId, crawlUrlId)); }
    catch (e) { setActionErr(msgOf(e, "Could not analyse this page.")); }
    finally { setBusy(null); }
  };
  const suggest = async () => {
    setBusy("suggest"); setActionErr(null);
    try { const s = await generatePageSuggestions(monitorId, crawlUrlId); setD((x) => ({ ...x, suggestions: s })); }
    catch (e) { setActionErr(msgOf(e, "Could not generate suggestions.")); }
    finally { setBusy(null); }
  };

  const a = d?.analysis;
  const top = a?.summary?.top_issue;
  return (
    <div className="sp-pi">
      {err && <p className="sp-si-job-error">{err}</p>}
      {!d && !err && <Skeleton h={90} />}
      {d && (
        <div className="sp-po-action">
          <div className="sp-po-action-k">{top ? "Most important problem" : "Recommended next action"}</div>
          {top ? (
            <>
              <strong>{top.issue}</strong> <span className={`sp-si-pri pri-${top.priority}`}>{PRIORITY[top.priority]}</span>
              <p>{top.recommended_change}</p>
              <p className="sp-si-muted">Evidence: {top.evidence[0]}</p>
              {top.action_center_finding && (
                <Link className="sp-si-link" to="/app/action-center">Tracked in Action Center <ArrowRight size={12} /></Link>
              )}
            </>
          ) : a ? (
            <><strong>No problem found</strong><p className="sp-si-muted">The checks that could be run on this page passed.</p></>
          ) : fallbackAction ? (
            <><strong>{fallbackAction.title}</strong>{fallbackAction.action && <p>{fallbackAction.action}</p>}
              <p className="sp-si-muted">Evidence: {fallbackAction.evidence}</p>
              {fallbackAction.kind === "finding" && (
                <Link className="sp-si-link" to="/app/action-center">Open in Action Center <ArrowRight size={12} /></Link>
              )}</>
          ) : null}
        </div>
      )}
      {d && !a && (
        <p className="sp-si-muted sp-pi-status">
          {d.status === "queued" ? "This page is queued for analysis." : "This page hasn't been analysed yet."}{" "}
          {canRun && <Button variant="ghost" onClick={analyze} loading={busy === "analyze"}>Analyse this page now</Button>}
        </p>
      )}
      {d && a && d.status === "from_earlier_crawl" && (
        <p className="sp-si-muted">This analysis is from an earlier crawl of the page.</p>
      )}
      {actionErr && <p className="sp-si-job-error" role="alert">{actionErr}</p>}

      {children?.(!!a)}

      {a && (
        <>
          {a.patterns.length > 0 && (
            <div className="sp-pi-patterns">
              <h3>What the data shows</h3>
              <ul className="sp-po-why">{a.patterns.map((p) => (
                <li key={p.code}>{p.text}{p.sample && <span className="sp-si-muted"> (sample: {p.sample})</span>}</li>
              ))}</ul>
              <p className="sp-si-muted">{a.disclaimer}</p>
            </div>
          )}
          <ChecksSection title="Content and technical" checks={a.checks.filter((c) => c.section === "content" || c.section === "technical")} />
          <ChecksSection title="Entity and structured data" checks={a.checks.filter((c) => c.section === "schema")} />
          <ChecksSection title={`AI answer readiness · ${a.summary.readiness.met} of ${a.summary.readiness.assessed} checks met`}
                         checks={a.checks.filter((c) => c.section === "readiness")} note={a.readiness_note} />
          <Comparison c={a.comparison} />
          <details className="sp-pi-sec" open>
            <summary>Recommended actions ({a.recommendations.length})</summary>
            {!a.recommendations.length ? <p className="sp-si-muted">Nothing to change based on the evidence available.</p>
              : a.recommendations.map((r) => <RecCard key={r.code} r={r} />)}
          </details>
        </>
      )}

      {d && (
        <details className="sp-pi-sec">
          <summary><Sparkles size={12} /> AI content suggestions</summary>
          {d.suggestions ? <Suggestions s={d.suggestions} />
            : !d.ai_suggestions_available ? <p className="sp-si-muted">AI suggestions aren't configured on this server.</p>
            : canRun ? (
              <>
                <p className="sp-si-muted">Generates a suggested title, description, headings, FAQ ideas and internal links
                  from this page's crawled content. Nothing is changed on your website.</p>
                <Button variant="ghost" onClick={suggest} loading={busy === "suggest"}><Sparkles size={13} /> Generate suggestions</Button>
              </>
            ) : <p className="sp-si-muted">Ask an admin or member to generate suggestions.</p>}
        </details>
      )}

      {d && (d.history.length > 0 || d.verification.length > 0) && (
        <details className="sp-pi-sec">
          <summary>History and verification</summary>
          {d.verification.length > 0 && (
            <ul className="sp-po-why">{d.verification.map((v) => (
              <li key={v.finding_id}>{v.title} — {v.verification_status ? v.verification_status.replace(/_/g, " ") : "not re-checked yet"}</li>
            ))}</ul>
          )}
          <ul className="sp-po-why">{d.history.map((h) => (
            <li key={h.id}>Analysed {fmtDate(h.analyzed_at)} · {h.recommendations} recommendation(s)
              {h.resolved_since_previous.length > 0 && <> · resolved since the previous analysis: {h.resolved_since_previous.join(", ").replace(/_/g, " ")}</>}
            </li>
          ))}</ul>
        </details>
      )}
    </div>
  );
}

function ChecksSection({ title, checks, note }) {
  if (!checks.length) return null;
  const problems = checks.filter((c) => c.status === "fail" || c.status === "warn").length;
  return (
    <details className="sp-pi-sec">
      <summary>{title}{problems ? ` · ${problems} to review` : ""}</summary>
      <ul className="sp-pi-checks">
        {checks.map((c, i) => (
          <li key={`${c.code}-${i}`} className={`st-${c.status}`}>
            <span className="sp-pi-st">{STATUS[c.status] || c.status}</span> {c.title}
            {c.observed && <span className="sp-si-muted"> — {c.observed}</span>}
            {c.detail && <div className="sp-si-muted">{c.detail}</div>}
          </li>
        ))}
      </ul>
      {note && <p className="sp-si-muted">{note}</p>}
    </details>
  );
}

function Comparison({ c }) {
  return (
    <details className="sp-pi-sec">
      <summary>Compared with similar pages</summary>
      {!c.available ? <p className="sp-si-muted">Not enough data to compare: {c.reason}</p> : (
        <>
          <p className="sp-si-muted">Based on {c.basis_label} across {c.qualifying_pages} pages of this type with {c.minimum}.
            Stronger pages: {c.successful.n}; weaker pages: {c.weak.n}.
            This page is {c.position === "not_qualifying" ? "not compared (too little data of its own)" : `in the ${c.position} group`}.</p>
          {c.differences.length ? <ul className="sp-po-why">{c.differences.map((x) => <li key={x.feature}>{x.text}</li>)}</ul>
            : <p className="sp-si-muted">No measurable difference where this page falls behind the stronger pages.</p>}
          <p className="sp-si-muted">{c.disclaimer}</p>
        </>
      )}
    </details>
  );
}

function RecCard({ r }) {
  return (
    <details className="sp-pi-rec">
      <summary>
        <span className={`sp-si-pri pri-${r.priority}`}>{PRIORITY[r.priority]}</span> <strong>{r.issue}</strong>
        <span className="sp-si-muted"> · {r.confidence} confidence · {EFFORT[r.effort]} · {BENEFIT[r.benefit_category]}</span>
      </summary>
      <dl className="sp-si-kv">
        <dt>Evidence</dt><dd>{r.evidence.map((e) => <div key={e}>{e}</div>)}</dd>
        <dt>Why it matters</dt><dd>{r.why_it_matters}</dd>
        <dt>Change</dt><dd>{r.recommended_change}</dd>
        {r.example && <><dt>Example</dt><dd>
          {r.example.current !== undefined && <div><span className="sp-si-muted">{r.example.label}:</span> {r.example.current}</div>}
          {r.example.pattern && <div><span className="sp-si-muted">Pattern:</span> {r.example.pattern}</div>}
          {r.example.code && <><div className="sp-si-muted">{r.example.label}</div><pre className="sp-pi-code">{r.example.code}</pre></>}
        </dd></>}
        {r.dependencies.length > 0 && <><dt>Needs</dt><dd>{r.dependencies.join("; ")}</dd></>}
        <dt>How to verify</dt><dd>{r.verification}</dd>
        {r.action_center_finding && <><dt>Action Center</dt><dd>Tracked as “{r.action_center_finding.title}”.{" "}
          <Link className="sp-si-link" to="/app/action-center">Open</Link></dd></>}
      </dl>
    </details>
  );
}

function Suggestions({ s }) {
  return (
    <div className="sp-pi-sugg">
      <p className="sp-pi-ai"><Sparkles size={12} /> {s.notice}</p>
      {s.pairs.length > 0 && (
        <div className="sp-si-table-wrap">
          <table className="sp-si-table sp-pi-pairs">
            <thead><tr><th>Element</th><th>Current</th><th>AI suggestion</th></tr></thead>
            <tbody>{s.pairs.map((p) => (
              <tr key={p.field}><td>{FIELD[p.field] || p.field}</td><td>{p.original || <span className="sp-si-muted">(none)</span>}</td>
                <td>{p.suggested}{p.rationale && <div className="sp-si-muted">{p.rationale}</div>}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {s.heading_outline.length > 0 && (
        <div className="sp-pi-cols">
          <div><h4>Current section headings</h4><ul className="sp-po-why">{(s.current_headings.length ? s.current_headings : ["(none recorded)"]).map((h) => <li key={h}>{h}</li>)}</ul></div>
          <div><h4>Suggested outline</h4><ul className="sp-po-why">{s.heading_outline.map((h) => <li key={h}>{h}</li>)}</ul></div>
        </div>
      )}
      {s.missing_sections.length > 0 && <><h4>Sections to consider</h4>
        <ul className="sp-po-why">{s.missing_sections.map((x) => <li key={x.heading}><strong>{x.heading}</strong> — {x.why}</li>)}</ul></>}
      {s.faq.length > 0 && <><h4>FAQ ideas</h4>
        <ul className="sp-po-why">{s.faq.map((x) => <li key={x.question}><strong>{x.question}</strong> {x.answer_outline}</li>)}</ul></>}
      {s.internal_links.length > 0 && <><h4>Internal links to add</h4>
        <ul className="sp-po-why">{s.internal_links.map((x) => <li key={x.url}>{x.anchor_text} → {x.url}{x.reason ? ` (${x.reason})` : ""}</li>)}</ul></>}
      {s.schema && <p className="sp-si-muted">Structured data: {s.schema.type} — {s.schema.notes}</p>}
      {s.limitations && <p className="sp-si-muted">Limitations: {s.limitations}</p>}
      <p className="sp-si-muted">Generated {fmtDate(s.generated_at)}. Text in [brackets] needs facts only you can supply.</p>
    </div>
  );
}
