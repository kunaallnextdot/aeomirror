/* Separate chart components (Phase 4), built on recharts. Each takes plain data
   from GET /api/dashboard and renders inside a fixed-height box (no layout shift). */
import React from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { scoreColor } from "./ui.jsx";

const AXIS = { fill: "var(--txt-dim)", fontSize: 10 };
const TOOLTIP = {
  contentStyle: { background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--txt-mid)" }, itemStyle: { color: "var(--txt)" },
};

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

export function ScoreHistoryChart({ trend = [] }) {
  // Keep the raw timestamp so the axis can show a date across multiple days, or a
  // time-of-day when every scan landed on the same calendar day (avoids "Jul 27" ×N).
  const data = trend.map((t, i) => ({ i, ts: t.scan_time, score: t.overall_score }));
  if (!data.length) return <ChartEmpty label="No score history yet" />;
  const singleDay = new Set(data.map((d) => sameCalendarDay(d.ts))).size <= 1;
  const fmtTick = (i) => (singleDay ? shortTime(data[i]?.ts) : shortDate(data[i]?.ts));
  const fmtLabel = (i) => {
    const iso = data[i]?.ts;
    return singleDay ? `${shortDate(iso)} ${shortTime(iso)}` : shortDate(iso);
  };
  // Small series read better as near-straight segments with visible points than as a
  // heavily-smoothed spline that overshoots between sparse samples.
  const curve = data.length < 10 ? "linear" : "monotone";
  return (
    <div className="d-chart">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
          <defs>
            <linearGradient id="dg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="i" type="category" tickFormatter={fmtTick} tick={AXIS} axisLine={false} tickLine={false} />
          <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
          <Tooltip {...TOOLTIP} labelFormatter={fmtLabel} />
          <Area type={curve} dataKey="score" stroke="var(--accent)" strokeWidth={2} fill="url(#dg)"
                dot={{ r: 3, fill: "var(--accent)", strokeWidth: 0 }}
                activeDot={{ r: 5, fill: "var(--accent)", stroke: "var(--panel)", strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ScoreDistributionChart({ distribution = [] }) {
  if (!distribution.some((d) => d.count > 0)) return <ChartEmpty label="No distribution yet" />;
  const mid = { "0-20": 10, "20-40": 30, "40-60": 50, "60-80": 70, "80-100": 90 };
  return (
    <div className="d-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={distribution} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="bucket" tick={AXIS} axisLine={false} tickLine={false} />
          <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
          <Tooltip {...TOOLTIP} cursor={{ fill: "var(--panel-2)" }} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {distribution.map((d) => <Cell key={d.bucket} fill={scoreColor(mid[d.bucket])} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TopIssuesChart({ categories = [] }) {
  const data = categories.map((c) => ({ label: c.label, count: c.fail_count }));
  if (!data.length) return <ChartEmpty label="No failing signals — nice!" />;
  // No numeric X axis (its decimal ticks were meaningless for small integer counts);
  // the count rides at the end of each bar as a label instead. Extra right margin
  // leaves room for the label without clipping.
  return (
    <div className="d-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 26, bottom: 0, left: 8 }}>
          <XAxis type="number" hide allowDecimals={false} domain={[0, "dataMax"]} />
          <YAxis type="category" dataKey="label" width={110} tick={AXIS} axisLine={false} tickLine={false} />
          <Tooltip {...TOOLTIP} cursor={{ fill: "var(--panel-2)" }} />
          <Bar dataKey="count" fill="var(--bad)" radius={[0, 4, 4, 0]} barSize={14}>
            <LabelList dataKey="count" position="right" fill="var(--txt-mid)" fontSize={11}
                       fontFamily="'IBM Plex Mono', monospace" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/* Most common failures — ranked recurring issue messages. A list (not a bar chart)
   because the issue text is long; the count reads as a right-aligned pill, which is
   clearer than a bar when there are only a handful of near-equal counts. */
export function CommonFailuresChart({ failures = [] }) {
  if (!failures.length) return <ChartEmpty label="No recurring failures 🎉" />;
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

function ChartEmpty({ label }) {
  return <div className="d-chart" style={{ display: "grid", placeItems: "center", color: "var(--txt-dim)", fontSize: 12.5 }}>{label}</div>;
}
