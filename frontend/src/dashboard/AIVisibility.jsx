/* Phase 3 — AI Visibility dashboard (Aurora). A first-class, negative-first framing over
   the existing Answer Tracking run: coverage overview, provider breakdown, where AI doesn't
   mention you, competitor landscape, grounded content gaps, and the AEO Opportunity Finder.
   All data comes from GET /prompt-runs/:id/visibility (gated server-side); this component
   only renders it — it never computes findings or calls an LLM. Additive: renders nothing
   until the run's analysis is ready, so it never breaks the page. */
import React, { useEffect, useState } from "react";
import {
  Eye, EyeOff, Quote, Trophy, Lightbulb, Lock, AlertTriangle,
  TrendingUp, TrendingDown, Minus, MapPin,
} from "lucide-react";
import { getPromptRunVisibility, getReportAIVisibility, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import { Cell, Button } from "./aurora.jsx";

const pct = (v) => (v == null ? "—" : `${v}%`);
const PRI_COLOR = {
  Critical: "var(--au-peach-d)", High: "var(--au-peach-d)",
  Medium: "var(--au-lemon-d)", Low: "var(--au-muted)",
};

// Answer Tracking page: the run's AI Visibility (mounted only once a run exists).
export function AIVisibilityPanel({ runId }) {
  const { openUpgrade } = useUpgrade();
  const [v, setV] = useState(null);

  useEffect(() => {
    if (!runId) { setV(null); return undefined; }
    let alive = true;
    setV(null);
    getPromptRunVisibility(runId)
      .then((d) => { if (alive) setV(d); })
      .catch((e) => { if (alive && !(e instanceof ScanError)) setV(null); });
    return () => { alive = false; };
  }, [runId]);

  if (!v || !v.visibility) return null;
  return <AIVisibilityView data={v} openUpgrade={openUpgrade} />;
}

// Report integration (Feature 7): the site's AI Visibility from the monitor's latest AT
// run, or a clean "No data yet" state — grounded, never fabricated.
export function ReportAIVisibility({ scanId }) {
  const { openUpgrade } = useUpgrade();
  const [state, setState] = useState(null);   // null = loading

  useEffect(() => {
    if (!scanId) { setState(null); return undefined; }
    let alive = true;
    setState(null);
    getReportAIVisibility(scanId)
      .then((d) => { if (alive) setState(d); })
      .catch(() => { if (alive) setState({ available: false, reason: "error" }); });
    return () => { alive = false; };
  }, [scanId]);

  if (!state) return null;                     // loading — avoid a flash
  if (!state.available || !state.visibility) return <AIVisibilityEmpty reason={state.reason} />;
  return <AIVisibilityView data={state} openUpgrade={openUpgrade} />;
}

export function AIVisibilityEmpty({ reason }) {
  const msg = reason === "no_run"
    ? "No Answer Tracking runs yet for this site. Run Answer Tracking to unlock AI Visibility."
    : "Set up a monitor and run Answer Tracking for this site to see how AI assistants mention it.";
  return (
    <Cell solid className="au-av">
      <div className="au-panel-h">AI Visibility <span className="au-sub">how AI assistants see your brand</span></div>
      <div className="au-av-empty">
        <Eye size={20} />
        <div className="au-av-empty-t">No data yet</div>
        <div className="au-av-empty-s">{msg}</div>
      </div>
    </Cell>
  );
}

export function AIVisibilityView({ data: v, openUpgrade = () => {} }) {
  const vis = v.visibility;
  const unlocked = !!v.unlocked;
  const ans = v.answerability || {};
  const notVisible = ans.not_visible || [];
  const gaps = v.content_gaps || [];
  const ci = v.competitor_intelligence || {};
  const opps = v.opportunities || {};
  const trend = v.trend;

  return (
    <Cell solid className="au-av">
      <div className="au-panel-h">AI Visibility <span className="au-sub">how AI assistants see your brand</span></div>

      {/* Overall answerability verdict (deterministic; evidence-backed) */}
      {ans.verdict && (
        <div className={`au-av-verdict v-${ans.verdict}`}>
          <span className="au-av-verdict-label">{ans.verdict_label}</span>
          <span className="au-av-verdict-ev">{ans.evidence}</span>
        </div>
      )}

      {/* 1. Negative-first overview */}
      <div className="au-av-lede">
        You appear in <b>{pct(vis.mention_rate)}</b> of relevant AI answers.
        {vis.missed_rate != null && <> You're missing from <b className="au-av-miss">{pct(vis.missed_rate)}</b>.</>}
        {trend && trend.direction && trend.direction !== "insufficient_history" && (
          <TrendBadge trend={trend} />
        )}
      </div>
      {trend?.config_changed && (
        <div className="au-av-note"><AlertTriangle size={12} /> Trend affected by a model/search configuration change — recent runs may not be directly comparable.</div>
      )}

      <div className="au-av-metrics">
        <Metric label="Mention rate" value={pct(vis.mention_rate)} sub={`${vis.analyzed_count} answers analyzed`} />
        <Metric label="Brand citations" value={vis.citation_count} sub="answers citing your URLs" />
        {vis.average_position != null && <Metric label="Avg. position" value={`#${vis.average_position}`} />}
        <Metric label="Competitor mentions" value={vis.competitor_mentions} sub="rival recommendations" />
      </div>
      {vis.excluded_extraction_failures > 0 && (
        <div className="au-av-note"><AlertTriangle size={12} /> {vis.excluded_extraction_failures} sample(s) excluded — extraction failed (not counted as “not mentioned”).</div>
      )}

      {/* 2. Provider breakdown */}
      {(vis.per_provider || []).length > 0 && (
        <div className="au-av-block">
          <div className="au-av-h">Provider breakdown</div>
          {vis.per_provider.map((p) => (
            <div key={p.provider} className="au-av-bar">
              <span className="au-av-bar-l">{p.provider}</span>
              <span className="au-av-bar-track"><span className="au-av-bar-fill" style={{ width: `${p.mention_rate || 0}%` }} /></span>
              <span className="au-av-bar-v">{pct(p.mention_rate)} <span className="au-dim">· {p.samples}</span></span>
            </div>
          ))}
          {vis.locked_provider_count > 0 && (
            <LockRow label={`${vis.locked_provider_count} more provider(s) in the full breakdown`}
                     onClick={() => openUpgrade("ai_visibility")} />
          )}
        </div>
      )}

      {/* 3. Where AI doesn't mention you (negative-first) */}
      {notVisible.length > 0 && (
        <div className="au-av-block">
          <div className="au-av-h"><EyeOff size={13} /> Where AI doesn't mention you</div>
          {notVisible.map((p) => (
            <div key={p.prompt_id} className="au-av-gap">
              <div className="au-av-gap-q">{p.prompt}</div>
              <div className="au-av-gap-meta">
                0% across {p.samples} sample{p.samples === 1 ? "" : "s"}
                {(p.recommended_entities || []).length > 0 && <> · AI recommended: {p.recommended_entities.slice(0, 3).join(", ")}</>}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 4. Competitor landscape */}
      {(ci.brand || (ci.competitors || []).length > 0) && (
        <div className="au-av-block">
          <div className="au-av-h"><Trophy size={13} /> AI competitor landscape</div>
          <div className="au-av-tablewrap">
            <table className="au-av-table">
              <thead><tr><th>Rank</th><th>Brand</th><th>Appears</th><th>Avg pos</th><th>Trend</th></tr></thead>
              <tbody>
                {ci.brand && (
                  <tr className="au-av-you">
                    <td>#{ci.brand.rank ?? "—"}</td><td>{ci.brand.name} <span className="au-av-youtag">you</span></td>
                    <td>{pct(ci.brand.appearance_rate)}</td><td>{ci.brand.average_position != null ? `#${ci.brand.average_position}` : "—"}</td>
                    <td><RankDelta d={ci.brand.rank_delta} /></td>
                  </tr>
                )}
                {(ci.competitors || []).map((c) => (
                  <tr key={c.name}>
                    <td>#{c.rank ?? "—"}</td>
                    <td>{c.name}{c.tracked && <span className="au-av-tracked" title="A competitor you configured">tracked</span>}</td>
                    <td>{pct(c.appearance_rate)}</td>
                    <td>{c.average_position != null ? `#${c.average_position}` : "—"}</td>
                    <td><RankDelta d={c.rank_delta} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {ci.locked_competitor_count > 0 && (
            <LockRow label={`${ci.locked_competitor_count} more competitor(s) in the full leaderboard`}
                     onClick={() => openUpgrade("competitor_intelligence")} />
          )}
          {/* Head-to-head — evidence-backed, never "competitor won" */}
          {(ci.head_to_head || []).length > 0 && (
            <div className="au-av-h2h">
              <div className="au-av-h2h-h">Head-to-head</div>
              {ci.head_to_head.map((h) => (
                <div key={h.competitor} className="au-av-h2h-row">
                  <span>{h.statement}</span>
                </div>
              ))}
            </div>
          )}
          {ci.head_to_head_locked && (
            <LockRow label="Head-to-head — see every answer a competitor holds and you don't"
                     onClick={() => openUpgrade("competitor_intelligence")} />
          )}
        </div>
      )}

      {/* 5. Content gaps (grounded only) */}
      {gaps.length > 0 && (
        <div className="au-av-block">
          <div className="au-av-h"><Quote size={13} /> Content gaps</div>
          {gaps.map((g) => (
            <div key={g.prompt_id} className="au-av-cg">
              <div className="au-av-cg-q">{g.prompt}</div>
              {g.insufficient_evidence ? (
                <div className="au-av-cg-none">Insufficient evidence to diagnose this gap.</div>
              ) : (
                <>
                  {g.why && <div className="au-av-cg-why"><b>Why you're missing:</b> {g.why}</div>}
                  {(g.actions || []).length > 0 && (
                    <ul className="au-av-cg-actions">{g.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
                  )}
                </>
              )}
            </div>
          ))}
          {v.locked_content_gap_count > 0 && (
            <LockRow label={`${v.locked_content_gap_count} more content gap(s) with grounded actions`}
                     onClick={() => openUpgrade("ai_visibility")} />
          )}
        </div>
      )}

      {/* 6. AEO Opportunity Finder. Several opportunities can share one root cause
          (e.g. a missing-schema score-loss card, several missing-type cards, and an
          entity-completeness card are all driven by the same missing Organization
          schema) — `opps.groups` (server-computed, additive) says which ones do. Every
          item is still real data the server sent; grouping only decides which one
          renders as the primary card, with the rest tucked behind a "show evidence"
          toggle instead of repeating the same root cause as separate cards. */}
      {(opps.items || []).length > 0 && (
        <div className="au-av-block">
          <div className="au-av-h"><Lightbulb size={13} /> AEO Opportunity Finder <span className="au-sub">ranked by Opportunity Impact</span></div>
          {(() => {
            const groupsById = Object.fromEntries((opps.groups || []).map((g) => [g.root_cause_id, g]));
            const itemsById = Object.fromEntries(opps.items.map((o) => [o.id, o]));
            return opps.items
              .filter((o) => o.is_primary !== false)   // non-primary members render nested, not standalone
              .map((o) => {
                const group = o.root_cause_id ? groupsById[o.root_cause_id] : null;
                const supporting = group
                  ? group.opportunity_ids.filter((id) => id !== o.id)
                      .map((id) => itemsById[id]).filter(Boolean)
                  : [];
                return <OpportunityCard key={o.id} o={o} group={group} supporting={supporting} />;
              });
          })()}
          {opps.locked_count > 0 && (
            <LockRow label={`${opps.locked_count} more opportunit${opps.locked_count === 1 ? "y" : "ies"} in the full finder`}
                     onClick={() => openUpgrade("opportunity_finder")} />
          )}
        </div>
      )}

      {/* 7. Unlock */}
      {!unlocked && (
        <div className="au-av-unlock">
          <div className="au-av-unlock-t"><Lock size={14} /> Unlock complete AI Visibility</div>
          <div className="au-av-unlock-s">Full provider &amp; prompt analysis, every content gap, the complete competitor leaderboard with head-to-head, and the full Opportunity Finder.</div>
          <Button variant="accent" onClick={() => openUpgrade("ai_visibility")}>Upgrade to Pro</Button>
        </div>
      )}
    </Cell>
  );
}

function Metric({ label, value, sub }) {
  return (
    <div className="au-av-metric">
      <div className="au-av-metric-v">{value}</div>
      <div className="au-av-metric-l">{label}</div>
      {sub && <div className="au-av-metric-s">{sub}</div>}
    </div>
  );
}

/* One opportunity card. When it heads a root-cause group, `supporting` holds the
   OTHER real opportunities that share that root cause — collapsed by default behind a
   toggle so the negative-first list doesn't repeat one deficiency as several cards,
   without ever hiding or discarding the underlying evidence. */
function OpportunityCard({ o, group, supporting }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="au-av-opp">
      <span className="au-av-opp-pri" style={{ background: PRI_COLOR[o.priority] || "var(--au-muted)" }}>{o.priority}</span>
      <div className="au-av-opp-main">
        {group && <div className="au-av-opp-root">Root cause: {group.label}</div>}
        <div className="au-av-opp-t">{o.title}</div>
        {o.description && <div className="au-av-opp-d">{o.description}</div>}
        {o.recommended_action && <div className="au-av-opp-a"><b>Action:</b> {o.recommended_action}</div>}
        {(o.affected_prompts || []).length > 0 && (
          <div className="au-av-opp-refs">{o.affected_prompts.slice(0, 2).map((p) => p.prompt).filter(Boolean).join(" · ")}</div>
        )}
        {(o.affected_urls || []).length > 0 && (
          <div className="au-av-opp-refs"><MapPin size={11} /> {o.affected_urls.join(", ")}</div>
        )}
        {supporting.length > 0 && (
          <>
            <button type="button" className="au-av-opp-toggle" onClick={() => setExpanded((e) => !e)}>
              {expanded ? "Hide" : "Show"} {supporting.length} supporting evidence item{supporting.length === 1 ? "" : "s"}
            </button>
            {expanded && (
              <ul className="au-av-opp-evidence">
                {supporting.map((s) => (
                  <li key={s.id}>{s.title}{s.description ? ` — ${s.description}` : ""}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
      <span className="au-av-opp-impact" title="Opportunity Impact (0–100)">{o.impact ?? "—"}</span>
    </div>
  );
}

function LockRow({ label, onClick }) {
  return (
    <button type="button" className="au-av-lockrow" onClick={onClick}>
      <Lock size={12} /> <span>{label}</span> <span className="au-av-lockrow-cta">Unlock</span>
    </button>
  );
}

function TrendBadge({ trend }) {
  const map = {
    improving: { icon: <TrendingUp size={12} />, cls: "up", label: "Improving" },
    declining: { icon: <TrendingDown size={12} />, cls: "down", label: "Declining" },
    stable: { icon: <Minus size={12} />, cls: "flat", label: "Stable" },
    unknown: { icon: <Minus size={12} />, cls: "flat", label: "—" },
  };
  const t = map[trend.direction] || map.unknown;
  return <span className={`au-av-trend ${t.cls}`}>{t.icon} {t.label}{trend.delta != null ? ` ${trend.delta > 0 ? "+" : ""}${trend.delta}` : ""}</span>;
}

function RankDelta({ d }) {
  if (d == null) return <span className="au-dim">—</span>;
  if (d > 0) return <span className="au-av-delta up"><TrendingUp size={11} /> {d}</span>;
  if (d < 0) return <span className="au-av-delta down"><TrendingDown size={11} /> {Math.abs(d)}</span>;
  return <span className="au-dim"><Minus size={11} /></span>;
}
