/* Separate chart components (Phase 4), built on recharts. Each takes plain data
   from GET /api/dashboard and renders inside a fixed-height box (no layout shift).

   Aurora migration: each chart accepts an optional `aurora` prop. Default (false) is the
   UNCHANGED dark theme (still used by un-migrated MonitorDetail/Compare); `aurora` swaps to
   the light Aurora palette. Data/logic unchanged — theming only. */
import React from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { scoreColor } from "./ui.jsx";

const auScoreColor = (v) => (v >= 75 ? "var(--au-mint-d)" : v >= 45 ? "var(--au-lemon-d)" : "var(--au-peach-d)");

// Theme bundle keyed by the `aurora` flag. Dark values are the pre-migration constants.
function theme(aurora) {
  if (aurora) return {
    axis: { fill: "var(--au-muted)", fontSize: 10, fontFamily: "'DM Mono', monospace" },
    tooltip: {
      contentStyle: { background: "var(--au-solid)", border: "1px solid var(--au-line)", borderRadius: 12,
                      fontSize: 12, boxShadow: "var(--au-sh-s)", color: "var(--au-ink)" },
      labelStyle: { color: "var(--au-muted)" }, itemStyle: { color: "var(--au-ink)" },
    },
    grid: "var(--au-line)", accent: "var(--au-primary)", bad: "var(--au-peach-d)",
    cursor: "rgba(20,30,51,.05)", panel: "var(--au-solid)", label: "var(--au-muted)",
    score: auScoreColor, klass: true,
  };
  return {
    axis: { fill: "var(--txt-dim)", fontSize: 10 },
    tooltip: {
      contentStyle: { background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 },
      labelStyle: { color: "var(--txt-mid)" }, itemStyle: { color: "var(--txt)" },
    },
    grid: "var(--line)", accent: "var(--accent)", bad: "var(--bad)",
    cursor: "var(--panel-2)", panel: "var(--panel)", label: "var(--txt-mid)",
    score: scoreColor, klass: false,
  };
}

function shortDate(iso) {
  try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
  catch { return ""; }
}
function shortTime(iso) {
  try { return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); }
  catch { return ""; }
}
function sameCalendarDay(iso) {
  try { const d = new Date(iso); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }
  catch { return iso; }
}

export function ScoreHistoryChart({ trend = [], aurora = false }) {
  const t = theme(aurora);
  // Keep the raw timestamp so the axis can show a date across multiple days, or a
  // time-of-day when every scan landed on the same calendar day (avoids "Jul 27" ×N).
  const data = trend.map((tp, i) => ({ i, ts: tp.scan_time, score: tp.overall_score }));
  if (!data.length) return <ChartEmpty label="No score history yet" aurora={aurora} />;
  const singleDay = new Set(data.map((d) => sameCalendarDay(d.ts))).size <= 1;
  const fmtTick = (i) => (singleDay ? shortTime(data[i]?.ts) : shortDate(data[i]?.ts));
  const fmtLabel = (i) => {
    const iso = data[i]?.ts;
    return singleDay ? `${shortDate(iso)} ${shortTime(iso)}` : shortDate(iso);
  };
  const curve = data.length < 10 ? "linear" : "monotone";
  return (
    <div className={t.klass ? "au-chart" : "d-chart"}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
          <defs>
            <linearGradient id={aurora ? "dg-au" : "dg"} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={t.accent} stopOpacity={0.35} />
              <stop offset="100%" stopColor={t.accent} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={t.grid} vertical={false} />
          <XAxis dataKey="i" type="category" tickFormatter={fmtTick} tick={t.axis} axisLine={false} tickLine={false} />
          <YAxis domain={[0, 100]} tick={t.axis} axisLine={false} tickLine={false} />
          <Tooltip {...t.tooltip} labelFormatter={fmtLabel} />
          <Area type={curve} dataKey="score" stroke={t.accent} strokeWidth={2} fill={`url(#${aurora ? "dg-au" : "dg"})`}
                dot={{ r: 3, fill: t.accent, strokeWidth: 0 }}
                activeDot={{ r: 5, fill: t.accent, stroke: t.panel, strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ScoreDistributionChart({ distribution = [], aurora = false }) {
  const t = theme(aurora);
  if (!distribution.some((d) => d.count > 0)) return <ChartEmpty label="No distribution yet" aurora={aurora} />;
  const mid = { "0-20": 10, "20-40": 30, "40-60": 50, "60-80": 70, "80-100": 90 };
  return (
    <div className={t.klass ? "au-chart" : "d-chart"}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={distribution} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid stroke={t.grid} vertical={false} />
          <XAxis dataKey="bucket" tick={t.axis} axisLine={false} tickLine={false} />
          <YAxis allowDecimals={false} tick={t.axis} axisLine={false} tickLine={false} />
          <Tooltip {...t.tooltip} cursor={{ fill: t.cursor }} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {distribution.map((d) => <Cell key={d.bucket} fill={t.score(mid[d.bucket])} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TopIssuesChart({ categories = [], aurora = false }) {
  const t = theme(aurora);
  const data = categories.map((c) => ({ label: c.label, count: c.fail_count }));
  if (!data.length) return <ChartEmpty label="No failing signals — nice!" aurora={aurora} />;
  return (
    <div className={t.klass ? "au-chart" : "d-chart"}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 26, bottom: 0, left: 8 }}>
          <XAxis type="number" hide allowDecimals={false} domain={[0, "dataMax"]} />
          <YAxis type="category" dataKey="label" width={110} tick={t.axis} axisLine={false} tickLine={false} />
          <Tooltip {...t.tooltip} cursor={{ fill: t.cursor }} />
          <Bar dataKey="count" fill={t.bad} radius={[0, 4, 4, 0]} barSize={14}>
            <LabelList dataKey="count" position="right" fill={t.label} fontSize={11}
                       fontFamily={aurora ? "'DM Mono', monospace" : "'IBM Plex Mono', monospace"} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/* Most common failures — ranked recurring issue messages (a list, not a bar chart). */
export function CommonFailuresChart({ failures = [], aurora = false }) {
  if (!failures.length) return <ChartEmpty label="No recurring failures 🎉" aurora={aurora} />;
  if (aurora) {
    return (
      <div className="au-fails">
        {failures.map((f, i) => (
          <div key={i} className="au-fail-row">
            <span className="au-fail-rank">{i + 1}</span>
            <span className="au-fail-txt">{f.issue}</span>
            <span className="au-fail-pill">{f.count}×</span>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="d-fails">
      {failures.map((f, i) => (
        <div key={i} className="d-fail-row">
          <span className="d-fail-rank d-mono">{i + 1}</span>
          <span className="d-fail-txt">{f.issue}</span>
          <span className="d-fail-pill d-mono">{f.count}×</span>
        </div>
      ))}
    </div>
  );
}

function ChartEmpty({ label, aurora = false }) {
  if (aurora) return <div className="au-chart-empty">{label}</div>;
  return <div className="d-chart" style={{ display: "grid", placeItems: "center", color: "var(--txt-dim)", fontSize: 12.5 }}>{label}</div>;
}
